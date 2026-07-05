"""word2jats 评测包(权威设计:docs/03-评测/评测体系设计.md)。

三层三对照物(§2):
  L0 合法  validity.py   对 JATS 规矩(DTD + 出版规范规则),不看参考
  L1 忠实  fidelity.py   对源 docx(词多重集守恒 + 图片 md5),守"只加结构不改内容"
  L2 对位  structure.py  对 结构参考.xml(归一化 → 按语义键对齐 → 逐类命中/缺陷)

单一路径:样例登记 samples.py / 归一化 normalize.py / 报告 report.py / 一键 run.py / 消融 ablation.py。
评测器 100% 确定性、不含 LLM(§3.1);无加权总分,产物 = 缺陷清单 + 分类命中率(§3.7)。
对照物永远是外部冻结的 结构参考.xml,绝不用本系统历史输出当参考。
"""
