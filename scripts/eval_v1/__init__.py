"""word2jats 第一代评测器(权威设计:docs/06-评测与成绩.md)。

三层三对照物(§2):
  L0 合法  validity.py   对 JATS 规矩(DTD + 出版规范规则),不看金标准
  L1 忠实  fidelity.py   对源 docx(词多重集守恒 + 图片存在性),守"只加结构不改内容"
  L2 对位  structure.py  对金标准(结构参考.xml + figures.zip;归一化 → 按语义键对齐 → 逐类缺陷)

单一路径:样例登记 samples.py / 归一化 normalize.py / 报告 report.py / 一键 run.py。
评测器 100% 确定性、不含 LLM(§3.1);无加权总分,产物 = 缺陷清单 + 分类命中率(§3.7)。
对照物永远是外部冻结的金标准,绝不用本系统历史输出当参考。

**与 V2 平级,不分主次。** `scripts/eval_v2` 是另一套独立实现(完整语义树严格比对),
两者判分逻辑互不引用,同时评估同一批转换输出;只共用 `样例数据/样例登记.json` 这一份
样例名单(名单是数据,不是判分规则)。两器数字不可相加也不可直接对照,理由见
`scripts/evalsuite.py` 的模块说明。一条命令跑完两器:`python -m scripts.evalsuite`。
"""
