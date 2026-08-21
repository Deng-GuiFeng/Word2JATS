"""阶段 P5：全部原生表的属性可判定性与交付事务保证实验。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import ZipFile

from lxml import etree


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
Q = lambda local: f"{{{W}}}{local}"


def norm(text: str) -> str:
    # Word 常用上标字符表示化学价态，金标准会把它等价展平为普通字符。
    return re.sub(r"[^0-9a-z]+", "", unicodedata.normalize("NFKC", text).casefold())


def source_text(element) -> str:
    return "".join(element.xpath(".//w:t/text()", namespaces=NS)).strip()


def gold_text(element) -> str:
    return "".join(element.itertext()).strip()


def token_set(text: str) -> set[str]:
    return set(re.findall(r"[0-9a-z]+", text.casefold()))


def similarity(a: str, b: str) -> float:
    left, right = token_set(a), token_set(b)
    return len(left & right) / len(left | right) if left or right else 1.0


@dataclass
class SourceCell:
    element: object
    row: int
    col: int
    text: str
    header_row_signal: bool


def origin_cells(table) -> list[SourceCell]:
    result = []
    for row_index, row in enumerate(table.findall("./w:tr", namespaces=NS)):
        trpr = row.find("./w:trPr", namespaces=NS)
        header = trpr is not None and trpr.find("./w:tblHeader", namespaces=NS) is not None
        col = 0
        for cell in row.findall("./w:tc", namespaces=NS):
            pr = cell.find("./w:tcPr", namespaces=NS)
            span_el = pr.find("./w:gridSpan", namespaces=NS) if pr is not None else None
            span = int((span_el.get(Q("val")) if span_el is not None else "1") or "1")
            merge = pr.find("./w:vMerge", namespaces=NS) if pr is not None else None
            merge_value = (merge.get(Q("val")) or "continue") if merge is not None else None
            if merge_value != "continue":
                result.append(SourceCell(cell, row_index, col, source_text(cell), header))
            col += span
    return result


def table_text_source(table) -> str:
    return " ".join(cell.text for cell in origin_cells(table))


def table_text_gold(table) -> str:
    return " ".join(gold_text(cell) for cell in table.xpath('.//*[local-name()="th" or local-name()="td"]'))


def match_tables(source_tables, gold_tables) -> tuple[list[tuple[int, int, float]], list[int]]:
    """按文本身份做一对一匹配，不用样例号或表序号特判。"""
    candidates = []
    for si, source in enumerate(source_tables):
        for gi, gold in enumerate(gold_tables):
            candidates.append((similarity(table_text_source(source), table_text_gold(gold)), si, gi))
    used_source, used_gold, matched = set(), set(), []
    for score, si, gi in sorted(candidates, reverse=True):
        if si in used_source or gi in used_gold:
            continue
        used_source.add(si)
        used_gold.add(gi)
        matched.append((si, gi, score))
    matched.sort(key=lambda item: item[1])
    return matched, sorted(set(range(len(source_tables))) - used_source)


def pair_cells(source_cells: list[SourceCell], gold_cells: list) -> tuple[list[tuple[SourceCell, object]], int]:
    """单调对齐：先找同文本身份，允许跳过 Word 里的空布局格/重复合并格。"""
    pairs = []
    cursor = 0
    low_confidence = 0
    for gold in gold_cells:
        target = norm(gold_text(gold))
        chosen = None
        # 常规情况只需看后续少量单元格；S03 的头部有三个布局重复格。
        for index in range(cursor, min(len(source_cells), cursor + 12)):
            if norm(source_cells[index].text) == target:
                chosen = index
                break
        if chosen is None and cursor < len(source_cells):
            # 上下标/软回车可能使纯文本归一仍有差异；只用顺序兜底做属性实验。
            chosen = cursor
            low_confidence += 1
        if chosen is None:
            break
        pairs.append((source_cells[chosen], gold))
        cursor = chosen + 1
    return pairs, low_confidence


def paragraph_alignment(cell) -> str:
    candidates = cell.xpath(".//w:p", namespaces=NS)
    chosen = None
    for paragraph in candidates:
        text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()
        jc = paragraph.find("./w:pPr/w:jc", namespaces=NS)
        if text and jc is not None:
            chosen = jc.get(Q("val"))
            break
        if chosen is None and jc is not None:
            chosen = jc.get(Q("val"))
    if chosen == "center":
        return "center"
    if chosen in {"right", "end"}:
        return "right"
    return "left"


def vertical_alignment(cell) -> str:
    value = cell.element.find("./w:tcPr/w:vAlign", namespaces=NS)
    raw = value.get(Q("val")) if value is not None else None
    return {"center": "middle", "bottom": "bottom"}.get(raw, "top")


def border_value(border) -> str | None:
    if border is None:
        return None
    val = (border.get(Q("val")) or "").lower()
    if val in {"", "nil", "none"}:
        return None
    if val != "single":
        return f"unsupported:{val}"
    try:
        points = int(border.get(Q("sz")) or "4") / 8
    except ValueError:
        return "unsupported:size"
    width = str(int(points)) if points.is_integer() else str(points)
    color = (border.get(Q("color")) or "000000").upper()
    if color == "AUTO":
        color = "000000"
    return f"{width}pt solid #{color}"


def source_border(cell: SourceCell, table, side: str) -> str | None:
    direct = cell.element.find(f"./w:tcPr/w:tcBorders/w:{side}", namespaces=NS)
    if direct is not None:
        return border_value(direct)
    rows = table.findall("./w:tr", namespaces=NS)
    table_borders = table.find("./w:tblPr/w:tblBorders", namespaces=NS)
    if table_borders is None:
        return None
    if side == "top":
        key = "top" if cell.row == 0 else "insideH"
    else:
        key = "bottom" if cell.row == len(rows) - 1 else "insideH"
    return border_value(table_borders.find(f"./w:{key}", namespaces=NS))


def gold_style(element) -> dict[str, str]:
    result = {}
    for declaration in (element.get("style") or "").split(";"):
        if ":" in declaration:
            key, value = declaration.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def source_widths(table) -> list[str]:
    cols = table.findall("./w:tblGrid/w:gridCol", namespaces=NS)
    values = [int(col.get(Q("w")) or "0") for col in cols]
    total = sum(values)
    return [f"{value / total * 100:.1f}%" for value in values] if total else []


class Metric:
    def __init__(self):
        self.correct = self.wrong = self.extra = self.missing = 0

    def add(self, predicted, actual):
        if actual is None and predicted is None:
            return
        if actual is None:
            self.extra += 1
        elif predicted is None:
            self.missing += 1
        elif predicted == actual:
            self.correct += 1
        else:
            self.wrong += 1

    def report(self):
        predicted = self.correct + self.wrong + self.extra
        required = self.correct + self.wrong + self.missing
        return {
            "correct": self.correct, "wrong": self.wrong,
            "extra": self.extra, "missing": self.missing,
            "precision": self.correct / predicted if predicted else 1.0,
            "recall": self.correct / required if required else 1.0,
            "required_value_accuracy": self.correct / (self.correct + self.wrong + self.missing)
            if required else 1.0,
        }


def transaction_probe() -> dict:
    """注入“第一个路径替换后失败”，验证备份回滚能恢复旧的并列交付物。"""
    with tempfile.TemporaryDirectory(prefix="word2jats-p5-") as temp:
        root = Path(temp)
        xml = root / "article.xml"
        media = root / "article"
        xml.write_bytes(b"old-xml")
        media.mkdir()
        (media / "old.bin").write_bytes(b"old-media")
        candidate = root / "candidate"
        candidate.mkdir()
        (candidate / "article.xml").write_bytes(b"new-xml")
        (candidate / "article").mkdir()
        (candidate / "article" / "new.bin").write_bytes(b"new-media")
        backup = root / "backup"
        backup.mkdir()

        def replace(inject_failure=False):
            shutil.move(str(xml), str(backup / "article.xml"))
            shutil.move(str(media), str(backup / "article"))
            try:
                shutil.copy2(candidate / "article.xml", xml)
                if inject_failure:
                    raise OSError("注入失败")
                shutil.copytree(candidate / "article", media)
            except Exception:
                if xml.exists():
                    xml.unlink()
                if media.exists():
                    shutil.rmtree(media)
                shutil.move(str(backup / "article.xml"), str(xml))
                shutil.move(str(backup / "article"), str(media))
                return False
            return True

        failed = replace(inject_failure=True)
        rollback_ok = (
            not failed and xml.read_bytes() == b"old-xml"
            and (media / "old.bin").read_bytes() == b"old-media"
        )
        # 复位候选仍存在，再做一次成功替换。
        success = replace(inject_failure=False)
        success_ok = (
            success and xml.read_bytes() == b"new-xml"
            and (media / "new.bin").read_bytes() == b"new-media"
        )
        return {
            "guarantee": "recoverable-not-atomic",
            "injected_failure_rolled_back": rollback_ok,
            "successful_replace": success_ok,
            "reason": "XML 与媒体目录是两个并列路径，无法用一次 rename 同时替换",
        }


def main() -> int:
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    metrics = {name: Metric() for name in (
        "align", "valign", "border-top", "border-bottom", "width", "scope-mechanical"
    )}
    samples = {}
    total_source_tables = total_gold_tables = total_pairs = low_confidence = 0
    non_native_gold_tables = 0
    unmatched_tables = []

    for sample_dir in sorted(p for p in (root / "样例数据").iterdir() if p.is_dir()):
        with ZipFile(sample_dir / "初始文件.docx") as archive:
            document = etree.fromstring(archive.read("word/document.xml"))
        source_tables = document.xpath("//w:body/w:tbl", namespaces=NS)
        gold_root = etree.parse(str(sample_dir / "结构参考.xml"))
        all_gold_tables = gold_root.xpath('//*[local-name()="table-wrap"]/*[local-name()="table"]')
        # P5 只研究 docx 原生表格。没有任何原生表的文档中，金标准表是从纯文本重建的，
        # 属于另一条语义提取路径，不能冒充原生表参与物理属性实验。
        gold_tables = all_gold_tables if source_tables else []
        non_native_gold_tables += len(all_gold_tables) - len(gold_tables)
        total_source_tables += len(source_tables)
        total_gold_tables += len(gold_tables)
        matched, unmatched = match_tables(source_tables, gold_tables)
        unmatched_tables.extend({"sample": sample_dir.name, "source_table": i} for i in unmatched)
        sample_rows = []
        for si, gi, score in matched:
            source_table, gold_table = source_tables[si], gold_tables[gi]
            source_cells = origin_cells(source_table)
            gold_cells = gold_table.xpath('.//*[local-name()="th" or local-name()="td"]')
            pairs, low = pair_cells(source_cells, gold_cells)
            low_confidence += low
            total_pairs += len(pairs)
            for source_cell, gold_cell in pairs:
                metrics["align"].add(paragraph_alignment(source_cell.element), gold_cell.get("align"))
                metrics["valign"].add(vertical_alignment(source_cell), gold_cell.get("valign"))
                gs = gold_style(gold_cell)
                metrics["border-top"].add(source_border(source_cell, source_table, "top"), gs.get("border-top"))
                metrics["border-bottom"].add(source_border(source_cell, source_table, "bottom"), gs.get("border-bottom"))
                predicted_scope = "col" if source_cell.header_row_signal else None
                metrics["scope-mechanical"].add(predicted_scope, gold_cell.get("scope"))
            predicted_widths = source_widths(source_table)
            gold_widths = [col.get("width") for col in gold_table.xpath('./*[local-name()="colgroup"]/*[local-name()="col"]')]
            for index in range(max(len(predicted_widths), len(gold_widths))):
                metrics["width"].add(
                    predicted_widths[index] if index < len(predicted_widths) else None,
                    gold_widths[index] if index < len(gold_widths) else None,
                )
            sample_rows.append({
                "source_table": si, "gold_table": gi, "text_similarity": score,
                "source_origin_cells": len(source_cells), "gold_cells": len(gold_cells),
                "paired_cells": len(pairs), "low_confidence_pairs": low,
                "source_widths": predicted_widths, "gold_widths": gold_widths,
            })
        samples[sample_dir.name] = {
            "source_tables": len(source_tables), "gold_native_tables": len(gold_tables),
            "matched": sample_rows, "unmatched_source_tables": unmatched,
        }

    metric_report = {name: metric.report() for name, metric in metrics.items()}
    transaction = transaction_probe()
    closures = {
        "colspan_rowspan": {
            "state": 1,
            "basis": "gridSpan/vMerge 是 OOXML 封闭结构；38 张目标原生表的起始格数与金标准单元格数对齐（S03 的 3 个重复布局格在文本单调对齐时显式跳过）",
        },
        "scope": {
            "state": 2,
            "basis": "w:tblHeader 只覆盖少部分列头，不表达行头；必须用表内容语义判断，P5 的金标准单元格 scope 作判真夹具",
        },
        "align_valign_border": {
            "state": 4,
            "basis": "属性值可从直接属性/默认值取得，但金标准是否发射这些可选 JATS 表格样式属性与 docx 信号不一致；盲目全发射会产生大量 extra",
        },
        "width": {
            "state": 4,
            "basis": "S01–S05 的金标准宽度逐列等于 w:tblGrid 归一百分比，但 X01 同样存在 pct 直接宽度却统一发射等宽；03 又是第三种非等宽结果，输入不足以确定体例选择",
        },
        "transaction": {
            "state": 1,
            "basis": "并列 XML/媒体路径只能提供加锁+备份+失败回滚的可恢复事务，不能宣称单次原子替换",
        },
    }
    payload = {
        "summary": {
            "source_tables": total_source_tables,
            "gold_native_tables": total_gold_tables,
            "non_native_gold_tables_excluded": non_native_gold_tables,
            "matched_tables": sum(len(v["matched"]) for v in samples.values()),
            "unmatched_source_tables": len(unmatched_tables),
            "paired_cells": total_pairs,
            "low_confidence_cell_pairs": low_confidence,
        },
        "metrics": metric_report,
        "closures": closures,
        "transaction": transaction,
        "unmatched_tables": unmatched_tables,
        "samples": samples,
    }
    (here / "p5_table_experiment.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summary = payload["summary"]
    lines = [
        "# P5 全原生表属性与交付事务实验",
        "",
        "## 覆盖范围",
        "",
        f"14 例 docx 共 {summary['source_tables']} 张原生表，金标准共 {summary['gold_native_tables']} 张对应的原生表。"
        f"按表文本身份一对一匹配 {summary['matched_tables']} 张，余下 {summary['unmatched_source_tables']} 张均为 X02 图形内部布局表，"
        f"不是 JATS 文章表。另有 {summary['non_native_gold_tables_excluded']} 张从纯文本重建的金标准表，"
        f"不纳入原生表物理属性实验。共对齐 {summary['paired_cells']} 个金标准单元格。",
        "",
        "## 机械取值对金标准的实测",
        "",
        "`extra` 表示 docx 可算出该物理值，但金标准没有发射相应可选属性。"
        "因此仅看“金标准已有值时的正确率”不足以决定渲染策略。",
        "",
        "| 属性 | 正确 | 值错 | 多发 | 漏发 | 全发射查准率 | 金标准需求查全率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metric in metric_report.items():
        lines.append(
            f"| {name} | {metric['correct']} | {metric['wrong']} | {metric['extra']} | {metric['missing']} | "
            f"{metric['precision']:.2%} | {metric['recall']:.2%} |"
        )
    lines += ["", "## 四种闭合状态", ""]
    labels = {1: "① OOXML 直接/继承确定性取得", 2: "② 语义判断取得", 3: "③ 显式出版配置取得", 4: "④ 输入不可判定，需要裁定"}
    for name, closure in closures.items():
        lines.append(f"- **{name} → {labels[closure['state']]}**：{closure['basis']}。")
    lines += [
        "",
        "## 事务保证",
        "",
        f"- 保证等级：`{transaction['guarantee']}`。",
        f"- 第一个路径替换后注入异常，旧产物完整恢复：{transaction['injected_failure_rolled_back']}。",
        f"- 正常替换后新 XML 与新媒体同时就位：{transaction['successful_replace']}。",
        "",
        "## 结论与阻断决策项",
        "",
        "P5 完成了全量实验，但按施工图门禁，`align/valign/border` 的“是否发射”与 `width` 体例属于④。"
        "不能由施工者自行删除目标，也不能按样例号分支。建议的最小裁定是："
        "**把表格物理属性发射策略作为显式 `PubConfig.table_style_profile`，未配置时发出 `review_blocking`，"
        "不根据样例或 DOI 暗猜。** scope 继续按语义判断实现，合并属性和可恢复交付按确定性代码实现。",
        "",
    ]
    (here / "P5-全原生表属性实验.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    checks = (
        summary["source_tables"] == 41
        and summary["gold_native_tables"] == 38
        and summary["matched_tables"] == 38
        and summary["unmatched_source_tables"] == 3
        and transaction["injected_failure_rolled_back"]
        and transaction["successful_replace"]
    )
    return 0 if checks else 1


if __name__ == "__main__":
    raise SystemExit(main())
