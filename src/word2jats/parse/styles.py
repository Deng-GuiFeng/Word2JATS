"""Word 样式继承与文字实际生效格式解析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lxml import etree

from ..model.source import RunRef
from .ooxml import is_on, qn, w_val


@dataclass(frozen=True)
class _Style:
    style_id: str
    kind: str
    name: Optional[str]
    based_on: Optional[str]
    run_properties: dict[str, object]
    table_conditions: dict[str, dict[str, object]]
    default: bool = False


def _on_or_off(element) -> Optional[bool]:
    return None if element is None else is_on(element)


def _run_properties(rpr) -> dict[str, object]:
    if rpr is None:
        return {}
    result: dict[str, object] = {}
    for tag, key in (
        ("b", "bold"), ("i", "italic"), ("strike", "strike"),
        ("smallCaps", "small_caps"),
    ):
        value = _on_or_off(rpr.find(qn(f"w:{tag}")))
        if value is not None:
            result[key] = value
    underline = rpr.find(qn("w:u"))
    if underline is not None:
        result["underline"] = (w_val(underline) or "single").lower() not in {
            "none", "nil", "false", "0", "off",
        }
    vertical = rpr.find(qn("w:vertAlign"))
    if vertical is not None:
        value = (w_val(vertical) or "baseline").lower()
        result["superscript"] = value == "superscript"
        result["subscript"] = value == "subscript"
    language = rpr.find(qn("w:lang"))
    if language is not None:
        value = w_val(language) or language.get(qn("w:eastAsia"))
        if value:
            result["language"] = value
    return result


class StyleResolver:
    """按 docDefaults→段落样式→字符样式→直接格式计算有效值。"""

    def __init__(self, styles_xml: bytes | None):
        self.styles: dict[str, _Style] = {}
        self.default_run: dict[str, object] = {}
        self.default_paragraph_style: Optional[str] = None
        if not styles_xml:
            return
        root = etree.fromstring(styles_xml)
        defaults = root.find(qn("w:docDefaults"))
        if defaults is not None:
            rpr_default = defaults.find(qn("w:rPrDefault"))
            self.default_run = _run_properties(
                rpr_default.find(qn("w:rPr")) if rpr_default is not None else None
            )
        for element in root.findall(qn("w:style")):
            style_id = w_val(element, "styleId")
            if not style_id:
                continue
            kind = w_val(element, "type") or ""
            name = element.find(qn("w:name"))
            based = element.find(qn("w:basedOn"))
            style = _Style(
                style_id=style_id, kind=kind,
                name=w_val(name) if name is not None else None,
                based_on=w_val(based) if based is not None else None,
                run_properties=_run_properties(element.find(qn("w:rPr"))),
                table_conditions={
                    w_val(condition, "type") or "": _run_properties(
                        condition.find(qn("w:rPr"))
                    )
                    for condition in element.findall(qn("w:tblStylePr"))
                },
                default=is_on(element) if element.get(qn("w:default")) is not None else False,
            )
            self.styles[style_id] = style
            if kind == "paragraph" and element.get(qn("w:default")) in {"1", "true", "on"}:
                self.default_paragraph_style = style_id

    def style_name(self, style_id: Optional[str]) -> Optional[str]:
        style = self.styles.get(style_id or self.default_paragraph_style or "")
        return style.name if style else None

    def _chain(self, style_id: Optional[str], expected_kind: str) -> list[_Style]:
        chain = []
        seen = set()
        current = style_id
        while current and current not in seen:
            seen.add(current)
            style = self.styles.get(current)
            if style is None:
                break
            if style.kind == expected_kind:
                chain.append(style)
            current = style.based_on
        chain.reverse()
        return chain

    @staticmethod
    def _table_conditions(context: dict) -> list[str]:
        row = int(context.get("row", 0))
        col = int(context.get("col", 0))
        colspan = max(1, int(context.get("colspan", 1)))
        rows = max(1, int(context.get("rows", 1)))
        cols = max(1, int(context.get("cols", 1)))
        look = context.get("look") or {}
        values = ["wholeTable"]
        if not look.get("no_h_band", False):
            values.append("band1Horz" if row % 2 == 0 else "band2Horz")
        if not look.get("no_v_band", True):
            values.append("band1Vert" if col % 2 == 0 else "band2Vert")
        first_row = row == 0 and look.get("first_row", True)
        last_row = row == rows - 1 and look.get("last_row", False)
        first_col = col == 0 and look.get("first_col", False)
        last_col = col + colspan >= cols and look.get("last_col", False)
        if first_col:
            values.append("firstCol")
        if last_col:
            values.append("lastCol")
        if first_row:
            values.append("firstRow")
        if last_row:
            values.append("lastRow")
        if first_row and first_col:
            values.append("nwCell")
        if first_row and last_col:
            values.append("neCell")
        if last_row and first_col:
            values.append("swCell")
        if last_row and last_col:
            values.append("seCell")
        return values

    def effective(self, r_element, paragraph_style: Optional[str], *,
                  run_id: str, part: str, node_path: str,
                  table_context: Optional[dict] = None) -> RunRef:
        values: dict[str, object] = {
            "bold": False, "italic": False, "superscript": False,
            "subscript": False, "underline": False, "strike": False,
            "small_caps": False, "language": None,
        }
        values.update(self.default_run)
        if table_context and table_context.get("style_id"):
            for style in self._chain(table_context["style_id"], "table"):
                values.update(style.run_properties)
                for condition in self._table_conditions(table_context):
                    values.update(style.table_conditions.get(condition, {}))
        paragraph_style = paragraph_style or self.default_paragraph_style
        for style in self._chain(paragraph_style, "paragraph"):
            values.update(style.run_properties)
        rpr = r_element.find(qn("w:rPr"))
        rstyle = rpr.find(qn("w:rStyle")) if rpr is not None else None
        character_style = w_val(rstyle) if rstyle is not None else None
        for style in self._chain(character_style, "character"):
            values.update(style.run_properties)
        values.update(_run_properties(rpr))
        # vertAlign 的两个布尔值不得同时为真。
        if values.get("superscript"):
            values["subscript"] = False
        elif values.get("subscript"):
            values["superscript"] = False
        return RunRef(
            run_id=run_id, part=part, node_path=node_path,
            style_id=character_style or paragraph_style,
            bold=bool(values["bold"]), italic=bool(values["italic"]),
            superscript=bool(values["superscript"]),
            subscript=bool(values["subscript"]),
            underline=bool(values["underline"]), strike=bool(values["strike"]),
            small_caps=bool(values["small_caps"]),
            language=values["language"] if isinstance(values["language"], str) else None,
        )
