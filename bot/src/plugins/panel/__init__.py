"""官网与 Web 管理面板：挂载在 NoneBot 的 FastAPI 应用上（与反向 WS 共用 8080 端口）。

- 页面：/（重定向到官网 https://xingchao.dev）、/panel（管理面板）
- 认证：XINGCHAO_PANEL_PASSWORD，留空则启动时随机生成并打印到日志；
  登录成功后种 HttpOnly Cookie（sha256(password)）
- 安全约定：面板仅经 compose 映射 127.0.0.1:8081（SSH 隧道访问），不暴露公网

结构：
- common.py        认证、群名缓存、状态数据等公共部分
- routes_*.py      按领域分组的 API 路由，各自提供 register(app)
- page.py          未构建 web/dist 时的内嵌兜底页面
- 本文件           组装：初始化密码、注册各路由、最后挂载静态前端（mount 必须在所有 API 之后）
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from nonebot import get_driver
from nonebot.log import logger

from . import common, routes_admin, routes_ai, routes_content, routes_moderation, routes_overview
from .page import _PAGE


def _register_routes() -> None:
    app = get_driver().server_app
    for module in (routes_overview, routes_admin, routes_ai, routes_moderation, routes_content):
        module.register(app)

    # 官网部署在 Cloudflare Pages（独立仓库 xingchao_site），bot 只负责管理面板（base=/panel/）。
    # mount 必须放在所有 API 路由之后。
    from fastapi.staticfiles import StaticFiles

    base_dir = Path(__file__).resolve().parents[3]
    dist_dir = base_dir / "web" / "dist"

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("https://xingchao.dev", status_code=302)

    @app.get("/panel", response_model=None)
    @app.get("/panel/", response_model=None)
    async def panel_page() -> FileResponse | HTMLResponse:
        if dist_dir.is_dir():
            return FileResponse(dist_dir / "index.html")
        return HTMLResponse(_PAGE)

    if dist_dir.is_dir():
        app.mount("/panel", StaticFiles(directory=str(dist_dir), html=True), name="panel")
        logger.info(f"Web 管理面板（React 构建）已挂载：/panel（{dist_dir}）")
    else:
        logger.info("Web 管理面板（内嵌单页）已挂载：/panel（未找到 web/dist）")


def _setup() -> None:
    common._init_password()
    try:
        _register_routes()
    except Exception:
        logger.exception("Web 管理面板挂载失败（当前驱动可能不是 FastAPI）")


_setup()
