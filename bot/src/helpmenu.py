"""/帮助 指令菜单：数据驱动，分类展示。

- /帮助：总览（每类一行，常用指令），按身份只显示该看的分类
- /帮助 <分类>：该分类的完整指令（分类名：聊天 / 工具 / 互动 / 信息 / 管理 / 系统，也认部分别名）
- /帮助 全部：所有可见分类（群里用合并转发发出，避免刷屏；失败退回文字）
- 管理 / 系统两类仅群管理 / 超管可见
- 展示统一用中文指令名；英文写法仍可用（别名），菜单里不再展示
- 纯函数，不依赖 NoneBot，便于测试；指令增删时只需改下面的 SECTIONS
"""

from __future__ import annotations

from dataclasses import dataclass

LINE = "━━━━━━━━━━━━"


@dataclass(frozen=True)
class Section:
    key: str
    emoji: str
    title: str
    aliases: tuple[str, ...]
    summary: str                  # 总览里这一类的一行说明
    items: tuple[tuple[str, str], ...]  # (指令, 说明)
    admin: bool = False


SECTIONS: tuple[Section, ...] = (
    Section(
        "聊天", "💬", "聊天与 AI", ("ai", "对话", "聊天与ai"),
        "@我直接说话 · /记忆 · /说 · /自拍",
        (
            ("@星潮 直接说话", "聊天、问问题、让我办事（也可以叫「星潮」）"),
            ("/记忆", "查看我对你的印象"),
            ("/忘记我 · /记住我", "清除并停止记录 / 恢复记录"),
            ("/说 <文字>", "我用语音念出来（也可以说“用语音回复我”）"),
            ("/转文字", "回复一条语音，识别成文字"),
            ("/自拍", "看看星潮本人的照片"),
            ("关于星潮", "机器人与开发者信息"),
        ),
    ),
    Section(
        "工具", "🔧", "实用工具", ("实用", "工具箱"),
        "/搜索 · /天气 · /提醒 · /投票 · /画",
        (
            ("/搜索 <关键词>", "联网搜索"),
            ("/天气 <城市>", "查天气（汇率直接问我，如“100美元多少人民币”）"),
            ("/提醒 <时间> <内容>", "个人提醒，如 /提醒 明天8点 开会、/提醒 五分钟后 喝水"),
            ("/提醒列表 · /取消提醒 <编号>", "查看 / 取消我的提醒"),
            ("/投票 <题目> <选项…>", "发起群投票；/投 <序号> 参与，/投票结果、/结束投票"),
            ("/画 <描述>", "AI 画图（每人每天 3 张）"),
            ("/群文件 [关键词]", "查看 / 搜索群文件"),
            ("/查询资料 <QQ>", "QQ 资料卡片"),
        ),
    ),
    Section(
        "互动", "🎲", "互动娱乐", ("娱乐", "游戏", "趣味"),
        "/赞我 · /龙王 · 签到 · 抽奖 · /轮盘",
        (
            ("/赞我", "给你的资料卡点赞（每天一次）"),
            ("/龙王", "本群龙王与荣誉成员"),
            ("签到 · 积分 · 排行榜 · 挖矿 · 钓鱼 · 抽奖", "云签到小游戏"),
            ("转账 · 打劫 @某人 · 出售 物品", "积分玩法"),
            ("疯狂星期四", "KFC 文案（也可以 疯狂星期一~日）"),
            ("/轮盘 + /开枪", "俄罗斯轮盘"),
            ("/猜成语", "猜成语游戏"),
            ("戳一戳机器人", "随机回复"),
        ),
    ),
    Section(
        "信息", "📊", "群信息", ("统计", "资讯"),
        "/统计 · /趋势榜 · /更新日志 · /身份",
        (
            ("/统计 [昨日]", "群活跃统计"),
            ("/发言统计 · /月发言统计", "水群榜"),
            ("/趋势榜 [今日|本周|本月] [语言]", "GitHub 趋势榜"),
            ("/更新日志", "最近一次更新内容"),
            ("/身份", "查看群号 / 用户 / 机器人 ID"),
            ("/连通测试", "看机器人在不在线"),
            ("直接发 GitHub 链接", "自动生成仓库卡片"),
            ("私聊发理由 或 /申诉 <理由>", "对处罚有异议时申诉"),
        ),
    ),
    Section(
        "管理", "🛡", "群管理 · 仅超管", ("群管", "群管理"),
        "/禁言 · /踢出 · /撤回 · /公告 · /精华",
        (
            ("/禁言 @某人 [分钟] · /解除禁言 @某人", "禁言 / 解除（需机器人是管理员）"),
            ("/全体禁言 开|关", "全体禁言"),
            ("/踢出 @某人", "移出本群"),
            ("/撤回", "回复目标消息撤回"),
            ("/delete @某人 <条数>", "批量撤回"),
            ("/公告 <内容> · /公告 列表", "发布 / 查看群公告"),
            ("/名片 @某人 内容 · /头衔 @某人 内容", "改群名片 / 专属头衔"),
            ("/精华 · /取消精华 · /精华列表", "回复消息设为 / 取消精华"),
            ("/清理群员 <群号> [确认]", "群满员时清理不活跃成员"),
            ("/入群验证 开|关|提醒|踢出|<分钟>", "新人入群算术验证"),
            ("/敏感词 添加|删除|列表", "敏感词批量管理"),
            ("/拉黑用户 · /拉黑群 <目标>", "黑名单"),
        ),
        admin=True,
    ),
    Section(
        "系统", "⚙️", "系统管理 · 仅超管", ("设置", "管理员", "运维"),
        "/白名单 · /模块 · /智能问答 · /备份",
        (
            ("/白名单 列表|添加|删除 <群号>", "群白名单"),
            ("/超管 列表|添加|删除 <QQ>", "超管管理"),
            ("/模块 <名称> 开|关", "回复/链接/视觉/主动/博客/感知/识别文字/签到/在线状态/链接安全"),
            ("/智能问答 开|关|状态|清空|视觉 <模型>", "AI 问答管理"),
            ("/活跃度 安静|正常|话痨 [群号|全局]", "机器人主动程度"),
            ("/语音回复 开|关 · /音色 · /签名 <内容>", "语音回复 / 音色 / 机器人签名"),
            ("/博客 状态|刷新|列表|开|关", "博客知识关联推荐"),
            ("/词库 重载|列表", "关键词词库"),
            ("/欢迎语 开|关|查看|设置 <内容>", "新人欢迎语"),
            ("/定时任务 列表 · /推送 列表|立即 <编号>", "定时任务 / 内容推送"),
            ("/通过 · /拒绝 <序号> · /待审批", "入群申请审批"),
            ("/邀请列表 · /同意邀请 · /拒绝邀请 <序号>", "好友申请与拉群邀请"),
            ("/申诉列表|查看|通过|驳回 <编号>", "违规申诉处理"),
            ("/运行状态 · /状态", "机器人状态 / 服务器资源"),
            ("/能力自检", "探测当前 NapCat 支持哪些 QQ 能力"),
            ("/备份", "立即备份数据库"),
        ),
        admin=True,
    ),
)


def visible(is_admin: bool) -> tuple[Section, ...]:
    return tuple(s for s in SECTIONS if is_admin or not s.admin)


def find_section(name: str) -> Section | None:
    n = name.strip().lower().lstrip("/")
    for s in SECTIONS:
        if n == s.key or n in s.aliases or n == s.title.lower():
            return s
    return None


def render_overview(is_admin: bool) -> str:
    lines = ["✨ 星潮 · 指令菜单", LINE]
    for s in visible(is_admin):
        lines.append(f"{s.emoji} {s.title}")
        lines.append(f"   {s.summary}")
    lines += [
        LINE,
        "📖 发送 /帮助 <分类> 查看详情，如 /帮助 工具",
        "📚 发送 /帮助 全部 查看所有指令",
        "💡 也可以直接 @我 说人话，比如“明天8点提醒我开会”",
    ]
    if not is_admin:
        lines.append("🔒 管理类指令仅群管理与超管可见")
    return "\n".join(lines)


def render_section(s: Section) -> str:
    lines = [f"{s.emoji} {s.title}", LINE]
    lines += [f"◈ {cmd}\n   {desc}" for cmd, desc in s.items]
    lines += [LINE, "↩ /帮助 返回总览"]
    return "\n".join(lines)


def render_all(is_admin: bool) -> str:
    """所有可见分类拼成一段文字（AI 工具 get_help 与合并转发失败时的兜底）。"""
    parts = ["✨ 星潮 · 全部指令"]
    for s in visible(is_admin):
        parts.append("\n".join([f"{s.emoji} {s.title}"] + [f"  ◈ {cmd} — {desc}" for cmd, desc in s.items]))
    if not is_admin:
        parts.append("🔒 管理类指令仅群管理与超管可见")
    return "\n\n".join(parts)


def forward_nodes(is_admin: bool, uin: str, name: str = "星潮") -> list[dict]:
    """合并转发节点：每个分类一条。"""
    return [
        {"type": "node", "data": {"name": name, "uin": uin, "content": render_section(s)}}
        for s in visible(is_admin)
    ]


def help_text(arg: str, is_admin: bool) -> tuple[str, bool]:
    """按参数返回 (文本, 是否是「全部」)。分类不存在或无权限时给出提示。"""
    arg = arg.strip()
    if not arg:
        return render_overview(is_admin), False
    if arg in ("全部", "all", "所有"):
        return render_all(is_admin), True
    s = find_section(arg)
    if s is None:
        names = " / ".join(x.key for x in visible(is_admin))
        return f"没有「{arg}」这个分类。可选：{names}、全部", False
    if s.admin and not is_admin:
        return "这类指令仅群管理与超管可见～", False
    return render_section(s), False
