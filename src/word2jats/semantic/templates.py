"""完整匹配未填写的出版日期模板；实际日期和文章内容不在此范围内。"""
import re

_MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
_EMPTY = r'(?:[.．…_–—-]{2,}|…+)'
_DATE = rf'(?:{_EMPTY}|{_MONTH}\s+{_EMPTY}\s*,?\s*\d{{4}})'
_PART = rf'(?:Received|Accepted|Revised|Submitted)\s*:?\s*{_DATE}'
_HISTORY = re.compile(rf'\s*\(\s*{_PART}(?:\s*;\s*{_PART})*\s*\)\s*',re.I)


def is_unfilled_publication_history(text):
    return isinstance(text,str) and _HISTORY.fullmatch(text) is not None
