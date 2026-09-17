"""把现有转换产物组织为用户可浏览、定位的结果，不改写原 XML。"""
import json
from pathlib import Path

from lxml import etree

from .editor import extract, parse, text
from .source_view import anchor


def prepare(xml, report=None):
    root = parse(xml)
    records = (report or {}).get("provenance", [])
    blocks = []
    names = {"article-title": "文章信息", "abstract": "摘要", "trans-abstract": "译文摘要", "sec": "章节", "fig": "图", "table-wrap": "表", "disp-formula": "公式", "ref-list": "参考文献"}
    eligible = set(names) | {"p", "ref", "inline-formula"}
    paths = {root.getroottree().getpath(n): n for n in root.iter() if isinstance(n.tag, str)}
    used = {n.get("id") for n in root.iter() if n.get("id")}
    for path, node in paths.items():
        if node.tag not in eligible:
            continue
        if node.tag == "article-title" and path != "/article/front/article-meta/title-group/article-title":
            continue
        ident = node.get("id")
        if not ident:
            ident = "w2j-view-" + str(len(blocks) + 1)
            while ident in used:
                ident += "x"
            node.set("id", ident); used.add(ident)
        origin = next((r for r in records if r.get("source_ranges") and (r.get("output_path", "").startswith(path + "/") or r.get("output_path") == path)), {})
        ranges = origin.get("source_ranges") or []
        source_id = ranges[0][0] if ranges else ""
        label = text(node.find("title")) or text(node.find("label")) or names.get(node.tag, "正文段落")
        if node.tag == "article-title":
            label = "文章信息"
        elif node.tag == "ref-list":
            label = "参考文献"
        elif node.tag == "ref":
            label = "文献 " + (text(node.find("label")) or str(1 + sum(b["kind"] == "ref" for b in blocks)))
        elif node.tag == "inline-formula":
            label = "行内公式 " + str(1 + sum(b["kind"] == "inline-formula" for b in blocks))
        description = ""
        if node.tag == "ref":
            forms = []
            if node.find("element-citation") is not None: forms.append("字段著录")
            if node.find("mixed-citation") is not None: forms.append("混合著录")
            description = "、".join(forms) or "文献条目"
        elif node.tag in {"disp-formula", "inline-formula"}:
            forms = []
            if node.xpath('.//*[local-name()="math" and namespace-uri()="http://www.w3.org/1998/Math/MathML"]'): forms.append("MathML")
            if node.xpath('.//graphic | .//inline-graphic'): forms.append("图像")
            if node.find(".//tex-math") is not None: forms.append("TeX")
            description = "、".join(forms) or "文字表达"
        elif node.tag == "table-wrap":
            description = "单元格结构" if node.find(".//table") is not None else "图像表格" if node.find(".//graphic") is not None else "表格内容"
        depth = sum(parent.tag in {"sec", "abstract"} for parent in node.iterancestors())
        blocks.append({"id": ident, "label": label[:120], "kind": node.tag, "depth": depth, "description": description,
                       "navigation": node.tag in names,
                       "source_id": source_id, "source_anchor": anchor(source_id) if source_id else ""})
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True), blocks


def structure(xml):
    """当前 XML 的内容清单；数量与承载形式不是正确率。"""
    root = parse(xml)
    fields = extract(xml)
    return {
        "authors": len(fields["authors"]), "affiliations": len(fields["affiliations"]),
        "keywords": [text(n) for n in root.findall("./front/article-meta/kwd-group/kwd")],
    }


def read_report(result):
    file = Path(result["candidate_dir"]).parent / "report.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}


def issues(result, xml, report, blocks):
    rows = []
    pub = extract(xml)["publication"]
    if not pub["journal_id"] or not (pub["issn_print"] or pub["issn_electronic"]):
        rows.append({"title": "缺少期刊信息", "detail": "选择期刊，或填写刊名和 ISSN，保存后会重新检查当前 XML。", "action": "publication", "category": "publication", "blocking": True})
    for item in result.get("review_items", []):
        # 正常整理记录不变成人工待办；未结构化文献作为内容详情，不强制签收。
        if item["code"] in {"SINGLE_CELL_WRAPPER_UNWRAPPED", "REFERENCE_FIELDS_INCOMPLETE"}:
            continue
        source_id = item.get("source_id", "")
        match = next((b for b in blocks if b["source_id"] == source_id), None)
        rows.append({"title": item["title"], "detail": item.get("action", ""),
                     "action": "source", "category": "content", "source_id": source_id,
                     "excerpt": item.get("source_text", "")[:500],
                     "source_anchor": anchor(source_id) if source_id else "",
                     "anchor": match["id"] if match else "", "blocking": item["blocking"]})
    validation = result.get("validation") or {}
    other_format_errors = [e for e in validation.get("errors", []) if "journal-meta" not in e]
    if not validation.get("dtd_valid") and (not rows or other_format_errors):
        rows.append({"title": "XML 结构需要调整", "detail": "展开技术详情可查看具体标签问题。网页可修改文章与出版信息；其他标签或引用关系需下载 XML 后调整，也可更换模型重新转换。", "action": "checks", "category": "format", "blocking": True})
    # delivered 是原始自动转换结论。补齐出版信息后只解除已有证据证明
    # 已解决的格式失败，不把旧的 DTD 失败换成新的、无依据的正文警告。
    gates = (result.get("stats", {}).get("verify", {}).get("gates") or {})
    required = {"understanding", "supported_ooxml", "well_formed", "dtd", "id_unique",
                "rid_closed", "media_bytes", "media_format", "no_redundant_files",
                "source_coverage", "output_provenance"}
    format_only_resolved = (result.get("edited") and validation.get("ok") and
        required.issubset(gates) and gates.get("dtd") is False and
        all(value is True for name, value in gates.items() if name != "dtd"))
    if result.get("delivered") is False and not rows and not format_only_resolved:
        rows.append({"title": "部分内容需要进一步检查", "detail": "请对照原稿检查内容。网页可修改文章与出版信息；正文结构需要调整时，可下载 XML 继续编辑，或整理 Word 后重新转换。", "action": "checks", "category": "content", "blocking": True})
    return rows
