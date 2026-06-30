#!/usr/bin/env python
"""**独立**的本地模型调用(直连 sglang OpenAI 接口),不依赖 word2jats。

用法:
  echo 'prompt' | python scripts/label/ask.py [--system S] [--image path] [--max-tokens N]
"""
import argparse
import base64
import sys
from openai import OpenAI

URL = "http://localhost:30000/v1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default="你是严谨的学术文档信息抽取与核验助手,只依据给定材料,不编造。")
    ap.add_argument("--user", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()
    user = args.user if args.user is not None else sys.stdin.read()

    client = OpenAI(base_url=URL, api_key="none")
    model = client.models.list().data[0].id

    if args.image:
        with open(args.image, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content = [{"type": "text", "text": user},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]
    else:
        content = user

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": args.system},
                  {"role": "user", "content": content}],
        temperature=0, max_tokens=args.max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    print(resp.choices[0].message.content or "NULL")


if __name__ == "__main__":
    main()
