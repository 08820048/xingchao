"""白名单守卫：非白名单群 / 被停用群的消息不进入任何业务，也不触发第三方插件。

为什么用 event_preprocessor 而不是 on_message：
- 本项目的自研插件都带 GROUP_WHITELIST 规则，但第三方插件（GitHub 卡片、
  娶群友、猜成语等）不走这套规则。只在 matcher 层拦截，第三方插件仍会在
  非白名单群响应，等于把「白名单」这层安全默认漏掉了。
- 预处理器在所有 matcher 之前执行，调用 event.stop_propagation() 后事件不再下传，
  第三方插件也就收不到。超管保留例外：需要在任意群处理加群/管理操作。

日志：非白名单群只打 debug，不落聊天内容。
"""

from __future__ import annotations

from nonebot.adapters import Event
from nonebot.log import logger
from nonebot.message import event_preprocessor

from src.config import superuser_ids
from src.permission import disabled_groups, merged_whitelist


@event_preprocessor
async def block_non_whitelist(event: Event) -> None:
    group_id = getattr(event, "group_id", None)
    if not isinstance(group_id, int):
        return  # 私聊由各指令自己的权限规则处理
    user_id = getattr(event, "user_id", None)
    if isinstance(user_id, int) and user_id in superuser_ids():
        return  # 超管在任意群保留管理能力
    if group_id not in merged_whitelist():
        logger.debug(
            f"忽略非白名单群事件：group={group_id} user={user_id} "
            f"type={type(event).__name__}"
        )
        event.stop_propagation()
        return
    if group_id in disabled_groups():
        logger.debug(
            f"忽略已停用群事件：group={group_id} user={user_id} "
            f"type={type(event).__name__}"
        )
        event.stop_propagation()
