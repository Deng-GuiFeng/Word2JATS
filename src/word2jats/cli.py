"""命令行入口：``python -m word2jats convert <docx> ...``"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .pipeline import ConvertOptions, convert


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="word2jats",
        description="Word2JATS：学术论文 Word 稿件（.docx）→ JATS 1.3 XML",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="将 Word 稿件转换为 JATS XML")
    c.add_argument("docx", help="Word 稿件路径（.docx）")
    c.add_argument("-o", "--out-dir", default="output", help="输出目录（默认 output）")
    c.add_argument("--journal", dest="journal_id", default=None,
                   help="期刊 id（如 RCM/JIN/HSF），缺省时尝试从 DOI 推断")
    c.add_argument("--doi", default=None, help="文章 DOI，如 10.31083/JIN49347")
    c.add_argument("--publication-year", default=None,
                   help="出版工作流明示的四位出版年")
    c.add_argument("--publisher-note", dest="include_publisher_note", action="store_true",
                   default=None, help="显式生成出版社声明")
    c.add_argument("--no-publisher-note", dest="include_publisher_note", action="store_false",
                   help="显式不生成出版社声明")
    c.add_argument("--no-validate", dest="do_validate", action="store_false",
                   help="跳过出口校验（DTD + 内容守恒 + 结构自洽）")
    c.add_argument("--llm", default="dashscope",
                   choices=["dashscope", "local", "deepseek"],
                   help="理解层模型后端（方法必需；默认 dashscope=阿里云百炼 qwen3.7-plus）")
    c.add_argument("--max-workers", type=int, default=32,
                   help="理解任务及进程级在线模型并发上限（默认 32）")
    c.add_argument("--input-token-budget", type=int, default=90_000,
                   help="单个理解窗口的输入 token 安全上限")
    c.add_argument("--boundary-token-budget", type=int, default=4_000,
                   help="切窗时每侧边界上下文的 token 安全上限")
    c.add_argument("--output-token-budget", type=int, default=128_000,
                   help="单次模型输出上限（默认 128000）")
    c.add_argument("--llm-timeout", type=float, default=None,
                   help="流式连接各 HTTP 阶段的等待上限；默认不设无依据的客户端截止时间")
    c.add_argument("--llm-transport-retries", type=int, default=2,
                   help="超时、限流和服务端故障的最大传输重试次数")
    c.add_argument("--llm-retry-backoff", type=float, default=1.0,
                   help="传输重试的初始退避秒数")
    c.add_argument("--llm-retry-backoff-max", type=float, default=8.0,
                   help="本地生成的传输重试退避上限秒数")

    args = parser.parse_args(argv)

    if args.command == "convert":
        opts = ConvertOptions(
            docx_path=args.docx,
            out_dir=args.out_dir,
            journal_id=args.journal_id,
            doi=args.doi,
            publication_year=args.publication_year,
            include_publisher_note=args.include_publisher_note,
            do_validate=args.do_validate,
            llm=args.llm,
            max_workers=args.max_workers,
            input_token_budget=args.input_token_budget,
            boundary_token_budget=args.boundary_token_budget,
            output_token_budget=args.output_token_budget,
            llm_timeout=args.llm_timeout,
            llm_transport_retries=args.llm_transport_retries,
            llm_retry_backoff=args.llm_retry_backoff,
            llm_retry_backoff_max=args.llm_retry_backoff_max,
        )
        res = convert(opts)
        if res.delivered:
            print("✓ 转换完成: %s" % res.xml_path)
        else:
            print("✗ 转换结果需要处理: %s" % res.candidate_xml)
            print("  检查报告: %s" % (Path(res.candidate_dir).parent / "report.json"))
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
        return 0 if res.delivered else 1


if __name__ == "__main__":
    sys.exit(main())
