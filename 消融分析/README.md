# 消融分析

本目录是 word2jats 消融分析的**审计留痕**：记录每个消融臂的评测结果、逐例成本（输入/输出 tokens）与跨臂汇总，供复核。方法与结论对应 [`docs/06-评测与成绩.md`](../docs/06-评测与成绩.md) 的评测口径。

## 消融的目的（先钉死，避免"为消融而消融"）

本系统是**单一方法链**（docx→parse→understand(LLM 判结构)→render→verify），不是"多个可独立增删的组件叠成一个指标"。所以我们不做"把 parse/render 挨个关掉数缺陷"这种对方法链无意义的机械消融，只做两类**能改变真实判断**的消融：

1. **部署维度**——换不同模型、不同超参数，回答落地铁问："方法是否绑死单一厂商/单一模型？换更弱/更便宜/可私有化的模型能不能扛？成本多少？" 这直接服务评分的**落地性（40%）**。
2. **模块设计有效性**——用 monkeypatch 逐个拆掉**可分离**的设计模块（出口守卫、三个理解 pass、长参考二分、书籍二级 pass、机械修复），看质量与**编造率**如何变，回答"每个设计模块到底值不值、是承重墙还是可删冗余"。这服务**创新性（30%）**与工程可维护性。

## 三个核心指标

| 指标 | 含义 |
|---|---|
| **defect_total** | 剩余缺陷 = L0 错误 + L1 忠实 + L2 对位（对照冻结 `结构参考.xml`，越小越好，0=完美） |
| **n_fab（安全不变量）** | L1 编造词数。本方法声称"正文按源块 idx 从 docx 取回、物理杜绝编造"，**各臂看它是否恒 0**。它对 docx 词多重集算、**不依赖金标准（gold-free）**，故在 held-out 上也能诚实测量 |
| **输入/输出 tokens** | 逐例成本。部署维度换模型时的真实计费口径 |

## 消融臂清单

### 部署维度（deploy）——每模型搜最佳温度，再拿各自最好成绩横向比

**目的**：把每个模型放进本转换器，找出它**自己最佳超参数下**的表现，再横向比。重点是模型，超参只是手段。

**方法（几经修正，如实记录）**：

- **思考固定关**，依据是**成本**（思考输出 token 是非思考的 2–6 倍）。首轮曾据"缺陷"说思考质量差，后发现那是被我自设的"超时"（客户端单次调用上限，通义 240 秒）掐断污染——思考单次要吐长思维链、超时被截断致结构残缺，已撤回该结论。
- **只调温度一个旋钮**——temp 和 top_p 作用重叠，多数模型官方建议只调其一（Qwen 例外，同时给两值）；故 top_p 用各模型官方推荐值（Qwen 系 0.8、DeepSeek 默认）、不参与搜索，温度网格 {0, 0.3, 0.7}。**temp=0 是贪心解码，top_p/top_k 自动失效。**
- **temp>0 非确定，每个温度点跑 3 个种子取均值 + 记逐种子波动**（temp=0 确定，跑一次即可）。

各模型官方推荐超参（调研核实，带出处）：

| 模型 | 非思考推荐 t/top_p | 确定性场景 | 默认思考 |
|---|---|---|---|
| qwen3.7-plus / max | 0.7 / 0.8 | 无 t=0 背书 | 开 |
| deepseek-v4-pro / flash | 通用 1.0 | **t=0.0**；思考模式忽略采样 | 开 |
| Qwen3.6-35B-A3B（本地） | 0.7 / 0.8 | 无专门建议 | 开 |

出处：`help.aliyun.com/zh/model-studio/{qwen-api-via-dashscope,deep-thinking}`、`huggingface.co/Qwen/{Qwen3-32B,Qwen3.6-35B-A3B}`、`api-docs.deepseek.com/{guides/thinking_mode,quick_start/parameter_settings}`、`docs.vllm.ai`（temp=0 贪心时 top_p 失效）。

**臂**：5 个模型（qwen3.7-plus / qwen3.7-max / deepseek-v4-pro / deepseek-v4-flash / 本地 Qwen3.6-35B-A3B）× 温度 {0, 0.3, 0.7}，思考全关。temp0 复用现有臂，temp0.3/0.7 为 `T-<模型>-<温度>`（各 3 种子）。另有 `floor-llm-off`（关 LLM 基线）与 `model-deepseek-v4-pro/flash`（思考模式，仅作参考、已知贵且被超时污染）。逐模型结果与横向比见 [`结论.md`](结论.md)。

### 模块设计有效性（module，复用基线模型缓存，只看缺陷不看成本）

| 臂 | 拆掉什么 | 回答什么 |
|---|---|---|
| `mod-guards-off` | 出口守恒守卫（参考字段子串校验 + 关键词标题门控） | 守卫拦下多少编造？承重墙还是冗余 |
| `mod-front-off` | front 理解 pass | 前置区（题名/作者/摘要）判定的贡献 |
| `mod-body-off` | body 理解 pass | 正文分节判定的贡献 |
| `mod-refs-off` | refs 理解 pass | 参考文献切分的贡献 |
| `mod-bisection-off` | 长参考自适应二分 | 是死重量还是对超长综述必要（可简化审计） |
| `mod-bookfields-off` | 书籍参考二级 pass | 书籍字段建模的贡献（主要 03/04） |
| `mod-repair-off` | 渲染末机械修复（悬空 xref/空表行） | 最后一道确定性防线的负载 |

## 诚实报告纪律（反过拟合的可视化承诺）

这是本消融**抗质疑的命根子**，比数字本身更重要：

1. **gold-free 指标可全 10 例合并报，L2 才需分 held-out。** `n_fab`、DTD 错误、L1（对 docx 词多重集）**只需 docx、不需金标准**，天然免疫"对训练可见的金标准刷分"的同域循环，故全 10 例合并即可。只有 L2 对位（比金标准 `结构参考.xml`）的 delta 才必须区分**训练可见（01–05，我方迭代过）**与 **held-out（S01–05，只给 docx）**。
2. **克制统计推断。** 模块组与各模型 temp0 点是确定的（temp=0+缓存+逐字节复现），是**精确计数**、不做均值/CI/显著性。部署组 temp>0 因非确定，报的是 **3 种子均值 + 逐种子波动**（方括号）——种子太少，只用于看"大差距是否真"（如 qwen-max temp0.3=88 vs temp0=140），**不做置信区间/显著性**，边际差异（如 deepseek 170 vs 178）明确视为噪声内。
3. **泛化靠机制、不靠数字。** 真正干净的 held-out 在本项目并不存在（S01–05 的结构参考是本队自建）。方法的通用性论证靠"判据是 `w:tblHeader`/`isalpha`/`idx 取回` 这类出版方无关的 OOXML/JATS 硬信号"，而非 held-out 上的 delta 数字。

## 目录结构与复现

```
消融分析/                       ← 审计留痕（入库）
├── README.md                   本文件：方法与臂定义
├── arms/<arm>.json             每个臂：逐例 {缺陷分层, n_fab, in/out tokens, 耗时, 调用数}
├── 汇总-质量.md / 汇总.json      跨臂质量对比（缺陷 + n_fab + DTD）
├── 汇总-成本.md                 跨臂成本对比（逐例 输入/输出 tokens）
└── 结论.md                     人读的分析结论
reports/ablation/               ← 转换产物（gitignored，可再生）
├── _cache/<cache_as>/<sample>/ LLM 磁盘缓存（按臂隔离，防串味）
└── <arm>/<sample>/             各臂各例的输出 XML + 外部化图片
```

复现（须用项目 `.venv`，DTD 校验需要）：

```bash
# 部署组·每模型温度搜索（云端；temp0 复用，temp0.3/0.7 各 3 种子）
PYTHONPATH=scripts .venv/bin/python -m eval.ablation --arms \
  model-qwen3.7-plus,model-qwen3.7-max,deepseek-v4-pro-nothink,deepseek-v4-flash-nothink,\
T-plus-0.3,T-plus-0.7,T-max-0.3,T-max-0.7,T-dspro-0.3,T-dspro-0.7,T-dsflash-0.3,T-dsflash-0.7,floor-llm-off
# 本地 Qwen3.6（单卡 sglang，用当时空闲的 GPU）：
#   SGLANG_DISABLE_CUDNN_CHECK=1 CUDA_VISIBLE_DEVICES=<空闲卡号> conda run -n qwen36_blkw \
#     python -m sglang.launch_server --model-path <Qwen3.6> --tp-size 1 --port 30000 ...
PYTHONPATH=scripts .venv/bin/python -m eval.ablation --arms model-qwen3.6-local,T-local-0.3,T-local-0.7
# 模块组（复用基线缓存，快）
PYTHONPATH=scripts .venv/bin/python -m eval.ablation --arms module
# 汇总
PYTHONPATH=scripts .venv/bin/python -m eval.ablation --summarize
```
