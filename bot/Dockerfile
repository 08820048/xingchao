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

# CJK 字体：htmlrender / groupmate-waifu 渲染中文图片必需
# apt 换国内镜像源（deb.debian.org 直连很慢）；playwright 走 npmmirror 下载 chromium
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/*.sources /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fontconfig \
    && rm -rf /var/lib/apt/lists/*

COPY bot/pyproject.toml ./
COPY bot/src ./src
RUN pip install --no-cache-dir .

# 依赖钉子过老的第三方插件：跳过依赖解析单独安装
# （crazy_thursday 钉 httpx<0.24、handle 钉 Pillow<11、dog 错误依赖 poetry、
#   pokepoke_miss 声明的 pil_utils==0.1.10 钉死 Pillow<11 但源码实际未用到，
#   其运行时依赖 nonebot2/adapter/httpx/pillow 已由主安装提供）
RUN pip install --no-cache-dir --no-deps \
        nonebot_plugin_crazy_thursday \
        nonebot_plugin_handle \
        nonebot_plugin_dog \
        pokepoke_miss

COPY bot/bot.py ./
COPY bot/.env.prod ./
RUN cp .env.prod .env
COPY --from=web /web/dist ./web/dist

EXPOSE 8080

CMD ["python", "bot.py"]
