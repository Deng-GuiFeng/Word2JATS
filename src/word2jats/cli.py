"""命令行入口：``python -m word2jats convert <docx> ...``"""

from __future__ import annotations

import argparse
import sys

from .pipeline import ConvertOptions, convert


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="word2jats",
        description="学术论文 Word(docx) → JATS 1.3 XML 自动转换",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="转换单个 docx 为 JATS XML")
    c.add_argument("docx", help="输入 docx 路径")
    c.add_argument("-o", "--out-dir", default="output", help="输出目录（默认 output）")
    c.add_argument("--journal", dest="journal_id", default=None,
                   help="期刊 id（如 RCM/JIN/HSF），缺省时尝试从 DOI 推断")
    c.add_argument("--doi", default=None, help="文章 DOI，如 10.31083/JIN49347")
    c.add_argument("--no-validate", dest="do_validate", action="store_false",
                   help="跳过出口校验（DTD + 内容守恒 + 结构自洽）")
    c.add_argument("--llm", default="dashscope",
                   choices=["dashscope", "local", "deepseek"],
                   help="理解层模型后端（方法必需；默认 dashscope=阿里云百炼 qwen3.7-plus）")

    args = parser.parse_args(argv)

    if args.command == "convert":
        opts = ConvertOptions(
            docx_path=args.docx,
            out_dir=args.out_dir,
            journal_id=args.journal_id,
            doi=args.doi,
            do_validate=args.do_validate,
            llm=args.llm,
        )
        res = convert(opts)
        print("✓ 已生成: %s" % res.xml_path)
        print("  文章号: %s" % res.article_id)
        for k, v in res.stats.items():
            print("  %-18s %s" % (k, v))
        if res.validation is not None:
            v = res.validation
            status = "通过" if v.ok else "未通过"
            print("  DTD 校验: %s (well_formed=%s, dtd_valid=%s)" %
                  (status, v.well_formed, v.dtd_valid))
            for e in v.errors[:12]:
                print("    - %s" % e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
