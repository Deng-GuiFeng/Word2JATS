# word2jats Web 应用

网页上传 Word 投稿稿（`.docx`），拿到 JATS 标准 XML —— 不用碰命令行。

## 启动

```bash
# 1. 装依赖（项目根目录）
pip install -r requirements.txt          # 或：pip install -e ".[web]"

# 2. 配 .env（项目根目录），填模型 API Key
#    DASHSCOPE_API_KEY=sk-xxxx

# 3. 起服务
python -m webapp                          # 默认 http://127.0.0.1:8000
python -m webapp --port 8080 --host 0.0.0.0   # 换端口 / 对外
```

浏览器打开 `http://127.0.0.1:8000`，拖入一篇 `.docx`（可选：图片包 zip、DOI、期刊），
点“开始转换”，几十秒后拿到结果：可下载 zip（XML + 外部化图片），结果页分五个标签页——
**渲染视图**（按期刊样式排版，图片/公式都在）、**原始 XML**（高亮）、**校验**（DTD + 结构检查分级）、
**结构摘要**（各部分计数）、**内容忠实**（输出与原稿逐词比对，证明没改坏正文）。
同一文件再传会命中缓存、秒回。

## 怎么工作的

- 转换是阻塞几十秒的云端大模型调用，甩到线程池后台跑，不卡服务：
  上传 → 立即拿 `task_id` → 前端轮询状态 → 完成后取结果。
- 服务端持 API Key；LLM 磁盘缓存放 `webapp/_cache/`，每个任务的上传件与产物放 `webapp/_runs/<task_id>/`（都不入库）。
- 转换本身复用命令行同一套管线（`word2jats.pipeline.convert`），Web 层只做上传/调度/展示。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET  | `/`                       | 上传页 |
| POST | `/api/convert`            | 提交转换（multipart：docx + 可选 figures/doi/journal）→ `{task_id}` |
| GET  | `/api/status/{task_id}`   | 轮询状态（pending/running/done/error + 阶段 + 耗时） |
| GET  | `/api/result/{task_id}`   | 结果（stats + 校验 + XML 原文） |
| GET  | `/api/download/{task_id}` | 下载 zip（XML + 图片） |
| GET  | `/api/journals`           | 期刊下拉列表 |

分阶段设计与计划见 [`设计与计划.md`](设计与计划.md)。
