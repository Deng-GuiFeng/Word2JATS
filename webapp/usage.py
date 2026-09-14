"""区分生成分析结果的实际用量与本次操作新增用量。"""
from word2jats.llm.usage import TOKEN_FIELDS, summarize_usage


def public_usage(row):
    network = list(row.get("usage_records") or [])
    reused = list(row.get("reused_usage_records") or [])
    incremental = dict(row.get("usage") or summarize_usage(network))
    # 旧快照没有复用响应的身份与用量，不能把已知的新增零当成总用量。
    missing = max(0, int(row.get("cache_hits") or 0) - len(reused))
    if network or reused or missing:
        records, seen = [], set()
        for index, record in enumerate(network + reused):
            request_id = record.get("request_id")
            identity = ((record.get("provider"), record.get("model"), "request", request_id)
                        if request_id else
                        (record.get("provider"), record.get("model"), "cache", record["cache_key"])
                        if record.get("cache_key") else ("record", index))
            if identity not in seen:
                records.append(record)
                seen.add(identity)
        records.extend({"usage": None, "status": "reused"} for _ in range(missing))
        total = summarize_usage(records)
        # 兼容只存汇总的新调用快照；没有逐调用记录时保留已经计量的新增小计。
        if not network and incremental.get("requests", 0):
            for name in TOKEN_FIELDS:
                total[name] += incremental.get(name) or 0
            total["requests"] += incremental["requests"]
            total["complete"] &= incremental.get("complete", False)
        total["available"] = any(record.get("usage") for record in records) or bool(incremental.get("requests"))
    else:
        total = dict(incremental)
        total["available"] = bool(total.get("requests"))
    return {"result_usage": total, "incremental_usage": incremental,
            "reused_responses": int(row.get("cache_hits") or 0)}
