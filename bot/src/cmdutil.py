"""指令参数的中文归一化：让 /白名单 添加 123、/模块 回复 关 这类中文写法与旧的英文写法等价。

只归一化「动作词」（前一两个词），不碰后面的内容，避免把欢迎语、公告正文里的"开""关"误改。
英文写法继续可用（兼容老习惯与已有脚本），菜单和文档里统一展示中文。
"""

from __future__ import annotations

ACTIONS: dict[str, str] = {
    "列表": "list", "查看": "view", "查询": "view",
    "添加": "add", "增加": "add", "加入": "add",
    "删除": "del", "移除": "del", "删": "del",
    "开": "on", "开启": "on", "打开": "on",
    "关": "off", "关闭": "off",
    "状态": "status", "刷新": "refresh", "清空": "clear", "重载": "reload",
    "设置": "set", "视觉": "vision", "视觉模型": "vision", "测试": "test", "立即": "now",
}

PERIODS: dict[str, str] = {"今日": "daily", "今天": "daily", "本周": "weekly", "本月": "monthly"}


def norm_action(raw: str, *, second: bool = False) -> str:
    """把开头的中文动作词换成英文动作词；second=True 时第二个词也归一化（如 /模块 回复 关）。"""
    raw = raw.strip()
    if not raw:
        return raw
    parts = raw.split(None, 2 if second else 1)
    parts[0] = ACTIONS.get(parts[0], parts[0])
    if second and len(parts) > 1:
        parts[1] = ACTIONS.get(parts[1], parts[1])
    return " ".join(parts)
