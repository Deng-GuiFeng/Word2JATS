"""将一次完整验证的响应复制到新的演示缓存，不覆盖旧缓存。"""
from pathlib import Path
import argparse
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, action="append", help="可重复指定，合并不同模型的完整响应")
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = [(root / "reports/_llm_cache" / tag).resolve() for tag in args.tag]
    for source in sources:
        source.relative_to(root / "reports/_llm_cache")
        if not source.is_dir():
            raise FileNotFoundError(source)
    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=False)
    count = 0
    for file in sorted(file for source in sources for file in source.glob("*/*.json")):
        dest = target / file.name
        if dest.exists():
            if dest.read_bytes() != file.read_bytes():
                raise ValueError(f"同一请求存在不同回答：{file.name}")
        else:
            shutil.copy2(file, dest)
            count += 1
    if not count:
        raise ValueError("没有可用响应")
    print(f"已复制 {count} 个响应到 {target}")


if __name__ == "__main__":
    main()
