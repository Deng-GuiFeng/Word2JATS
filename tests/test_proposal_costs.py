"""报告费用只使用实际用量分项；输入总量不重复收费。"""
from decimal import Decimal

import pytest

from scripts.proposal_tables import usage_cost


def usage(miss=1_000_000, hit=1_000_000, output=1_000_000):
    return dict(cache_miss_tokens=miss, cache_hit_tokens=hit, output_tokens=output,
                input_tokens=miss + hit, total_tokens=miss + hit + output)


@pytest.mark.parametrize('name,expected', [('Qwen','8.32'),('DeepSeek','10.04')])
def test_three_disjoint_billing_categories(name, expected):
    assert usage_cost(usage(), name) == Decimal(expected)


def test_small_usage_not_rounded_before_aggregation():
    small = usage(1, 1, 1)
    assert sum(usage_cost(small, 'Qwen') for _ in range(10)) == usage_cost(usage(10,10,10), 'Qwen')
    assert usage_cost(usage(0,0,0), 'DeepSeek') == 0


@pytest.mark.parametrize('field,value', [('input_tokens',1),('total_tokens',1),
                                      ('output_tokens',None),('cache_hit_tokens',-1),
                                      ('cache_miss_tokens',1.5)])
def test_invalid_or_incomplete_usage_is_not_priced(field,value):
    record = usage()
    record[field] = value
    with pytest.raises(ValueError):
        usage_cost(record, 'Qwen')
