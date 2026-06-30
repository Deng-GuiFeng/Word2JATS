#!/usr/bin/env python
"""命令行驱动本地多模态大模型(qwen36_deploy 的 sglang,GPU)。

供伪标签生成 Team 的各角色代理调用本地模型做抽取/核验(文本 + 视觉)。
用法:
  echo '<user prompt>' | python scripts/local_llm.py --system '<system>' [--image path] [--json] [--max-tokens N]
  python scripts/local_llm.py --system '...' --user '...' [--image ...] [--json]
输出:模型回复(--json 时为解析后紧凑 JSON;失败打印 NULL)。
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from word2jats.llm.client import LLMClient  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default="你是严谨的学术文档信息抽取与核验助手。只依据给定材料,不编造。")
    ap.add_argument("--user", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()
    user = args.user if args.user is not None else sys.stdin.read()

    llm = LLMClient(provider="local")
    if not llm.enabled:
        print("NULL: local LLM 未就绪(检查 sglang 服务 :30000)", file=sys.stderr)
        sys.exit(2)

    if args.image:
        if args.json:
            data = llm.extract_json(args.system, user, image_path=args.image,
                                    max_tokens=args.max_tokens) if _supports_img_json(llm) \
                else _vision_json(llm, args.system, user, args.image, args.max_tokens)
            print(json.dumps(data, ensure_ascii=False) if data else "NULL")
        else:
            print(llm.chat_vision(args.system + "\n" + user, args.image) or "NULL")
    else:
        if args.json:
            data = llm.extract_json(args.system, user, max_tokens=args.max_tokens)
            print(json.dumps(data, ensure_ascii=False) if data else "NULL")
        else:
            txt = llm.extract_json(args.system, user, max_tokens=args.max_tokens)
            print(json.dumps(txt, ensure_ascii=False) if txt else "NULL")


def _supports_img_json(llm):
    return False  # extract_json 不一定支持 image_path;统一走 _vision_json


def _vision_json(llm, system, user, image, max_tokens):
    raw = llm.chat_vision(system + "\n" + user + "\n只输出 JSON,不要解释。", image)
    if not raw:
        return None
    import re
    m = re.search(r"\{.*\}|\[.*\]", raw, re.S)
    try:
        return json.loads(m.group() if m else raw)
    except Exception:
        return {"_raw": raw}


if __name__ == "__main__":
    main()
