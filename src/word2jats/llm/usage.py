"""统一服务端用量口径；不分词、不估算费用，保留原始 usage。"""
from __future__ import annotations


TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens",
                "cache_hit_tokens", "cache_miss_tokens")


def usage_dict(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(vars(value))


def normalize_usage(raw):
    """命中是输入的子集；未命中优先取直返字段，否则由输入减命中。"""
    raw = raw or {}
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}

    def integer(*values):
        return next((v for v in values if type(v) is int and v >= 0), None)

    inp = integer(raw.get("prompt_tokens"), raw.get("input_tokens"))
    out = integer(raw.get("completion_tokens"), raw.get("output_tokens"))
    hit = integer(raw.get("prompt_cache_hit_tokens"), details.get("cached_tokens"),
                  raw.get("cached_tokens"))
    miss = integer(raw.get("prompt_cache_miss_tokens"))
    method = "response" if miss is not None else None
    if miss is None and inp is not None and hit is not None and hit <= inp:
        miss, method = inp - hit, "input_minus_cache_hit"
    errors = []
    if inp is not None and hit is not None and hit > inp:
        errors.append("cache_hit_exceeds_input")
    if None not in (inp, hit, miss) and hit + miss != inp:
        errors.append("cache_partition_mismatch")
    return {
        "input_tokens": inp, "output_tokens": out,
        "total_tokens": integer(raw.get("total_tokens")),
        "cache_hit_tokens": hit, "cache_miss_tokens": miss,
        "cache_miss_method": method, "errors": errors,
    }


def summarize_usage(records):
    """仅汇总真实网络请求，不能从可能重复引用的理解层 audit 中累加。"""
    rejected = [r for r in records if r.get("status") == "failed" and
                r.get("status_code") in (400, 401, 403, 404, 413, 422, 429) and
                r.get("usage") is None]
    normalized = [normalize_usage(r.get("usage")) for r in records if r not in rejected]
    totals = {}
    missing = {}
    for name in TOKEN_FIELDS:
        values = [r[name] for r in normalized]
        missing[name] = sum(v is None for v in values)
        # 缺少任何一次响应用量时保留已知小计，并显式标注完整性。
        totals[name] = sum(v for v in values if v is not None)
    return {
        **totals, "requests": len(records), "missing_fields": missing,
        "rejected_requests": len(rejected),
        "complete": not any(missing.values()) and not any(r["errors"] for r in normalized),
        "inconsistent_requests": sum(bool(r["errors"]) for r in normalized),
        "returned_models": sorted({r["returned_model"] for r in records if r.get("returned_model")}),
        "costs_returned": [
            {"request_id": r.get("request_id"), "fields": {
                k: v for k, v in (r.get("usage") or {}).items()
                if "cost" in k.lower() or k.lower() == "currency"
            }, "response_fields": r.get("response_cost", {})}
            for r in records if r.get("response_cost") or any(
                "cost" in k.lower() for k in (r.get("usage") or {})
            )
        ],
    }
