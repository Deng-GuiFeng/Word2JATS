"""Validator 线程安全回归测试。

历史 bug：Validator 每次都 os.chdir 切目录加载 DTD，chdir 改进程全局 cwd，Web 应用
并发跑 convert() 时互相踩 → DTD 子模块加载失败 → 假报表格元素"未声明"、DTD 不通过。
本测试并发校验同一份 DTD 合法的 XML，断言不出现假失败、结果一致。
"""

import concurrent.futures as cf
from pathlib import Path

import pytest

from word2jats.validate.validator import Validator

ROOT = Path(__file__).resolve().parent.parent
# 冻结的金标准结构参考本身 DTD 合法（docs/06），用作稳定的合法输入
GOLD = ROOT / "样例数据" / "01" / "结构参考.xml"


@pytest.mark.skipif(not GOLD.exists(), reason="缺金标准样例")
def test_concurrent_validation_no_false_failure():
    xml_bytes = GOLD.read_bytes()
    # 先单线程确认这份 XML 确实 DTD 合法（否则测试前提不成立）
    base = Validator().validate_bytes(xml_bytes)
    assert base.well_formed
    if not base.dtd_valid:            # 环境 DTD 加载不了则跳过（非本测试关注点）
        pytest.skip("本环境 DTD 未加载：%s" % base.errors[:1])

    def one(_):
        r = Validator().validate_bytes(xml_bytes)
        return r.dtd_valid, len(r.errors)

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(one, range(64)))

    assert all(ok for ok, _ in results), "并发校验出现假 DTD 失败（线程安全回归）"
    assert all(n == 0 for _, n in results)
