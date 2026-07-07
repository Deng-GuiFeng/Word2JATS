"""出口校验入口：内容守恒 + DTD 合法 + 结构自洽。

S6 会在此之上加"定点重问 / 机械修复"循环；当前先做报告，保证渲染后能自检。
"""

from __future__ import annotations

from lxml import etree

from ..validate.checks import run_checks, summarize
from ..validate.validator import Validator
from . import conservation


def verify(xml_bytes, docx_path):
    root = _parse(xml_bytes)
    val = Validator().validate_bytes(xml_bytes)
    cons = conservation.check(docx_path, root)
    issues = run_checks(xml_bytes)
    checks = summarize(issues)

    report = {
        "dtd_ok": val.ok,
        "dtd_errors": val.errors[:12],
        "conservation": {"n_fab": cons["n_fab"], "n_lost": cons["n_lost"],
                         "fabricated": dict(list(cons["fabricated"].items())[:30])},
        "checks": checks,
        "ok": val.ok and cons["n_fab"] == 0
              and not any(i.get("severity") == "error" for i in issues),
    }
    return report


def _parse(xml_bytes):
    parser = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
    return etree.fromstring(xml_bytes, parser)
