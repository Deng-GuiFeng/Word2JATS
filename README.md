# Word2JATS：面向学术出版的 Word 智能结构化转换

将 Word 学术论文（.docx）转换为 JATS 1.3 XML 与配套图片。提供命令行和 Web 两种入口；网页支持原稿对照、结构导航、文章信息修改、检查结果查看和成果下载。

## 安装与运行

需要 Python 3.10+。以下命令适用于 Linux / macOS，请在项目根目录执行。本机不需要 GPU；新文件的结构识别需要大语言模型服务。

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

已有 `.env` 时不要覆盖。填写需要使用的服务密钥：Qwen 对应 `DASHSCOPE_API_KEY`，DeepSeek 对应 `DEEPSEEK_API_KEY`。

```bash
.venv/bin/python -m webapp
```

打开 http://127.0.0.1:8000，选择大模型服务并上传 Word，转换后查看结果、按需修改并下载。期刊和 DOI 可在上传时指定，也可在结果页补充。已部署入口：https://word2jats.jianglab.work。

命令行示例：

```bash
PYTHONPATH=src .venv/bin/python -m word2jats convert \
  样例数据/03/初始文件.docx --journal JIN --doi 10.31083/JIN49347 \
  --llm deepseek -o output/
```

## 项目目录

| 目录 | 内容 |
|---|---|
| `src/word2jats/` | 转换器、提示词、期刊配置、DTD 和公式转换资源 |
| `webapp/` | Web 服务、页面及预览资源 |
| `tests/` | 转换器和网页回归测试、必要的固定测试数据 |
| `scripts/eval_v2/` | 独立的输出评价工具 |
| `docs/` | [系统设计](docs/系统设计.md)、[数据与评测](docs/数据与评测.md)、[运行与部署](docs/运行与部署.md) |
| `references/` | 赛事通知、介绍、样例包、基线代码及 JATS 手册 |
| `样例数据/` | 14 例输入、结构参考和附件 |
| `archives/` | 初赛提交 ZIP、决赛提交 ZIP、最终答辩 PPTX |
| `runtime/` | 本机缓存、任务和分片上传，不纳入 Git |

提交 ZIP 保留当时的交付版本，当前源码包含之后完成的改进。ZIP 中含供评审测试的配置，勿直接公开发布。

`references/`、`archives/` 中的 ZIP、`.env` 和 `runtime/` 仅保存在本机，不随 Git 推送；从仓库克隆即可安装运行，但需自行配置密钥，不附带已有任务和请求缓存。

## 测试

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

前端会话测试需要 Node.js。14 例双配置集成测试只读取本地 `runtime/cache`，禁止网络调用；未携带该缓存的源码副本会跳过这组测试，其余测试使用固定数据。

## 使用边界

网页可修改文章信息和出版信息，不是全文排版编辑器。复杂表格、公式或正文识别问题应对照原 Word 处理；结构参考和自动检查都不能替代内容判断。部署者需自行管理访问权限、服务额度和稿件留存。

源码许可见 [LICENSE](LICENSE)，第三方资源保留其随附许可说明。
