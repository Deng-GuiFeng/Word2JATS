"""封闭标准的确定性投影。

这些函数不从稿件布局推理语义，只在理解层已经确定了
字段类型之后，按相应标准把原文值投影为规范值。
"""

from __future__ import annotations

import re
from typing import Optional


_ORCID = re.compile(
    r"(?i)(?:https?://orcid\.org/)?(?:"
    r"(\d{4})-(\d{4})-(\d{4})-(\d{3}[\dX])|(\d{15}[\dX]))"
)


def canonical_orcid(value: str) -> Optional[str]:
    """把完整、校验码正确的 ORCID iD 投影为官方 HTTPS URI。"""
    if not isinstance(value, str):
        return None
    match = _ORCID.fullmatch(value)
    if match is None:
        return None
    groups = match.groups()
    if groups[4] is not None:
        raw = groups[4].upper()
        identifier = "-".join(
            raw[index:index + 4] for index in range(0, 16, 4)
        )
    else:
        identifier = "-".join(groups[:4]).upper()
    digits = identifier.replace("-", "")
    total = 0
    for digit in digits[:15]:
        total = (total + int(digit)) * 2
    remainder = (12 - total % 11) % 11
    check = "X" if remainder == 10 else str(remainder)
    if digits[-1] != check:
        return None
    return f"https://orcid.org/{identifier}"
