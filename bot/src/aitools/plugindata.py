"""AI 工具：机器人自己 QQ 账号的资料（会员、等级、昵称、签名）。"""

from __future__ import annotations

from src.aitools.registry import tool


@tool(
    "get_self_info",
    "查看你自己这个 QQ 账号的资料：会员（SVIP/年费/VIP 等级）、QQ 等级、注册年份、昵称、签名"
    "（用户问“你是会员吗/你开会员了吗/你的QQ等级/你多少级”时使用；只说工具返回的内容，不要编造到期时间）",
    {"type": "object", "properties": {}, "required": []},
    "all",
)
async def _t_self_info(bot, event, args) -> str:
    from src.plugins import selfinfo

    if not selfinfo.current():
        await selfinfo.refresh(bot)
    return selfinfo.describe()
