"""一条命令起服务：`python -m webapp`（或指定端口 `--port 8000`）。

需先装依赖：`pip install fastapi uvicorn python-multipart`（已在 requirements.txt）。
需 .env 配所选模型的 API Key。默认监听 127.0.0.1:8000。
"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Word2JATS Web 应用")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="改代码自动重启（开发用）")
    args = ap.parse_args()
    uvicorn.run("webapp.app:app", host=args.host, port=args.port,
                reload=args.reload)


if __name__ == "__main__":
    main()
