# word2jats 可运行原型

本原型把学术论文 Word 文件（`.docx`）自动转换为符合 **JATS Journal Publishing DTD 1.3** 的 XML，并将文中图片外部化。系统采用“大模型判断结构、程序确定性渲染”的方式：模型只返回段落和字段的结构索引，正文内容始终从原 Word 回填；输出前执行内容守恒、DTD 合法性和结构自洽检查。

## 1. 赛事验收入口

- **在线验收：** 前端已同步部署至 <https://word2jats.jianglab.work>，赛事委员会可直接访问并使用本项目自带的示例文件测试。
- **本地验收：** 项目根目录的 `.env` 已预置**余额 10 元的赛事委员会验收专用 API Key**，无需评审人员另行申请或填写密钥，可按下文步骤直接启动。

验收专用 Key 仅用于本项目赛事评审，请勿公开传播或用于其他用途。API Key 只由后端读取，不会发送到浏览器或写入转换结果。

## 2. 环境要求

- Python 3.12（推荐，提交版已在 Python 3.12.7 验证）
- 可访问阿里云百炼 DashScope 的网络
- 有效的 DashScope API Key（提交版 `.env` 已提供验收专用 Key）

也可使用 Docker 运行，无需本机 Python 环境。

## 3. 配置

赛事提交版无需修改配置，项目根目录的 `.env` 已填写验收专用配置。如需替换为自有 Key，只需编辑以下两项：

```dotenv
DASHSCOPE_API_KEY=<替换为自有密钥>
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

默认模型为 `qwen3.7-plus`。README 和前端均不展示实际密钥。没有有效密钥时只能生成空的 JATS 骨架，不能作为有效转换结果。

## 4. 本地运行

Linux / macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m webapp
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m webapp
```

浏览器打开 <http://127.0.0.1:8000>，上传 `示例输入/初始文件.docx`。示例对应期刊选择 `HSF`，DOI 填 `10.31083/HSF49106`。转换完成后可查看：

- 按期刊样式渲染的文章预览；
- JATS XML 原文与结构摘要；
- DTD 和结构检查结果；
- 输出内容与源 Word 的忠实度核对；
- XML 与外部化图片组成的 ZIP 交付包。

首次转换需调用大模型，通常约需 1–2 分钟，具体取决于网络、模型队列和稿件复杂度；相同请求会使用本地缓存。

## 5. 命令行运行

Linux / macOS：

```bash
PYTHONPATH=src .venv/bin/python -m word2jats convert \
  "示例输入/初始文件.docx" \
  --journal HSF \
  --doi 10.31083/HSF49106 \
  -o output
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python -m word2jats convert `
  "示例输入/初始文件.docx" `
  --journal HSF `
  --doi 10.31083/HSF49106 `
  -o output
```

输出目录包含 `<文章号>.xml` 和从 Word 提取的图片。终端会同时打印作者、参考文献、图表、公式、交叉引用和校验统计。

## 6. Docker 运行

```bash
docker build -t word2jats .
docker run --rm --env-file .env -p 8000:8000 word2jats
```

浏览器打开 <http://127.0.0.1:8000>。`.env` 不会被复制进镜像，密钥由 `--env-file` 在运行时注入。

## 7. 项目结构

```text
可运行原型/
├── src/word2jats/       核心转换引擎与 JATS 1.3 DTD 等资源
├── webapp/              FastAPI 后端、原生 HTML/CSS/JavaScript 前端、预览资源
├── 示例输入/            可直接用于验收的 Word 文件
├── .env                 已预置赛事委员会验收专用 Key
├── .env.example         配置模板
├── requirements.txt     已验证的 Python 依赖版本
├── pyproject.toml       Python 项目元数据
└── Dockerfile           Web 原型容器构建文件
```

核心处理链路：

```text
docx → OOXML 解析 → 内容流序列化 → 大模型结构判断
     → 语义文档组装 → JATS 确定性渲染 → 内容守恒与 DTD 校验
```

运行期会自动创建 `webapp/_runs/`、`webapp/_uploads/`、`webapp/_cache/`；这些目录仅保存任务产物、上传分片和模型响应缓存，删除后可自动重建。

## 8. 继续开发

- 转换编排入口：`src/word2jats/pipeline.py`
- Word 解析：`src/word2jats/parse/`
- 大模型结构理解：`src/word2jats/understand/`
- JATS 渲染：`src/word2jats/render/`
- 校验与内容守恒：`src/word2jats/validate/`、`src/word2jats/verify/`
- Web API：`webapp/app.py`
- 前端：`webapp/static/`

Web API 的主要端点为 `/api/convert`、`/api/status/{task_id}`、`/api/result/{task_id}` 和 `/api/download/{task_id}`；大文件另支持 `/api/upload/*` 分片上传流程。
