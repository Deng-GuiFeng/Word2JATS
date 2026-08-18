"""Word 表格物理属性的确定性登记。

本模块只记录 OOXML 中可确定的事实，不决定哪些可选样式应发射到
JATS。后者按 P5 结论由显式出版配置或语义判断决定。
"""

from __future__ import annotations

from .ooxml import is_on, qn, w_val


def _integer(element, attr="val", default=None):
    if element is None:
        return default
    try:
        return int(w_val(element, attr) or "")
    except (TypeError, ValueError):
        return default


def _border(element):
    if element is None:
        return None
    return {
        "style": w_val(element),
        "size_eighth_points": _integer(element, "sz"),
        "color": w_val(element, "color"),
        "space_points": _integer(element, "space"),
    }


def table_properties(table) -> dict:
    properties = table.find(qn("w:tblPr"))
    grid = table.find(qn("w:tblGrid"))
    widths = []
    if grid is not None:
        for column in grid.findall(qn("w:gridCol")):
            widths.append(_integer(column, "w", 0))
    result = {"grid_cols_twips": widths}
    if properties is None:
        return result
    style = properties.find(qn("w:tblStyle"))
    width = properties.find(qn("w:tblW"))
    layout = properties.find(qn("w:tblLayout"))
    look = properties.find(qn("w:tblLook"))
    borders = properties.find(qn("w:tblBorders"))
    result.update({
        "style_id": w_val(style) if style is not None else None,
        "width": {
            "value": _integer(width, "w"),
            "type": w_val(width, "type"),
        } if width is not None else None,
        "layout": w_val(layout, "type") if layout is not None else None,
        "look": _table_look(look),
        "borders": {
            side: _border(borders.find(qn(f"w:{side}")))
            for side in ("top", "left", "bottom", "right", "insideH", "insideV")
        } if borders is not None else None,
    })
    return result


def _table_look(element) -> dict:
    if element is None:
        return {
            "first_row": True, "last_row": False,
            "first_col": False, "last_col": False,
            "no_h_band": False, "no_v_band": True,
        }
    raw = w_val(element)
    try:
        bits = int(raw, 16) if raw else 0
    except ValueError:
        bits = 0

    def flag(attribute: str, bit: int, default: bool) -> bool:
        explicit = element.get(qn(f"w:{attribute}"))
        if explicit is not None:
            return str(explicit).lower() not in {"0", "false", "off", "none"}
        return bool(bits & bit) if raw else default

    return {
        "first_row": flag("firstRow", 0x20, True),
        "last_row": flag("lastRow", 0x40, False),
        "first_col": flag("firstColumn", 0x80, False),
        "last_col": flag("lastColumn", 0x100, False),
        "no_h_band": flag("noHBand", 0x200, False),
        "no_v_band": flag("noVBand", 0x400, True),
    }


def row_properties(row) -> dict:
    properties = row.find(qn("w:trPr"))
    if properties is None:
        return {"header": False}
    height = properties.find(qn("w:trHeight"))
    cant_split = properties.find(qn("w:cantSplit"))
    return {
        "header": is_on(properties.find(qn("w:tblHeader"))),
        "height_twips": _integer(height),
        "height_rule": w_val(height, "hRule") if height is not None else None,
        "cant_split": is_on(cant_split) if cant_split is not None else None,
    }


def cell_properties(cell) -> dict:
    properties = cell.find(qn("w:tcPr"))
    if properties is None:
        return {"grid_span": 1, "v_merge": None}
    span = properties.find(qn("w:gridSpan"))
    merge = properties.find(qn("w:vMerge"))
    width = properties.find(qn("w:tcW"))
    valign = properties.find(qn("w:vAlign"))
    borders = properties.find(qn("w:tcBorders"))
    shading = properties.find(qn("w:shd"))
    return {
        "grid_span": _integer(span, default=1) or 1,
        "v_merge": (w_val(merge) or "continue") if merge is not None else None,
        "width": {
            "value": _integer(width, "w"), "type": w_val(width, "type"),
        } if width is not None else None,
        "vertical_alignment": w_val(valign) if valign is not None else None,
        "borders": {
            side: _border(borders.find(qn(f"w:{side}")))
            for side in ("top", "left", "bottom", "right", "insideH", "insideV")
        } if borders is not None else None,
        "shading": {
            "fill": w_val(shading, "fill"), "color": w_val(shading, "color"),
            "pattern": w_val(shading),
        } if shading is not None else None,
    }
