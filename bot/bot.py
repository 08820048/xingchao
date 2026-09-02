"""星潮 Xingchao bot 入口。

只做：init、注册 OneBot V11 适配器、同步超管、加载 src/plugins。
任何业务逻辑都不允许写在本文件。
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.log import logger

from src.config import sync_superusers

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# XINGCHAO_SUPERUSERS（根 .env / 容器环境变量）优先，覆盖 .env 里的 SUPERUSERS
sync_superusers()

_access_token: str = getattr(driver.config, "onebot_access_token", "") or ""
if not _access_token:
    logger.warning("ONEBOT_ACCESS_TOKEN 为空：反向 WS 将无鉴权，仅可用于本地调试。生产环境禁止！")
elif len(_access_token) < 16 or _access_token == "change-me-long-random":
    logger.warning("ONEBOT_ACCESS_TOKEN 过弱（<16 位或为示例值），生产环境请替换为长随机字符串。")

nonebot.load_plugins("src/plugins")

# 第三方插件（nb 商店）：系统状态查看（/sysstatus，避免与内置 /status 冲突）
nonebot.load_plugin("nonebot_plugin_status")

logger.info("星潮 Xingchao bot 启动完成，等待 NapCat 反向 WS 连接 /onebot/v11/ws")

if __name__ == "__main__":
    try:
        nonebot.run()
    finally:
        # anyio 工作线程（nonebot 同步依赖注入产生）非 daemon，会阻塞进程正常退出；
        # 优雅停机（含 on_shutdown 钩子）完成后强制退出，避免 docker stop 挂起。
        import os

        os._exit(0)
