#!/usr/bin/env python
"""把两个独立核验角色(A/B)的标签对账:一致→采纳(可信),不一致→列为冲突待裁决。

用法: python scripts/reconcile_labels.py <A.json> <B.json> -o <final.json>
输出:final.json(A==B 的字段直接采纳;冲突字段标 __CONFLICT__)+ 打印冲突清单。
"""
import argparse
import json


def norm(x):
    if isinstance(x, str):
        return " ".join(x.split()).lower()
    if isinstance(x, list):
        return [norm(i) for i in x]
    if isinstance(x, dict):
        return {k: norm(v) for k, v in sorted(x.items())}
    return x


def eq(a, b):
    return norm(a) == norm(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    A = json.load(open(args.a, encoding="utf-8"))
    B = json.load(open(args.b, encoding="utf-8"))

    final, conflicts = {}, []
    keys = set(A) | set(B)
    for k in sorted(keys):
        if k == "low_confidence":
            continue
        va, vb = A.get(k), B.get(k)
        if eq(va, vb):
            final[k] = va
        else:
            # 列表:逐元素对账,定位到具体下标
            if isinstance(va, list) and isinstance(vb, list):
                if len(va) != len(vb):
                    conflicts.append({"field": k, "A": va, "B": vb, "note": "长度不同"})
                else:
                    for i, (ea, eb) in enumerate(zip(va, vb)):
                        if not eq(ea, eb):
                            conflicts.append({"field": "%s[%d]" % (k, i), "A": ea, "B": eb})
                final[k] = va  # 暂用 A,冲突项需裁决
            else:
                conflicts.append({"field": k, "A": va, "B": vb})
                final[k] = "__CONFLICT__"
    # 合并双方 low_confidence
    lc = (A.get("low_confidence") or []) + (B.get("low_confidence") or [])
    final["low_confidence"] = lc
    final["_n_conflicts"] = len(conflicts)

    json.dump(final, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"out": args.out, "n_conflicts": len(conflicts),
                      "conflicts": conflicts, "low_confidence": lc}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
