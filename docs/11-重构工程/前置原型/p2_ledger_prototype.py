"""阶段 P2：用 01 的真实地址段落验证字符区间覆盖账原型。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import ZipFile

from lxml import etree


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


@dataclass(frozen=True, order=True)
class TextRange:
    node_id: str
    start: int
    end: int

    def text(self, nodes: dict[str, str]) -> str:
        return nodes[self.node_id][self.start:self.end]


@dataclass(frozen=True)
class Entry:
    source: TextRange
    destination: str
    semantic_instance: str
    reuse_reason: str | None = None
    discard_reason: str | None = None


class CoverageLedger:
    """字符级覆盖账。

    覆盖与复用是两个问题：同一源区间可以有多个去向，但第二个去向必须说明
    复用理由；同一语义实例重复记账则始终是问题。
    """

    def __init__(self, nodes: dict[str, str]):
        self.nodes = nodes
        self.entries: list[Entry] = []

    def consume(
        self,
        source: TextRange,
        destination: str,
        semantic_instance: str,
        *,
        reuse_reason: str | None = None,
    ) -> None:
        self._validate(source)
        self.entries.append(Entry(source, destination, semantic_instance, reuse_reason=reuse_reason))

    def discard(self, source: TextRange, reason: str) -> None:
        self._validate(source)
        self.entries.append(Entry(source, "discard", f"discard:{reason}", discard_reason=reason))

    def audit(self) -> dict:
        uncovered = []
        for node_id, text in self.nodes.items():
            covered = [False] * len(text)
            for entry in self.entries:
                if entry.source.node_id != node_id:
                    continue
                for pos in range(entry.source.start, entry.source.end):
                    covered[pos] = True
            start = None
            for pos, char in enumerate(text + "\0"):
                missing = pos < len(text) and not covered[pos] and not char.isspace()
                if missing and start is None:
                    start = pos
                if not missing and start is not None:
                    uncovered.append(self._range_issue(TextRange(node_id, start, pos)))
                    start = None

        duplicate_instances = []
        seen_instances: dict[str, Entry] = {}
        for entry in self.entries:
            if entry.discard_reason:
                continue
            previous = seen_instances.get(entry.semantic_instance)
            if previous is not None:
                duplicate_instances.append({
                    "semantic_instance": entry.semantic_instance,
                    "first": asdict(previous),
                    "duplicate": asdict(entry),
                })
            else:
                seen_instances[entry.semantic_instance] = entry

        unexplained_reuse = []
        by_range: dict[TextRange, list[Entry]] = {}
        for entry in self.entries:
            if not entry.discard_reason:
                by_range.setdefault(entry.source, []).append(entry)
        for source, entries in by_range.items():
            if len(entries) <= 1:
                continue
            for entry in entries[1:]:
                if not entry.reuse_reason:
                    unexplained_reuse.append({
                        "range": asdict(source),
                        "destination": entry.destination,
                        "semantic_instance": entry.semantic_instance,
                    })

        return {
            "ok": not uncovered and not duplicate_instances and not unexplained_reuse,
            "uncovered": uncovered,
            "duplicate_semantic_instances": duplicate_instances,
            "unexplained_reuse": unexplained_reuse,
            "entries": [asdict(entry) for entry in self.entries],
        }

    def _validate(self, source: TextRange) -> None:
        text = self.nodes.get(source.node_id)
        if text is None:
            raise KeyError(source.node_id)
        if not (0 <= source.start <= source.end <= len(text)):
            raise ValueError(source)

    def _range_issue(self, source: TextRange) -> dict:
        return {**asdict(source), "text": source.text(self.nodes)}


def paragraph_text(paragraph) -> str:
    out: list[str] = []
    for node in paragraph.iter():
        name = etree.QName(node).localname if isinstance(node.tag, str) else ""
        if name == "t":
            out.append(node.text or "")
        elif name == "tab":
            out.append("\t")
        elif name in {"br", "cr"}:
            out.append("\n")
    return "".join(out)


def read_nodes(docx: Path, wanted: set[int]) -> dict[str, str]:
    with ZipFile(docx) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    paragraphs = root.xpath("//w:body/w:p", namespaces=NS)
    return {f"doc/p{index}": paragraph_text(paragraphs[index]) for index in sorted(wanted)}


def find_range(nodes: dict[str, str], node_id: str, quote: str) -> TextRange:
    text = nodes[node_id]
    start = text.index(quote)
    if text.find(quote, start + 1) >= 0:
        raise ValueError(f"摘抄不唯一：{node_id} {quote!r}")
    return TextRange(node_id, start, start + len(quote))


def complement(ledger: CoverageLedger, node_id: str, used: list[TextRange]) -> None:
    """将字段之间的标点和提示词作为 ref_notation 显式弃置。"""
    text = ledger.nodes[node_id]
    mask = [False] * len(text)
    for source in used:
        for pos in range(source.start, source.end):
            mask[pos] = True
    start = None
    for pos in range(len(text) + 1):
        gap = pos < len(text) and not mask[pos]
        if gap and start is None:
            start = pos
        if not gap and start is not None:
            ledger.discard(TextRange(node_id, start, pos), "address_notation")
            start = None


def build_good(nodes: dict[str, str]) -> CoverageLedger:
    ledger = CoverageLedger(nodes)
    p10 = "doc/p10"
    p11 = "doc/p11"
    addr1 = find_range(nodes, p10, (
        "1 Premium Care Center, Department of Cardiology, Fuwai Hospital, Chinese Academy of "
        "Medical Sciences & Peking Union Medical College, National Clinical Research Center for "
        "Cardiovascular Diseases, National Center for Cardiovascular Diseases, No.167 North Lishi "
        "Road, Xicheng District, Beijing, China"
    ))
    postal1 = find_range(nodes, p10, "100037")
    phone1 = find_range(nodes, p10, "+86(10)88322131")
    addr2 = find_range(
        nodes, p11,
        "2 Taizhou Building, 1088 Xueyuan Avenue, Shenzhen, People’s Republic of China",
    )
    postal2 = find_range(nodes, p11, "518055")

    ledger.consume(addr1, "address:addr1.text", "address:addr1:text")
    ledger.consume(postal1, "address:addr1.postal_code", "address:addr1:postal")
    ledger.consume(phone1, "address:addr1.phone", "address:addr1:phone")
    ledger.consume(addr2, "address:addr2.text", "address:addr2:text")
    ledger.consume(postal2, "address:addr2.postal_code", "address:addr2:postal")

    # 同一地址实体可被多位作者引用；这是关系复用，不是同一语义实例重复输出。
    ledger.consume(
        addr1, "author:Yinze-Ji.address_ref", "author:Yinze-Ji:address-ref",
        reuse_reason="作者与独立 Address 实体的多对多关系",
    )
    ledger.consume(
        addr1, "author:Aimin-Dang.address_ref", "author:Aimin-Dang:address-ref",
        reuse_reason="多位作者共用同一 Address 实体",
    )
    ledger.consume(
        addr1, "author:Naqiang-Lv.address_ref", "author:Naqiang-Lv:address-ref",
        reuse_reason="多位作者共用同一 Address 实体",
    )
    complement(ledger, p10, [addr1, postal1, phone1])
    complement(ledger, p11, [addr2, postal2])
    return ledger


def main() -> int:
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    nodes = read_nodes(root / "样例数据" / "01" / "初始文件.docx", {10, 11})
    good = build_good(nodes)
    good_report = good.audit()

    # 人工注入缺陷：删掉电话字段的消费记录，其他记录原样保留。
    broken = CoverageLedger(nodes)
    broken.entries = [entry for entry in good.entries if entry.semantic_instance != "address:addr1:phone"]
    broken_report = broken.audit()

    payload = {
        "scope": nodes,
        "good": good_report,
        "delete_phone_defect": broken_report,
    }
    (here / "p2_ledger_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    missing = broken_report["uncovered"]
    lines = [
        "# P2 字符区间两本账原型",
        "",
        "## 试验范围",
        "",
        "直接从 01 的 `word/document.xml` 取 `doc/p10` 和 `doc/p11`，即两条完整作者地址。"
        "原型以“节点+起止字符”记账，没有把段落压成不可定位的词袋。",
        "",
        "## 验证结果",
        "",
        f"- 完整账本：`ok={good_report['ok']}`，未覆盖 {len(good_report['uncovered'])} 处，"
        f"无依据复用 {len(good_report['unexplained_reuse'])} 处。",
        "- 同一地址区间被 Address 实体与三位作者关系引用，每次关系复用都有明确理由，不报重复。",
        "- 人工删掉 `address:addr1:phone` 的消费记录后，覆盖账必须失败。",
        "",
        "## 人工缺陷的精确定位",
        "",
    ]
    for issue in missing:
        lines.append(
            f"- `{issue['node_id']}[{issue['start']}:{issue['end']}]` 未覆盖：`{issue['text']}`"
        )
    lines += [
        "",
        "## 结论",
        "",
        "通过 P2：区间账能表达有依据的一对多复用，也能把块内删掉的单个字段定位到精确字符区间。",
        "",
    ]
    (here / "P2-字符区间两本账原型.md").write_text("\n".join(lines), encoding="utf-8")

    expected_phone = any(issue["text"] == "+86(10)88322131" for issue in missing)
    print(json.dumps({
        "good_ok": good_report["ok"],
        "broken_ok": broken_report["ok"],
        "missing": missing,
    }, ensure_ascii=False))
    return 0 if good_report["ok"] and not broken_report["ok"] and expected_phone else 1


if __name__ == "__main__":
    raise SystemExit(main())
