"""评测器自身的回归测试(此前评测无 pytest 覆盖)。

锁定四条不变量,任何改动破坏其一即报:
1. 参考 vs 自身 → L2 零缺陷(比对器自洽,无假阳)。
2. 覆盖守门 → 零未覆盖(参考每种元素都有明确处置,无静默漏检)。
3. 参考自身 → L0 零 error(参考是合法 JATS 1.3)。
4. L2 确定性:同输入两跑结果完全一致(无 AI/无随机进回路)。
外加:scope.json 存在且其 B 档清单与实测参考一致。
"""
import json
import os

import pytest

from eval import samples as S
from eval import structure, validity

ALL = S.SAMPLES
IDS = [s.key for s in ALL]


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_reference_self_compare_zero(smp):
    """参考与自身对位应零缺陷(含覆盖守门零未覆盖)。"""
    r = structure.run(smp, smp.ref_xml)
    assert r["defect_n"] == 0, "自比出现缺陷: %s" % [
        (c["cat"], c["defects"][:2]) for c in r["by_category"] if c["defects"]]
    cov = [c for c in r["by_category"] if c["cat"] == "覆盖守门"][0]
    assert not cov["defects"], "覆盖守门发现未覆盖元素: %s" % [d["key"] for d in cov["defects"]]


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_reference_L0_no_error(smp):
    """冻结的参考必须过 L0(0 error;warning/info 允许)。"""
    res = validity.check(smp.ref_xml)
    errs = [v for v in res["violations"] if v["severity"] == "error"]
    assert res["ok"] and not errs, "参考 L0 出错: %s" % errs[:3]


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_L2_deterministic(smp):
    """L2 两跑逐字节一致(确定性,无随机/无 AI)。"""
    a = structure.run(smp, smp.ref_xml)
    b = structure.run(smp, smp.ref_xml)
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == \
        json.dumps(b, sort_keys=True, ensure_ascii=False)


@pytest.mark.parametrize("smp", ALL, ids=IDS)
def test_scope_present_and_consistent(smp):
    """scope.json 存在,且其记录的 B 档清单与实测参考一致(防冻结记录过时)。"""
    assert os.path.exists(smp.scope_json), "缺 scope.json"
    sc = json.load(open(smp.scope_json, encoding="utf-8"))
    l2 = structure.run(smp, smp.ref_xml)
    ref_bnet = {k: v["ref"] for k, v in l2["b_coverage"].items()}
    assert sc["B_network_inventory"] == ref_bnet, \
        "scope 记录 %s ≠ 实测 %s" % (sc["B_network_inventory"], ref_bnet)
