"""施工图阶段 0 的确定性安全回归测试。"""

from types import SimpleNamespace

from word2jats.validate.checks import Issue
from word2jats.verify import verify as verify_module


_MINIMAL_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<article><front/><body><p>text</p></body></article>
"""


def test_verify_high_issue_is_a_real_gate(monkeypatch):
    """阶段 0.1：结构检查的 high 问题必须使出口报告失败。"""
    monkeypatch.setattr(
        verify_module,
        "Validator",
        lambda: SimpleNamespace(validate_bytes=lambda _: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        verify_module.conservation,
        "check",
        lambda *_: {"n_fab": 0, "n_lost": 0, "fabricated": {}},
    )
    monkeypatch.setattr(
        verify_module,
        "run_checks",
        lambda _: [Issue("duplicate_id", "high", "id 重复")],
    )

    report = verify_module.verify(_MINIMAL_XML, "unused.docx")

    assert report["ok"] is False
    assert report["checks"] == {"high": 1}
    assert report["blocking_issues"] == [
        {"code": "duplicate_id", "severity": "high", "detail": "id 重复"}
    ]


def test_verify_medium_issue_does_not_block_delivery(monkeypatch):
    """阶段 0.1：medium 诊断留在报告中，但不冒充 high 硬门。"""
    monkeypatch.setattr(
        verify_module,
        "Validator",
        lambda: SimpleNamespace(validate_bytes=lambda _: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        verify_module.conservation,
        "check",
        lambda *_: {"n_fab": 0, "n_lost": 0, "fabricated": {}},
    )
    monkeypatch.setattr(
        verify_module,
        "run_checks",
        lambda _: [Issue("no_authors", "medium", "没有作者")],
    )

    report = verify_module.verify(_MINIMAL_XML, "unused.docx")

    assert report["ok"] is True
    assert report["checks"] == {"medium": 1}
    assert report["blocking_issues"] == []
