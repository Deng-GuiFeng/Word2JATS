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
    c.add_argument("--figures", dest="figures_path", default=None,
                   help="外部图片包（zip 或目录），缺省时从 docx 内嵌图片提取")
    c.add_argument("--no-validate", dest="do_validate", action="store_false",
                   help="跳过 DTD 校验")
    c.add_argument("--crossref", action="store_true",
                   help="联网用 CrossRef 补全参考文献(补 DOI/规范刊名,带防错门槛)")
    c.add_argument("--refine", action="store_true",
                   help="智能升级:确定性抽取有缺口时(如没认出作者)让 LLM 补救,需配合 --llm")
    c.add_argument("--agent", action="store_true",
                   help="开启 Agent 视觉闭环(质量核心):渲染页+VLM 视觉核对→发现差异→"
                        "就地修复→循环收敛。需配合 --llm(如 --llm local)")
    c.add_argument("--agent-rounds", type=int, default=3, dest="agent_rounds",
                   help="Agent 闭环最大轮数(默认 3)")
    c.add_argument("--agent-dpi", type=int, default=120, dest="agent_dpi",
                   help="Agent 闭环渲染页 DPI(默认 120,保真优先;评测可降到 84 提速)")
    c.add_argument("--llm", default="off",
                   choices=["off", "local", "deepseek", "dashscope"],
                   help="LLM 语义增强后端（默认 off 纯确定性；local=本机多卡 Qwen VLM 服务）")

    args = parser.parse_args(argv)

    if args.command == "convert":
        opts = ConvertOptions(
            docx_path=args.docx,
            out_dir=args.out_dir,
            journal_id=args.journal_id,
            doi=args.doi,
            figures_path=args.figures_path,
            do_validate=args.do_validate,
            llm=args.llm,
            crossref=args.crossref,
            refine=args.refine,
            agent=args.agent,
            agent_rounds=args.agent_rounds,
            agent_dpi=args.agent_dpi,
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
