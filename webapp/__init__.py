"""word2jats Web 应用：上传 Word 稿，拿到 JATS XML。

进程内异步任务：上传 → 提交线程池 → 轮询状态 → 下载 zip。服务端持 API key。
运行说明见 README.md 和 docs/运行与部署.md，设计见 docs/系统设计.md。
"""
