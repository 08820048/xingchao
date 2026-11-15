"""人格设定：机器人不该把自己当"群管助手"标签。"""

from src.plugins import ai as ai_plugin
from src.plugins import persona


def test_no_tool_dump_in_capability_prompt():
    """能力清单不再整段贴进提示词（工具 schema 已经随 tools= 传了）。

    旧实现会把约 90 行“群信息/群管理/权限”贴进每条消息，模型自然句句往群管上靠。
    """
    text = ai_plugin._capability_prompt(False)
    assert text.count("- ") < 10          # 不再是一长串工具名
    assert "mute_member" not in text
    assert "kick_member" not in text
    assert len(text) < 1200


def test_capability_prompt_keeps_permission_semantics():
    """瘦身不能丢关键语义：没有权限 ≠ 没有这个功能。"""
    super_text = ai_plugin._capability_prompt(True)
    assert "超级管理员" in super_text
    other = ai_plugin._capability_prompt(False)
    assert "普通成员" in other
    assert "需要超管权限" in other
    assert "我没有这个功能" in other  # 明确禁止这样说


def test_capability_prompt_tells_model_tools_are_not_identity():
    text = ai_plugin._capability_prompt(False)
    assert "工具是你的手脚" in text
    assert "不要往管理、规则、功能上引" in text
    assert "主动推销" in text


def test_default_system_prompt_is_person_first():
    prompt = ai_plugin.DEFAULTS["ai_system_prompt"]
    assert "群管助手" in prompt          # 明确点名：你不是这个
    assert "你的身份是你的性格" in prompt
    assert "不列条目汇报" in prompt
    # 旧版开头是“一个开源的 QQ 群助手机器人”，正是标签来源
    assert not prompt.startswith("你是「星潮」，一个开源的 QQ 群助手")


def test_default_persona_backstory_is_not_group_helper():
    assert "群助手" not in persona.DEFAULT_PERSONA["backstory"]
    assert "打理群务" not in persona.DEFAULT_PERSONA["backstory"]
    assert "不把自己当工具" in persona.DEFAULT_PERSONA["backstory"]


def test_bearing_is_injected_and_forbids_self_labeling():
    block = persona.render_persona(persona.DEFAULT_PERSONA)
    assert "不是“群管助手”" in block
    assert "不要给自己贴身份标签" in block
    # 姿态块排在表情规则之前，是独立的自我认知段
    assert block.index("你是个什么样存在") < block.index("[表情:")


async def test_bearing_survives_custom_persona(store):
    """主人把人设改得再奇怪，姿态仍然注入。"""
    await persona.save_persona({"backstory": "我是一个专业的群管理助手"})
    block = persona.render_persona(await persona.get_persona())
    assert "专业的群管理助手" in block   # 用户写的保留
    assert "不要给自己贴身份标签" in block  # 但姿态压着


def test_ai_system_prompt_is_reversible(store):
    """面板「恢复默认」依赖 DEFAULTS 里的提示词可读。"""
    default = ai_plugin.DEFAULTS["ai_system_prompt"]
    assert isinstance(default, str) and len(default) > 100
