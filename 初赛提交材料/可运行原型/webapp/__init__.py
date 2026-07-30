"""word2jats Web 应用：上传 Word 稿，拿到 JATS XML。

进程内异步任务：上传 → 提交线程池 → 轮询状态 → 下载 zip。服务端持 API key。
详见 webapp/设计与计划.md。
"""
