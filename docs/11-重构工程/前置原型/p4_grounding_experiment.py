"""阶段 P4：基于允许源区间判真集的摘抄落锚实验。"""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from word2jats.llm.client import LLMClient


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
OBJECT = "\ufffc"


@dataclass(frozen=True, order=True)
class TextRange:
    node_id: str
    start: int
    end: int


@dataclass(frozen=True)
class Match:
    source: TextRange
    method: str


@dataclass
class TruthField:
    name: str
    description: str
    scopes: list[TextRange]
    allowed: list[list[TextRange]]
    expected_parts: int = 1
    ordered: bool = False
    match_mode: str = "exact-range"  # exact-range | anchor-start
    constraint: str | None = None


@dataclass
class Task:
    task_id: str
    sample: str
    case: str
    nodes: dict[str, str]
    fields: list[TruthField]


def paragraph_text(paragraph) -> str:
    """构造 P4 所需的字符流：文字、Tab、软回车与对象占位均有明确字符位置。"""
    out: list[str] = []
    for node in paragraph.iter():
        if not isinstance(node.tag, str):
            continue
        name = etree.QName(node).localname
        if name == "t":
            out.append(node.text or "")
        elif name == "tab":
            out.append("\t")
        elif name in {"br", "cr"}:
            out.append("\n")
        elif name in {"drawing", "pict", "object", "oMath", "oMathPara"}:
            # 只在最外层对象处记一个占位，避免 object 里的 pict 再记一次。
            if not any(
                isinstance(a.tag, str) and etree.QName(a).localname in
                {"drawing", "pict", "object", "oMath", "oMathPara"}
                for a in node.iterancestors()
            ):
                out.append(OBJECT)
    return "".join(out)


def read_nodes(docx: Path) -> dict[str, str]:
    with ZipFile(docx) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    paragraphs = root.xpath("//w:body//w:p", namespaces=NS)
    return {f"doc/p{i}": paragraph_text(p) for i, p in enumerate(paragraphs)}


def find_exact(nodes: dict[str, str], quote: str, *, node_id: str | None = None) -> list[TextRange]:
    found = []
    for current_id, text in nodes.items():
        if node_id is not None and current_id != node_id:
            continue
        pos = 0
        while True:
            pos = text.find(quote, pos)
            if pos < 0:
                break
            found.append(TextRange(current_id, pos, pos + len(quote)))
            pos += max(1, len(quote))
    return found


def one_range(nodes: dict[str, str], quote: str, node_id: str | None = None) -> TextRange:
    found = find_exact(nodes, quote, node_id=node_id)
    if len(found) != 1:
        raise ValueError(f"期望唯一区间，实得 {len(found)}：{node_id} {quote!r}")
    return found[0]


def whole(nodes: dict[str, str], node_ids: list[str]) -> list[TextRange]:
    return [TextRange(node_id, 0, len(nodes[node_id])) for node_id in node_ids]


def normalize_with_map(text: str) -> tuple[str, list[tuple[int, int]]]:
    translated = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}
    out: list[str] = []
    positions: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            start = i
            while i < len(text) and text[i].isspace():
                i += 1
            out.append(" ")
            positions.append((start, i))
            continue
        out.append(translated.get(char, char))
        positions.append((i, i + 1))
        i += 1
    return "".join(out), positions


def normalized(text: str) -> str:
    return normalize_with_map(text)[0]


def candidate_matches(
    quote: str,
    nodes: dict[str, str],
    scopes: list[TextRange],
    *,
    block_hint: str | None = None,
    after: tuple[int, int] | None = None,
    allow_objects: bool = False,
) -> list[Match]:
    if not isinstance(quote, str) or not quote:
        return []
    node_order = {node_id: index for index, node_id in enumerate(nodes)}
    exact: list[Match] = []
    for scope in scopes:
        if block_hint and scope.node_id != block_hint:
            continue
        text = nodes[scope.node_id][scope.start:scope.end]
        pos = 0
        while True:
            pos = text.find(quote, pos)
            if pos < 0:
                break
            source = TextRange(scope.node_id, scope.start + pos, scope.start + pos + len(quote))
            fragment = nodes[source.node_id][source.start:source.end]
            order_pos = (node_order[source.node_id], source.start)
            if (allow_objects or OBJECT not in fragment) and (after is None or order_pos > after):
                exact.append(Match(source, "exact"))
            pos += max(1, len(quote))
    if exact:
        return exact

    norm_quote = normalized(quote)
    result: list[Match] = []
    for scope in scopes:
        if block_hint and scope.node_id != block_hint:
            continue
        original = nodes[scope.node_id][scope.start:scope.end]
        norm_text, mapping = normalize_with_map(original)
        pos = 0
        while True:
            pos = norm_text.find(norm_quote, pos)
            if pos < 0:
                break
            end_pos = pos + len(norm_quote)
            if end_pos == pos:
                break
            start_original = scope.start + mapping[pos][0]
            end_original = scope.start + mapping[end_pos - 1][1]
            source = TextRange(scope.node_id, start_original, end_original)
            fragment = nodes[source.node_id][source.start:source.end]
            order_pos = (node_order[source.node_id], source.start)
            if (allow_objects or OBJECT not in fragment) and (after is None or order_pos > after):
                result.append(Match(source, "normalized"))
            pos += max(1, len(norm_quote))
    return result


def overlaps(a: TextRange, b: TextRange) -> bool:
    return a.node_id == b.node_id and a.start < b.end and b.start < a.end


def field_solutions(
    parts: list[dict], field: TruthField, nodes: dict[str, str]
) -> tuple[list[list[Match]], str]:
    """先收集所有候选，再做一次唯一联合分配，不按字段表顺序贪心推进。"""
    if len(parts) != field.expected_parts:
        return [], "wrong-part-count"
    candidate_sets = []
    for part in parts:
        if not isinstance(part, dict):
            return [], "malformed-part"
        candidates = candidate_matches(
            part.get("quote"), nodes, field.scopes,
            block_hint=part.get("node_hint"),
            allow_objects=bool(part.get("allow_objects", False)),
        )
        if field.constraint == "not-url":
            filtered = []
            for match in candidates:
                text = nodes[match.source.node_id]
                left = max(text.rfind(" ", 0, match.source.start), text.rfind("\t", 0, match.source.start)) + 1
                right_space = text.find(" ", match.source.end)
                right = len(text) if right_space < 0 else right_space
                token = text[left:right].lower()
                if "://" not in token and "doi.org/" not in token and "www." not in token:
                    filtered.append(match)
            candidates = filtered
        candidate_sets.append(candidates)
    if any(not candidates for candidates in candidate_sets):
        return [], "no-candidate"
    solutions = []
    node_order = {node_id: index for index, node_id in enumerate(nodes)}
    for combo in itertools.product(*candidate_sets):
        sources = [match.source for match in combo]
        if any(overlaps(a, b) for i, a in enumerate(sources) for b in sources[i + 1:]):
            continue
        if field.ordered:
            keys = [(node_order[source.node_id], source.start, source.end) for source in sources]
            if keys != sorted(keys):
                continue
        solutions.append(list(combo))
    unique = {
        tuple((m.source.node_id, m.source.start, m.source.end) for m in solution): solution
        for solution in solutions
    }
    return list(unique.values()), "ok" if unique else "inconsistent"


def task_prompt(task: Task) -> tuple[str, str]:
    system = (
        "You are a source-span annotator. Never rewrite, correct, decode entities, or add text. "
        "For every requested field, copy one or more contiguous quotations exactly as displayed, "
        "and repeat the node id in node_hint. Return one JSON object only, shaped as "
        '{"fields":{"field":[{"quote":"verbatim","node_hint":"doc/p1"}]}}. '
        "Use the requested number of parts. A field quote must contain the complete requested "
        "field, not merely its most distinctive substring. Exclude surrounding delimiter "
        "punctuation such as a trailing period unless the field description explicitly asks for it. "
        "If uncertain, return an empty list; do not guess."
    )
    lines = [f"TASK_ROUTE=P4-{task.task_id}", "SOURCE:"]
    for node_id, text in task.nodes.items():
        visible = text.replace(OBJECT, "⟦OBJECT⟧")
        lines.append(f"[{node_id}] {visible}")
    lines += ["", "FIELDS:"]
    for field in task.fields:
        lines.append(f"- {field.name}: {field.description}; exactly {field.expected_parts} quote part(s)")
    return system, "\n".join(lines)


def title_task(sample: str, nodes: dict[str, str], gold_xml: Path) -> Task:
    gold = etree.parse(str(gold_xml))
    title = "".join(gold.xpath('(//*[local-name()="article-title"])[1]')[0].itertext()).strip()
    if sample == "X02":
        pieces = [
            ("doc/p1", "Investigating the Role of diluent in Nickel (II) Ion Extraction "),
            ("doc/p2", "Using D2EHPA"),
        ]
        expected = [one_range(nodes, quote, node_id) for node_id, quote in pieces]
        task_nodes = {node_id: nodes[node_id] for node_id, _ in pieces}
    else:
        expected = [one_range(nodes, title)]
        task_nodes = {expected[0].node_id: nodes[expected[0].node_id]}
    field = TruthField(
        name="article_title", description=(
            "the complete article title, including every leading title phrase and its punctuation, "
            "excluding only a separate category label; if the supplied node contains only the title, copy it all"
        ),
        scopes=whole(nodes, list(task_nodes)), allowed=[expected],
        expected_parts=len(expected), ordered=True,
    )
    return Task(f"{sample}-title", sample, "title", task_nodes, [field])


def field(
    nodes, name, description, quote, node_id, *, scopes=None, ordered=False,
    occurrence: int | None = None, match_mode="exact-range", constraint=None,
):
    found = find_exact(nodes, quote, node_id=node_id)
    if occurrence is None:
        if len(found) != 1:
            raise ValueError(f"期望唯一区间，实得 {len(found)}：{node_id} {quote!r}")
        expected = found[0]
    else:
        expected = found[occurrence]
    return TruthField(
        name=name, description=description,
        scopes=scopes or whole(nodes, [node_id]), allowed=[[expected]], ordered=ordered,
        match_mode=match_mode, constraint=constraint,
    )


def build_tasks(root: Path) -> list[Task]:
    all_nodes = {
        sample.name: read_nodes(sample / "初始文件.docx")
        for sample in sorted(p for p in (root / "样例数据").iterdir() if p.is_dir())
    }
    tasks = [
        title_task(sample, nodes, root / "样例数据" / sample / "结构参考.xml")
        for sample, nodes in all_nodes.items()
    ]

    nodes = all_nodes["01"]
    p10, p11 = "doc/p10", "doc/p11"
    tasks.append(Task("01-addresses", "01", "multiple-addresses", {p10: nodes[p10], p11: nodes[p11]}, [
        field(nodes, "address_1", "the entire first numbered address from its leading unit number through country, excluding only the parenthesized postal/phone data",
              nodes[p10].split(" (Postal code:", 1)[0], p10),
        field(nodes, "postal_1", "postal code of the first address, digits only", "100037", p10),
        field(nodes, "phone_1", "telephone number of the first address", "+86(10)88322131", p10),
        field(nodes, "address_2", "the entire second numbered address from its leading unit number through country, excluding only the parenthesized postal data",
              nodes[p11].split(" (Postal code:", 1)[0], p11),
        field(nodes, "postal_2", "postal code of the second address, digits only", "518055", p11),
    ]))

    nodes = all_nodes["03"]
    p19 = "doc/p19"
    tasks.append(Task("03-same-node-dates", "03", "multiple-fields-one-node", {p19: nodes[p19]}, [
        field(nodes, "submitted", "date value following Submitted, including separators", "22\xa0/\xa012\xa0/\xa02025", p19),
        field(nodes, "revised", "date value following Revised, including separators", "24\xa0/\xa002\xa0/\xa02026", p19),
        field(nodes, "accepted", "value following Accepted", "待接收", p19),
    ]))

    nodes = all_nodes["05"]
    p298 = "doc/p298"
    tasks.append(Task("05-bare-doi-ref", "05", "bare-doi-reference", {p298: nodes[p298]}, [
        field(nodes, "ref_head", "an opening quotation long enough to identify the entry start; it must begin at the entry label",
              "[2] Carabello BA", p298, match_mode="anchor-start"),
        field(nodes, "article_title", "reference article title without surrounding punctuation", "Introduction to aortic stenosis", p298),
        field(nodes, "source", "journal title content only, excluding its trailing period delimiter", "Circ Res", p298),
        field(nodes, "year", "publication year", "2013", p298),
        field(nodes, "doi", "bare DOI value without the preceding doi label", "10.1161/circresaha.113.300156", p298),
    ]))

    nodes = all_nodes["X01"]
    ids = [f"doc/p{i}" for i in range(326, 356)]
    tasks.append(Task("X01-pseudo-jats", "X01", "pseudo-jats-reference", {i: nodes[i] for i in ids}, [
        field(nodes, "entry_head", "opening ref tag exactly as visible, including curly quotes", "<ref id=“b1”>", "doc/p326"),
        field(nodes, "surname", "first author surname content without tags", "Yemula", "doc/p331"),
        field(nodes, "article_title", "article-title content exactly as visible; do not decode &apos;",
              "Parkinson&apos;s Disease and the Gut: Symptoms, Nutrition, and Microbiota", "doc/p347"),
        field(nodes, "year", "year content without tags", "2021", "doc/p349"),
    ]))

    nodes = all_nodes["X03"]
    p345 = "doc/p345"
    tasks.append(Task("X03-unnumbered-ref", "X03", "unnumbered-reference", {p345: nodes[p345]}, [
        field(nodes, "authors", "complete author list before the first period", "Austen R. Anderson, Blaine J. Fowers", p345),
        field(nodes, "article_title", "article title", "Lifestyle behaviors, psychological distress, and well-being: A daily diary study", p345),
        field(nodes, "source", "journal title", "Social Science & Medicine", p345),
        field(nodes, "year", "publication year, not digits embedded in a DOI or URL", "2020", p345,
              occurrence=0, constraint="not-url"),
    ]))

    nodes = all_nodes["X04"]
    p222 = "doc/p222"
    tasks.append(Task("X04-unnumbered-ref", "X04", "unnumbered-reference", {p222: nodes[p222]}, [
        field(nodes, "authors", "complete author list before the first period", "Fernandez-Yague MA, Abbah SA, McNamara L, Zeugolis DI, Pandit A, Biggs MJ", p222),
        field(nodes, "article_title", "article title", "Biomimetic approaches in bone tissue engineering: Integrating biological and physicomechanical strategies", p222),
        field(nodes, "year", "publication year", "2015", p222),
    ]))

    # 重复年份跨节点时，节点提示是来源证据的一部分；不提示就应拒绝歧义。
    nodes = all_nodes["X03"]
    repeat_ids = ["doc/p345", "doc/p356", "doc/p367"]
    repeat_scope = whole(nodes, repeat_ids)
    tasks.append(Task("X03-repeated-years", "X03", "repeated-author-year", {i: nodes[i] for i in repeat_ids}, [
        field(nodes, "year_entry_1", "publication year belonging to node doc/p345, not digits in a DOI or URL", "2020", "doc/p345", scopes=repeat_scope, occurrence=0, constraint="not-url"),
        field(nodes, "year_entry_2", "publication year belonging to node doc/p356, not digits in a DOI or URL", "2020", "doc/p356", scopes=repeat_scope, constraint="not-url"),
        field(nodes, "year_entry_3", "publication year belonging to node doc/p367, not digits in a DOI or URL", "2020", "doc/p367", scopes=repeat_scope, constraint="not-url"),
    ]))

    # 14 份文档的参考区没有“两条文献紧贴在同一物理段落”的现成例子。
    # 用 05 的两条真实文献原文构造不加分隔符的变形夹具，专门验证锚点定界。
    source05 = all_nodes["05"]
    derived_id = "derived/05/two-refs-one-node"
    derived_nodes = {derived_id: source05["doc/p297"] + source05["doc/p298"]}
    tasks.append(Task("05-two-refs-one-node", "05", "two-references-one-node-derived", derived_nodes, [
        field(derived_nodes, "head_1", "an opening quotation for the first entry; it must begin at [1]", "[1] Pellikka PA", derived_id, ordered=True, match_mode="anchor-start"),
        field(derived_nodes, "head_2", "an opening quotation for the second entry; it must begin at [2]", "[2] Carabello BA", derived_id, ordered=True, match_mode="anchor-start"),
    ]))
    return tasks


def truth_json(tasks: list[Task]) -> dict:
    return {
        "definition": "返回区间必须属于 allowed 中某个完整方案；字符串能在原文找到不足以判对。",
        "tasks": [{
            "task_id": task.task_id,
            "sample": task.sample,
            "case": task.case,
            "nodes": task.nodes,
            "fields": [{
                "name": f.name,
                "description": f.description,
                "scopes": [asdict(r) for r in f.scopes],
                "allowed": [[asdict(r) for r in solution] for solution in f.allowed],
                "expected_parts": f.expected_parts,
                "ordered": f.ordered,
                "match_mode": f.match_mode,
                "constraint": f.constraint,
            } for f in task.fields],
        } for task in tasks],
    }


def evaluate_task(task: Task, response) -> list[dict]:
    response_fields = response.get("fields", {}) if isinstance(response, dict) else {}
    solution_sets: dict[str, list[list[Match]]] = {}
    early_reason: dict[str, str] = {}
    for field in task.fields:
        solutions, reason = field_solutions(response_fields.get(field.name, []), field, task.nodes)
        solution_sets[field.name] = solutions
        early_reason[field.name] = reason

    # 字段之间也同时分配：一个源字符区间不得无意被两个字段占用。
    active = [field for field in task.fields if solution_sets[field.name]]
    global_solutions = []
    if active:
        for combo in itertools.product(*(solution_sets[field.name] for field in active)):
            flat = [match for solution in combo for match in solution]
            if any(overlaps(a.source, b.source) for i, a in enumerate(flat) for b in flat[i + 1:]):
                continue
            global_solutions.append({field.name: solution for field, solution in zip(active, combo)})

    results = []
    for field in task.fields:
        parts = response_fields.get(field.name, [])
        if not solution_sets[field.name]:
            results.append({
                "task_id": task.task_id, "sample": task.sample, "case": task.case,
                "field": field.name, "status": "fallback", "reason": early_reason[field.name],
                "response": parts,
            })
            continue
        variants = {
            tuple((m.source.node_id, m.source.start, m.source.end) for m in solution): solution
            for global_solution in global_solutions
            if field.name in global_solution
            for solution in [global_solution[field.name]]
        }
        if len(variants) != 1:
            results.append({
                "task_id": task.task_id, "sample": task.sample, "case": task.case,
                "field": field.name, "status": "fallback",
                "reason": "ambiguous-global" if variants else "inconsistent-global",
                "response": parts,
            })
            continue
        assigned = next(iter(variants.values()))
        reason = "ok"
        got = [match.source for match in assigned]
        if field.match_mode == "anchor-start":
            correct = any(
                len(got) == len(allowed)
                and all(
                    actual.node_id == expected.node_id and actual.start == expected.start
                    for actual, expected in zip(got, allowed)
                )
                for allowed in field.allowed
            )
        else:
            correct = any(got == allowed for allowed in field.allowed)
        status = (
            "error" if not correct else
            "normalized" if any(match.method == "normalized" for match in assigned) else
            "exact"
        )
        results.append({
            "task_id": task.task_id, "sample": task.sample, "case": task.case,
            "field": field.name, "status": status, "reason": reason,
            "response": parts,
            "grounded": [{**asdict(m.source), "method": m.method,
                          "text": task.nodes[m.source.node_id][m.source.start:m.source.end]} for m in assigned],
            "allowed": [[asdict(r) for r in solution] for solution in field.allowed],
        })
    return results


def main() -> int:
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    tasks = build_tasks(root)
    truth = truth_json(tasks)
    (here / "p4_truth_set.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    client = LLMClient(provider="dashscope", cache_dir=str(here / "p4_llm_cache"), temperature=0)
    if not client.enabled:
        raise RuntimeError("P4 需要模型实际产生摘抄，但 DashScope 未启用")

    def call(task: Task):
        system, user = task_prompt(task)
        return task.task_id, client.extract_json(system, user, max_tokens=4096)

    with ThreadPoolExecutor(max_workers=min(32, len(tasks))) as executor:
        raw = dict(executor.map(call, tasks))
    results = [row for task in tasks for row in evaluate_task(task, raw.get(task.task_id))]

    # 负向性质：默认不得用一个摘抄穿过对象占位。
    negative_nodes = {"fixture/p0": f"alpha {OBJECT} omega"}
    negative_scope = whole(negative_nodes, ["fixture/p0"])
    negative = candidate_matches("alpha \ufffc omega", negative_nodes, negative_scope)
    negative_allowed = candidate_matches(
        "alpha \ufffc omega", negative_nodes, negative_scope, allow_objects=True
    )

    counts = Counter(row["status"] for row in results)
    total = len(results)
    fallback_rate = counts["fallback"] / total if total else 1.0
    summary = {
        "tasks": len(tasks), "samples": len({task.sample for task in tasks}), "fields": total,
        "exact": counts["exact"], "normalized": counts["normalized"],
        "errors": counts["error"], "fallback": counts["fallback"],
        "fallback_rate": fallback_rate,
        "object_crossing_rejected_by_default": not negative,
        "object_crossing_allowed_when_explicit": bool(negative_allowed),
        "llm_stats": client.stats,
    }
    payload = {"summary": summary, "raw_responses": raw, "results": results}
    (here / "p4_grounding_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# P4 摘抄落锚预实验",
        "",
        "## 判真口径",
        "",
        "判真集先于模型回答建立。每个字段记录允许的“节点+起止字符”区间方案；"
        "错误的定义是“已落锚，但返回区间不属于允许集”，不是“字符串找不到”。"
        "完整标注见 `p4_truth_set.json`。",
        "",
        "## 覆盖面",
        "",
        "- 14 例全部含文章标题字段；X02 标题跨两个源节点。",
        "- 01 多地址、邮编、电话；03 同一块内三个日期字段。",
        "- 05 裸 DOI 著录；X01 伪 JATS 跨多块著录；X03/X04 无编号著录。",
        "- X03 多条文献重复年份，验证块提示消歧；用 05 两条真实文献构造同节点无分隔符变形夹具。",
        "- 对象占位负向性质：默认穿过 U+FFFC 的摘抄被拒绝，只有显式声明才允许。",
        "",
        "## 结果",
        "",
        f"- 任务 {summary['tasks']} 个，覆盖 {summary['samples']} 例，字段 {summary['fields']} 个。",
        f"- 精确落锚 {summary['exact']}，归一落锚 {summary['normalized']}，"
        f"错误落锚 {summary['errors']}，拒绝/兜底 {summary['fallback']}。",
        f"- 兜底率 {summary['fallback_rate']:.2%}；门槛为错误落锚=0、兜底率<10%。",
        f"- 默认跨对象拒绝：{summary['object_crossing_rejected_by_default']}；"
        f"显式允许后可落锚：{summary['object_crossing_allowed_when_explicit']}。",
        "",
        "## 结论",
        "",
    ]
    passed = (
        summary["errors"] == 0 and summary["fallback_rate"] < 0.10
        and summary["object_crossing_rejected_by_default"]
        and summary["object_crossing_allowed_when_explicit"]
    )
    lines.append("通过 P4。" if passed else "**P4 未通过，必须修正摘抄协议或落锚算法后重跑。**")
    if not passed:
        lines += ["", "未通过项："]
        for row in results:
            if row["status"] in {"error", "fallback"}:
                lines.append(f"- `{row['task_id']}/{row['field']}`: {row['status']} / {row['reason']}")
    lines.append("")
    (here / "P4-摘抄落锚预实验.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
