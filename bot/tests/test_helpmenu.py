"""/帮助 菜单（一条消息、更新日志式排版）与指令中文归一化。"""

import re
from pathlib import Path

import pytest

from src import helpmenu
from src.cmdutil import norm_action


def test_menu_hides_admin_sections_for_members():
    member = helpmenu.render_menu(False)
    admin = helpmenu.render_menu(True)
    assert "【实用工具】" in member and "【群管理 · 仅超管】" not in member and "【系统管理 · 仅超管】" not in member
    assert "【群管理 · 仅超管】" in admin and "【系统管理 · 仅超管】" in admin and "/白名单" in admin
    assert "仅群管理与超管可见" in member and "仅群管理与超管可见" not in admin


def test_menu_is_changelog_style_and_not_emoji_heavy():
    text = helpmenu.render_menu(True)
    lines = text.splitlines()
    assert lines[0].startswith("📖 星潮") and lines[1] == helpmenu.LINE and helpmenu.LINE in lines[2:]
    assert all(l.startswith("  • ") for l in lines if "—" in l)  # 条目统一缩进 + 圆点
    emoji = re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", text)
    assert len(emoji) <= 1, emoji  # 只有标题一个图标
    assert len(text) < 3500  # 一条 QQ 消息放得下


def test_menu_uses_chinese_commands_only():
    full = helpmenu.render_menu(True)
    for en in ("/ping", "/help", "/stats", "/plugin", "/superuser", "/welcome", "/ai ", "/blog", "/task", "/feed",
               "/notice", "/recall", "/pending", "/trending", "/status", "/group", "/reply"):
        assert en not in full, en


def test_every_registered_chinese_command_is_in_menu():
    """防漂移：plugins 里注册的中文主指令都应出现在菜单里（刻意不放的在 EXEMPT）。"""
    EXEMPT = {"申诉查看", "申诉通过", "申诉驳回", "申诉列表", "帮助", "关于星潮"}  # 申诉合并成一行写；帮助是菜单本身；关于星潮不带斜杠
    menu = helpmenu.render_menu(True)
    names = set()
    for f in Path("src/plugins").glob("*.py"):
        names |= set(re.findall(r'on_command\(\s*"([^"]+)"', f.read_text()))
    missing = sorted(n for n in names if re.search(r"[一-鿿]", n) and n not in EXEMPT and f"/{n}" not in menu)
    assert not missing, f"菜单缺少指令：{missing}"


def test_every_primary_command_name_is_chinese():
    """统一为中文：主指令名不再是纯英文（英文只作 aliases）。"""
    english = set()
    for f in Path("src/plugins").glob("*.py"):
        english |= {n for n in re.findall(r'on_command\(\s*"([^"]+)"', f.read_text()) if re.fullmatch(r"[A-Za-z]+", n)}
    assert english <= {"approve", "reject"}, english  # request.py 里英文版是独立 matcher，保留兼容


@pytest.mark.parametrize("raw,expected", [
    ("列表", "list"), ("添加 123", "add 123"), ("删除 123", "del 123"), ("开", "on"), ("关闭", "off"),
    ("状态", "status"), ("设置 开 欢迎光临", "set 开 欢迎光临"), ("视觉 glm-4v", "vision glm-4v"),
    ("add 123", "add 123"), ("", ""),
])
def test_norm_action_only_touches_first_word(raw, expected):
    assert norm_action(raw) == expected


def test_norm_action_second_word_for_module_switch():
    assert norm_action("回复 关", second=True) == "回复 off"
    assert norm_action("视觉 开启", second=True) == "vision on"
