"""阶段 P1：遍历 14 份金标准，建立 SemanticDoc v2 的完备下界清单。

这个原型不导入生产转换器，也不使用评测器的归一化函数。它直接遍历
``样例数据/<key>/结构参考.xml``，盘点标签、属性、混合文本位置和媒体引用。
每个已出现的标签和属性必须能归入施工图规定的语义承载体；否则程序以非零状态结束。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree


XLINK = "http://www.w3.org/1999/xlink"
XML = "http://www.w3.org/XML/1998/namespace"


# 这里列的是“语义承载体”，不是把 JATS 标签原样复制成一套类。
# 实际类名与施工图第七.2节对齐；纯容器标签由渲染器确定性生成。
TAG_CARRIERS = {
    # JATS 文档骨架与纯容器
    **{tag: "渲染器确定性骨架" for tag in {
        "article", "front", "journal-meta", "journal-title-group", "publisher",
        "article-meta", "title-group", "article-categories", "subj-group",
        "contrib-group", "author-notes", "history", "permissions", "body", "back",
        "ref-list", "person-group", "fn-group", "table-wrap-foot", "thead", "tbody",
        "colgroup",
    }},
    # 出版工作流或期刊配置，不冒充稿件原文
    **{tag: "PubConfig/期刊注册表" for tag in {
        "journal-id", "journal-title", "abbrev-journal-title", "issn", "publisher-name",
        "copyright-statement", "copyright-year", "license", "license-p",
    }},
    # 前置区实体及其 SourceText 字段
    **{tag: "Contributor/Address/Affiliation/SourceText" for tag in {
        "contrib", "contrib-id", "name", "surname", "given-names", "suffix", "degrees",
        "role", "aff", "address", "addr-line", "postal-code", "phone", "email", "corresp",
    }},
    **{tag: "ArticleMeta/DateInfo/SourceText" for tag in {
        "article-id", "subject", "article-title", "date", "day", "month", "year",
        "abstract", "kwd-group", "kwd",
    }},
    # 通用块结构
    **{tag: "Section/Paragraph/SourceText" for tag in {
        "sec", "title", "p", "ack", "author-comment",
    }},
    # 行内格式是 SourceText 的有效 run 投影
    **{tag: "SourceText 行内格式投影" for tag in {
        "bold", "italic", "sup", "sub", "break",
    }},
    # 显示对象
    **{tag: "Figure/FigureGroup/GraphicalAbstract" for tag in {
        "fig", "fig-group", "caption", "graphic", "inline-graphic",
    }},
    # 表格
    **{tag: "TableBlock/TableCell" for tag in {
        "table-wrap", "table", "col", "tr", "th", "td",
    }},
    # 公式：MathML 子树是确定性 OMML→MathML 变换结果
    **{tag: "Formula/MathML 结构" for tag in {
        "disp-formula", "inline-formula", "math", "semantics", "mrow", "mi", "mn", "mo",
        "mtext", "mfrac", "msqrt", "msub", "msup", "msubsup", "mover", "munder", "mfenced",
    }},
    # 脚注与显式关系
    **{tag: "Note/关系边" for tag in {"fn", "xref"}},
    # 参考文献
    **{tag: "Reference/SourceText" for tag in {
        "ref", "label", "element-citation", "mixed-citation", "collab", "etal",
        "article-title", "chapter-title", "source", "edition", "publisher-loc", "publisher-name",
        "year", "month", "day", "volume", "issue", "fpage", "lpage", "elocation-id",
        "pub-id", "ext-link", "comment",
    }},
    # 术语表
    **{tag: "Glossary/DefList/SourceText" for tag in {
        "glossary", "def-list", "def-item", "term", "def",
    }},
}


# 同名标签在不同上下文可由不同类承载，上表只要证明有足够容量即可。
# 属性按信息性质归类，组合级清单仍会在报告中逐条列出。
def attribute_carrier(tag: str, attr: str) -> str | None:
    if attr == "id":
        return "语义实体内部身份 + DocIdAllocator"
    if attr in {"rid", "ref-type"} and tag == "xref":
        return "Note/交叉引用关系边"
    if attr in {"href"}:
        return "媒体资源引用或 SourceText.links"
    if tag in {"td", "th", "col"} and attr in {
        "align", "valign", "style", "scope", "rowspan", "colspan", "width"
    }:
        return "TableCell 有效物理属性/表头语义"
    if tag == "article" and attr in {"article-type", "dtd-version", "lang"}:
        return "SemanticDoc/PubConfig + 渲染器版本配置"
    if tag == "article-id" and attr == "pub-id-type":
        return "PubConfig/出版工作流标识类型"
    if tag == "abstract" and attr == "abstract-type":
        return "Abstract.kind"
    if tag == "contrib" and attr in {"contrib-type", "corresp"}:
        return "Contributor.kind/通讯关系"
    if tag == "contrib-group" and attr == "content-type":
        return "ContributorGroup.kind"
    if tag == "contrib-id" and attr in {"authenticated", "contrib-id-type"}:
        return "ContributorIdentifier"
    if tag == "date" and attr == "date-type":
        return "DateInfo.kind"
    if tag in {"element-citation", "mixed-citation"} and attr == "publication-type":
        return "Reference.publication_type"
    if tag == "ext-link" and attr == "ext-link-type":
        return "SourceText.links/Reference.doi_carrier"
    if tag in {"fig", "table-wrap"} and attr == "position":
        return "显示对象渲染策略"
    if tag == "fn" and attr == "fn-type":
        return "Note.kind"
    if tag == "issn" and attr == "pub-type":
        return "期刊注册表 ISSN 类型"
    if tag == "journal-id" and attr == "journal-id-type":
        return "期刊注册表标识类型"
    if tag == "kwd-group" and attr == "kwd-group-type":
        return "KeywordGroup.kind"
    if tag == "license" and attr == "license-type":
        return "PubConfig.license"
    if tag in {"math", "mi", "mo", "mover", "mfenced"} and attr in {
        "alttext", "display", "mathvariant", "stretchy", "accent", "open", "close", "separators"
    }:
        return "Formula/MathML 确定性变换结果"
    if tag == "person-group" and attr == "person-group-type":
        return "Reference.person_group.kind"
    if tag == "pub-id" and attr == "pub-id-type":
        return "ReferenceIdentifier.kind"
    if tag == "subj-group" and attr == "subj-group-type":
        return "ArticleCategory.kind"
    if tag == "abbrev-journal-title" and attr == "abbrev-type":
        return "期刊注册表缩写类型"
    return None


def local_name(name: str) -> str:
    return etree.QName(name).localname


def element_path(element) -> str:
    """输出不依赖命名空间前缀的稳定路径。"""
    parts: list[str] = []
    current = element
    while current is not None and isinstance(current.tag, str):
        name = local_name(current.tag)
        parent = current.getparent()
        if parent is None:
            parts.append(name)
            break
        siblings = [c for c in parent if isinstance(c.tag, str) and local_name(c.tag) == name]
        suffix = f"[{siblings.index(current) + 1}]" if len(siblings) > 1 else ""
        parts.append(name + suffix)
        current = parent
    return "/" + "/".join(reversed(parts))


def inventory(samples_root: Path) -> dict:
    tag_counts: Counter[str] = Counter()
    attr_counts: Counter[tuple[str, str, str]] = Counter()
    attr_pairs: Counter[tuple[str, str]] = Counter()
    mixed_slots: Counter[tuple[str, str, str]] = Counter()
    media_refs: list[dict] = []
    per_sample: dict[str, dict] = {}

    for sample_dir in sorted(p for p in samples_root.iterdir() if p.is_dir()):
        xml_path = sample_dir / "结构参考.xml"
        if not xml_path.exists():
            continue
        root = etree.parse(str(xml_path)).getroot()
        sample_tags: Counter[str] = Counter()
        sample_attrs: Counter[str] = Counter()
        sample_media = 0
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            tag = local_name(element.tag)
            tag_counts[tag] += 1
            sample_tags[tag] += 1
            for raw_attr, value in element.attrib.items():
                attr = local_name(raw_attr)
                attr_counts[(tag, attr, value)] += 1
                attr_pairs[(tag, attr)] += 1
                sample_attrs[f"{tag}@{attr}"] += 1
            if element.text and element.text.strip() and len(element):
                mixed_slots[(tag, "text-before-child", local_name(element[0].tag))] += 1
            for child in element:
                if child.tail and child.tail.strip():
                    mixed_slots[(tag, "tail-after-child", local_name(child.tag))] += 1
            if tag in {"graphic", "inline-graphic", "media"}:
                href = element.get(f"{{{XLINK}}}href") or element.get("href") or ""
                media_refs.append({
                    "sample": sample_dir.name,
                    "element": tag,
                    "path": element_path(element),
                    "href": href,
                })
                sample_media += 1
        per_sample[sample_dir.name] = {
            "elements": sum(sample_tags.values()),
            "tag_kinds": len(sample_tags),
            "tags": dict(sorted(sample_tags.items())),
            "attributes": dict(sorted(sample_attrs.items())),
            "media_references": sample_media,
        }

    unknown_tags = sorted(set(tag_counts) - set(TAG_CARRIERS))
    attr_coverage = {}
    unknown_attrs = []
    for tag, attr in sorted(attr_pairs):
        carrier = attribute_carrier(tag, attr)
        attr_coverage[f"{tag}@{attr}"] = carrier
        if carrier is None:
            unknown_attrs.append(f"{tag}@{attr}")

    return {
        "summary": {
            "samples": len(per_sample),
            "elements": sum(tag_counts.values()),
            "tag_kinds": len(tag_counts),
            "attribute_pairs": len(attr_pairs),
            "attribute_values": len(attr_counts),
            "mixed_slots": sum(mixed_slots.values()),
            "media_references": len(media_refs),
        },
        "tag_counts": dict(sorted(tag_counts.items())),
        "tag_coverage": {tag: TAG_CARRIERS.get(tag) for tag in sorted(tag_counts)},
        "attribute_counts": {
            f"{tag}@{attr}={json.dumps(value, ensure_ascii=False)}": count
            for (tag, attr, value), count in sorted(attr_counts.items())
        },
        "attribute_coverage": attr_coverage,
        "mixed_content_slots": {
            f"{parent}:{slot}:{child}": count
            for (parent, slot, child), count in sorted(mixed_slots.items())
        },
        "media_references": media_refs,
        "per_sample": per_sample,
        "unknown_tags": unknown_tags,
        "unknown_attributes": unknown_attrs,
    }


def markdown(data: dict) -> str:
    s = data["summary"]
    lines = [
        "# P1 金标准结构清单与 SemanticDoc v2 完备下界",
        "",
        "## 结论",
        "",
        (f"已遍历 {s['samples']} 份金标准，共 {s['elements']} 个元素、"
         f"{s['tag_kinds']} 种标签、{s['attribute_pairs']} 种“标签@属性”组合、"
         f"{s['media_references']} 处媒体引用。"),
        "",
        "SemanticDoc v2 不需要为每个 JATS 标签造一个同名类，但必须能以语义实体、"
        "SourceText、关系边、显示对象、确定性渲染骨架和 PubConfig 完整承载这些事实。"
        "下表是实现阶段的强制下界：装载器遇到表中结构却无承载位时必须报错，不得保留原始 XML 逃逸。",
        "",
        "## 按样例统计",
        "",
        "| 样例 | 元素 | 标签种类 | 媒体引用 | ref | fig | fig-group | table-wrap | 公式 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, row in data["per_sample"].items():
        tags = row["tags"]
        formulas = tags.get("disp-formula", 0) + tags.get("inline-formula", 0)
        lines.append(
            f"| {key} | {row['elements']} | {row['tag_kinds']} | {row['media_references']} | "
            f"{tags.get('ref', 0)} | {tags.get('fig', 0)} | {tags.get('fig-group', 0)} | "
            f"{tags.get('table-wrap', 0)} | {formulas} |"
        )

    lines += ["", "## 标签→语义承载体", "", "| 标签 | 次数 | 承载体 |", "|---|---:|---|"]
    for tag, count in data["tag_counts"].items():
        lines.append(f"| `{tag}` | {count} | {data['tag_coverage'][tag] or '**未覆盖**'} |")

    lines += ["", "## 属性组合→承载体", "", "| 组合 | 次数 | 承载体 |", "|---|---:|---|"]
    pair_counts: Counter[str] = Counter()
    for key, count in data["attribute_counts"].items():
        pair_counts[key.split("=", 1)[0]] += count
    for pair, carrier in data["attribute_coverage"].items():
        lines.append(f"| `{pair}` | {pair_counts[pair]} | {carrier or '**未覆盖**'} |")

    lines += [
        "", "## 混合内容位置", "",
        "这些位置要求 `SourceText.ranges` 与 run 投影保留元素前文本、子元素和 tail 的顺序；"
        "往返测试必须比较混合内容次序，不能只比较 `itertext()` 总字符串。",
        "", "| 父元素:位置:子元素 | 次数 |", "|---|---:|",
    ]
    for slot, count in data["mixed_content_slots"].items():
        lines.append(f"| `{slot}` | {count} |")

    lines += [
        "", "## 媒体引用", "",
        "具体路径与 href 见 `p1_gold_inventory.json` 的 `media_references`。语义层承载对象与资源身份，"
        "往返比较按媒体字节身份归一，不把历史文件名当作语义。",
        "", "## 门禁结果", "",
        f"- 未归类标签：{data['unknown_tags'] or '无'}",
        f"- 未归类属性组合：{data['unknown_attributes'] or '无'}",
        "- 结论：" + ("通过 P1。" if not data["unknown_tags"] and not data["unknown_attributes"] else "未通过 P1。"),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples-root", type=Path, default=Path("样例数据"))
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    data = inventory(args.samples_root)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "p1_gold_inventory.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out_dir / "P1-金标准结构清单.md").write_text(markdown(data), encoding="utf-8")
    if data["unknown_tags"] or data["unknown_attributes"]:
        print(json.dumps({
            "unknown_tags": data["unknown_tags"],
            "unknown_attributes": data["unknown_attributes"],
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(data["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
