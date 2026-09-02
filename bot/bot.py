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

# ─── 第三方插件（nb 商店，详见 docs/THIRD_PARTY_PLUGINS.md）───
THIRD_PARTY_PLUGINS = (
    "nonebot_plugin_blacklist",      # 黑名单（拉黑用户/群，event_preprocessor 拦截）
    "nonebot_plugin_cloudsignx",     # 云签到（签到/积分/挖矿小游戏）
    "nonebot_plugin_crazy_thursday", # 疯狂星期四文案
    "nonebot_plugin_dog",            # 舔狗日记/讲个笑话/一言
    "nonebot_plugin_githubcard",     # GitHub 链接卡片（自动识别消息里的 GitHub 链接）
    "nonebot_plugin_groupmate_waifu",# 娶群友/CP抽卡
    "nonebot_plugin_handle",         # 猜成语（/handle、/猜成语）
    "nonebot_plugin_miao",           # 口僻（发言随机加「喵」，概率见 .env）
    "nonebot_plugin_remake",         # 人生重开模拟器（/人生重开）
    "nonebot_plugin_status",         # 服务器资源状态（/状态）
    "nonebot_plugin_batch_withdrawal",   # /delete @某人 <条数> 批量撤回（群管/超管）
    "nonebot_plugin_BotMailNotice",      # Bot 上下线邮件通知（SMTP 配置见文档）
    "nonebot_plugin_emojilike",          # 被动：消息含表情时自动贴同款回应
    # internet_outage：需 Cloudflare Radar token（OUTAGE_CF_TOKEN），配置后取消下行注释
    # "nonebot_plugin_internet_outage",
    "nonebot_plugin_QRrender",           # /QR 生成二维码
    "nonebot_plugin_qqdetail",           # QQ 资料查询卡片
    "nonebot_plugin_revolver",           # /轮盘 /开枪 俄罗斯轮盘
    "nonebot_plugin_water_geoup_stats",  # /发言统计 /月发言统计
    "pokepoke_miss",                     # 戳一戳错过提醒
)
for _plugin in THIRD_PARTY_PLUGINS:
    nonebot.load_plugin(_plugin)

logger.info("星潮 Xingchao bot 启动完成，等待 NapCat 反向 WS 连接 /onebot/v11/ws")

if __name__ == "__main__":
    try:
        nonebot.run()
    finally:
        # anyio 工作线程（nonebot 同步依赖注入产生）非 daemon，会阻塞进程正常退出；
        # 优雅停机（含 on_shutdown 钩子）完成后强制退出，避免 docker stop 挂起。
        import os

        os._exit(0)
