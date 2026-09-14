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
    names = {"article-title": "文章信息", "abstract": "摘要", "sec": "章节", "fig": "图", "table-wrap": "表", "disp-formula": "公式", "ref-list": "参考文献"}
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
        depth = sum(parent.tag in {"sec", "abstract"} for parent in node.iterancestors())
        blocks.append({"id": ident, "label": label[:120], "kind": node.tag, "depth": depth,
                       "navigation": node.tag in names,
                       "source_id": source_id, "source_anchor": anchor(source_id) if source_id else ""})
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True), blocks


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
    if result.get("delivered") is False and not rows:
        rows.append({"title": "部分内容需要进一步检查", "detail": "请对照原稿检查内容。网页可修改文章与出版信息；正文结构需要调整时，可下载 XML 继续编辑，或整理 Word 后重新转换。", "action": "checks", "category": "content", "blocking": True})
    return rows
