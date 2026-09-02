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

EXPOSE 8080

CMD ["python", "bot.py"]
