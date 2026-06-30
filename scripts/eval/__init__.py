"""评测包:对照权威标签(金标准 XML / 伪标签 JSON)做诚实的正确性评估。

两层:
- Layer-1(profile + compare):全面、可定义、确定性的逐维度指标。
- Layer-2(judge,见 scripts/eval_judge.py + Workflow):每输出派独立 subagent 逐字对标签审查。

核心纪律:参考目标永远是**外部权威标签**(委员会金标准 / 独立核验伪标签),
绝不用本系统自己的历史输出当参考(那是"复现自己的错")。
"""
