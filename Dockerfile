# Word2JATS Web 应用容器
# 构建： docker build -t word2jats .
# 运行： docker run -p 8000:8000 -e DASHSCOPE_API_KEY=sk-xxxx word2jats
#   （API Key 通过环境变量传入，不打进镜像）

FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用层缓存）；lxml 的 wheel 自带 libxml2/libxslt，无需系统包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY src/ ./src/
COPY webapp/ ./webapp/
COPY pyproject.toml README.md ./

ENV PYTHONPATH=/app/src
EXPOSE 8000

# 对外监听；API Key 由运行时 -e 注入
CMD ["python", "-m", "webapp", "--host", "0.0.0.0", "--port", "8000"]
