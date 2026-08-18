"""出口校验入口：内容守恒 + DTD 合法 + 结构自洽。

只渲染一次并自检、产出报告；纠错由确定性上游守卫承担，不做事后向 LLM 重问的循环
（理由见 verify/repair.py 与 docs/04-系统设计.md）。
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
        "blocking_issues": [
            {"code": issue.code, "severity": issue.severity, "detail": issue.detail}
            for issue in issues if issue.severity == "high"
        ],
        "ok": val.ok and cons["n_fab"] == 0
              and not any(issue.severity == "high" for issue in issues),
    }
    return report


def _parse(xml_bytes):
    parser = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
    return etree.fromstring(xml_bytes, parser)
