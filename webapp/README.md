# word2jats Web 应用

网页上传 Word 投稿稿（`.docx`），拿到 JATS 标准 XML —— 不用碰命令行。

面向期刊编辑的设计思路、结果页为什么是这个信息架构，见 [`docs/08-Web应用`](../docs/08-Web应用.md)；挂公网、运维、分片上传的来龙去脉见 [`docs/10-部署上线`](../docs/10-部署上线.md)。

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

浏览器打开地址，拖入一篇 `.docx`（可选：DOI、期刊；图片自动从 docx 内嵌媒体提取），点「开始转换」。
**单篇约 2–4 分钟**——绝大部分时间在等云端模型判结构。完成后可下载 zip（XML + 外部化图片），结果页分五个区：
**成品预览**（期刊样式排版，图表公式都在）、**原文核对**（输出与原稿逐词比对，证明正文没被改坏）、
**内容清单**（作者/章节/图表/参考文献等各部分计数）、**合规检查**（DTD + 结构检查分级）、
**XML 源文件**（高亮全文，供技术人员核对）。同一文件再传会命中磁盘缓存、秒回。

## 用 Docker 部署

```bash
docker build -t word2jats .
docker run -p 8000:8000 -e DASHSCOPE_API_KEY=sk-xxxx word2jats
```

API Key 通过 `-e` 注入、不打进镜像。开放 8000 端口后浏览器访问即可。

## 怎么工作的

- 转换要几分钟且是阻塞的，甩到线程池后台跑，不卡服务：
  上传 → 立即拿 `task_id` → 前端轮询状态 → 完成后取结果。
- 转换本身复用命令行同一套管线（`word2jats.pipeline.convert`），Web 层只做上传、调度、展示。
- 服务端持 API Key。运行期目录都不入库、可随时删：
  `webapp/_cache/`（模型响应磁盘缓存）、`webapp/_runs/<task_id>/`（上传件与产物）、
  `webapp/_uploads/<upload_id>/`（分片上传的中途块，`complete` 后清理）。
- **大文件走分片上传**：浏览器把文件切成 2 MiB 数据块、并发 3 块传，服务端按序拼回并校验 SHA-256。
  这是为绕开 Cloudflare 隧道的 100 秒单请求超时，根因与实测参数见 [`docs/10`](../docs/10-部署上线.md)。
  一次性上传接口 `/api/convert` 保留给小文件和命令行调用。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET  | `/`                                | 上传页（静态资源 URL 带 mtime 版本号，绕开 CDN 缓存） |
| GET  | `/api/journals`                    | 期刊下拉列表（刊号 + 刊名） |
| POST | `/api/convert`                     | 一次性提交转换（multipart：docx + 可选 doi/journal）→ `{task_id}` |
| POST | `/api/upload/init`                 | 开分片上传会话（filename/size/total_chunks）→ `{upload_id}` |
| PUT  | `/api/upload/{upload_id}/{index}`  | 上传第 index 块（块体为请求体） |
| GET  | `/api/upload/{upload_id}`          | 查已收 / 缺失分片，供断点续传 |
| POST | `/api/upload/{upload_id}/complete` | 拼片、校验 sha256，转入转换（可带 doi/journal）→ `{task_id}` |
| GET  | `/api/status/{task_id}`            | 轮询状态（pending/running/done/error + 阶段 + 耗时） |
| GET  | `/api/result/{task_id}`            | 结果（stats + 校验 + 忠实自检 + XML 全文） |
| GET  | `/api/render/{task_id}`            | 服务端 XSLT 渲染的期刊样式 HTML（结果页 iframe 加载） |
| GET  | `/assets/jats-preview.css`         | 渲染视图配套 CSS（NCBI 公有领域） |
| GET  | `/api/download/{task_id}`          | 下载 zip（XML + 图片） |
| GET  | `/api/figure/{task_id}/{name}`     | 渲染视图引用的图片（TIFF 按需转 PNG 仅供预览，下载包原始字节不动） |
