FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. 允许外部传入动态参数与自定义上游仓库
ARG UPSTREAM_REPO=https://github.com/HanaokaYuzu/Gemini-API.git
ARG CACHE_BUST=1

# 2. 动态安装原作者最新代码（通过 CACHE_BUST 彻底打破 Docker 缓存层，确保每次构建都拉取最新 master）
RUN echo "Building with cache bust: ${CACHE_BUST}" && \
    pip install --no-cache-dir git+${UPSTREAM_REPO}

# 3. 安装管理面板后端依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 复制代码与前端静态文件
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV PORT=8000

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
