# 官网源码仓库（独立仓库 xingchao_site），构建时克隆并编译；
# 如仓库私有或地址不同，可用 --build-arg SITE_REPO_URL=<git url> 覆盖
ARG SITE_REPO_URL=https://github.com/08820048/xingchao_site.git

FROM node:22-alpine AS site
ARG SITE_REPO_URL
RUN apk add --no-cache git
WORKDIR /site
RUN git clone --depth 1 "$SITE_REPO_URL" .
RUN npm ci && npm run build

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim

# 可选：构建时传入 PIP_INDEX_URL（如国内镜像 https://pypi.tuna.tsinghua.edu.cn/simple）
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY bot/pyproject.toml ./
COPY bot/src ./src
RUN pip install --no-cache-dir .

COPY bot/bot.py ./
COPY bot/.env.prod ./
RUN cp .env.prod .env
COPY --from=web /web/dist ./web/dist
COPY --from=site /site/dist ./site/dist

EXPOSE 8080

CMD ["python", "bot.py"]
