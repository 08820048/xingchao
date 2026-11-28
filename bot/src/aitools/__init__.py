"""AI 工具注册表：LLM 可调用的全部工具（OpenAI function-calling 定义 + 本地执行器）。

- 每个工具用 @tool(name, description, params, perm) 装饰执行器 handler(bot, event, args) -> str，
  定义与实现写在一起；按领域分在 group / admin / schedule / knowledge / fun 几个模块
- 新增工具：在对应模块写处理函数并加 @tool，再把名字加到下面 TOOL_ORDER 合适的位置；
  同时更新 docs/AI_CAPABILITIES.md
- 本包不是 NoneBot 插件（不注册任何匹配器）；需要 ai.py 里的函数时用 registry._ai() 延迟导入
"""

from __future__ import annotations

from src.aitools import admin, fun, group, knowledge, plugindata, points, roulette, schedule, shop  # noqa: F401  导入即注册
from src.aitools.registry import _REGISTRY, ToolEntry, tool

__all__ = ["TOOL_ORDER", "build_tools", "tool"]

# 传给模型的工具顺序（相关的放在一起，模型更容易挑对）。漏写或多写会在导入时报错。
TOOL_ORDER: tuple[str, ...] = (
    "get_group_info",
    "get_member_list",
    "get_member_info",
    "get_active_stats",
    "mute_member",
    "unmute_member",
    "kick_member",
    "set_whole_ban",
    "list_whitelist",
    "add_whitelist_group",
    "remove_whitelist_group",
    "set_group_business",
    "get_github_trending",
    "get_current_time",
    "get_self_info",
    "send_voice",
    "set_voice_reply",
    "send_selfie",
    "calculate",
    "get_pending_join_requests",
    "approve_join_request",
    "reject_join_request",
    "list_appeals",
    "resolve_appeal",
    "list_replies",
    "reload_replies",
    "set_reply_enabled",
    "set_link_preview_enabled",
    "set_vision_guard_enabled",
    "set_vision_model",
    "get_group_honor",
    "set_member_card",
    "set_member_title",
    "send_like",
    "poke_member",
    "list_group_files",
    "set_bot_signature",
    "set_voice_character",
    "search_group_kb",
    "add_group_kb",
    "suggest_group_kb",
    "recall_chat",
    "web_search",
    "get_weather",
    "get_exchange_rate",
    "create_reminder",
    "list_my_reminders",
    "cancel_reminder",
    "set_join_verify",
    "create_poll",
    "get_poll_result",
    "end_poll",
    "get_points",
    "sign_in",
    "claim_points_relief",
    "transfer_points",
    "get_points_leaderboard",
    "get_points_history",
    "get_points_rules",
    "adjust_points",
    "set_points_config",
    "list_shop_items",
    "buy_shop_item",
    "list_my_redemptions",
    "list_pending_redemptions",
    "get_shop_stats",
    "resolve_shop_redemption",
    "add_shop_item",
    "update_shop_item",
    "get_roulette_status",
    "get_roulette_rules",
    "start_roulette",
    "begin_roulette",
    "join_roulette",
    "shoot_roulette",
    "cancel_roulette",
    "set_roulette_config",
    "generate_image",
    "list_pending_invites",
    "resolve_invite",
    "set_essence_message",
    "delete_essence_message",
    "list_essence_messages",
    "send_sticker",
    "save_sticker",
    "run_capability_check",
    "get_my_memory",
    "forget_my_memory",
    "resume_my_memory",
    "set_proactive_mode",
    "set_proactive_enabled",
    "list_sensitive_words",
    "add_sensitive_words",
    "remove_sensitive_words",
    "recall_message",
    "ping",
    "get_my_id",
    "get_help",
    "get_about",
    "search_blog",
    "get_blog_post",
    "refresh_blog",
    "set_blog_enabled",
    "get_ad_policy",
    "set_ad_policy",
    "get_welcome",
    "set_welcome",
    "set_welcome_enabled",
    "publish_group_notice",
    "get_group_notices",
    "send_group_message",
    "get_changelog",
    "run_backup",
    "list_feed_subscriptions",
    "run_feed_now",
    "list_scheduled_tasks",
    "create_scheduled_task",
    "delete_scheduled_task",
    "toggle_scheduled_task",
    "list_superusers",
    "add_superuser",
    "remove_superuser",
    "set_ai_enabled",
    "get_ai_status",
    "clear_ai_context",
)

if set(TOOL_ORDER) != set(_REGISTRY) or len(TOOL_ORDER) != len(_REGISTRY):
    raise RuntimeError(
        f"TOOL_ORDER 与已注册工具不一致：缺 {sorted(set(_REGISTRY) - set(TOOL_ORDER))}，"
        f"多 {sorted(set(TOOL_ORDER) - set(_REGISTRY))}"
    )

_TOOLS: list[ToolEntry] = [_REGISTRY[name] for name in TOOL_ORDER]


def build_tools() -> list[ToolEntry]:
    """返回全部工具注册表（含仅超管的）。

    权限过滤不在这里做：能力清单需要展示全部能力（标注【仅超管】），
    真正的调用权限在 chat() 中按 permitted 过滤，双重保证不越权。
    """
    return list(_TOOLS)
