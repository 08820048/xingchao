# AI 能力同步规约（斜杠指令 ⇄ 自然语言对话）

> 本文档是机器人功能的「单一事实来源」。**新增任何功能时必须查阅并遵循本规约。**

## 1. 背景

机器人有两套交互方式：

1. **斜杠指令**：`/help`、`/mute`、`/trending` 等，由各插件 `on_command` 注册；
2. **自然语言对话**：用户 @机器人（或私聊超管）直接说话，由 AI 插件
   （`bot/src/plugins/ai.py`）调用 LLM 并通过 **工具调用（function calling）** 完成。

历史问题：指令菜单（/help）里标注的能力，AI 对话时却经常回答
「我没有这样的功能」。原因是 LLM 只知道自己注册了哪些工具，而注册表滞后于指令开发。

**铁律：凡是能通过斜杠指令完成的事，必须也能通过和机器人的自然对话完成。**

## 2. 新增功能时的强制检查清单

新增/修改一个斜杠指令时，必须同步完成以下四步（缺一即视为功能未完成）：

- [ ] **实现指令**：在对应插件中注册 `on_command`；
- [ ] **注册 AI 工具**：在 `bot/src/plugins/ai.py` 中新增
      `_t_xxx` 处理器，并在 `_build_tools()` 里用 `_tool(...)` 注册
      （注意标注 `perm`：`all` = 所有人 / `superuser` = 仅超管）；
- [ ] **更新能力清单**：能力清单由 `_capability_prompt()` 从工具注册表**自动生成**，
      无需手工维护；只需确保工具的 `description` 写清楚「用户会怎么说这件事」；
- [ ] **更新本文档**：在第 4 节的对照表中补一行。

同步更新 `/help` 菜单文本（`bot/src/plugins/basic.py`）。

## 3. 实现机制说明

- **系统提示词注入**：`chat()` 每次都会把
  `_capability_prompt(_build_tools(is_superuser))` 作为一条 system 消息注入，
  内含全部工具清单与「能力规约」，并明确禁止 AI 说「我没有这个功能」。
  权限过滤是自动的：非超管用户不会看到 `superuser` 工具。
- **工具执行**：处理器签名统一为 `async def _t_xxx(bot, event, args) -> str`，
  返回值是给 LLM 的中文摘要（不是给用户看的原文）。
- **纯 @ 唤起**：用户只 @ 机器人、不带文字时，AI 插件会注入
  `PURE_AT_PROMPT`，要求结合上下文回应；没有上下文则自由发挥。
  AI 未启用 / 超限 / 调用失败时，发送 `PURE_AT_FALLBACK` 中的固定回应，
  保证机器人**永远不会对纯 @ 完全沉默**。
- **上下文**：每群保留最近 N 轮对话（`ai_ctx_rounds`），纯 @ 响应同样走上下文。

## 4. 斜杠指令 ⇄ AI 工具对照表

| 斜杠指令 | AI 工具 | 权限 | 自然语言示例 |
| --- | --- | --- | --- |
| `/ping` | `ping` | 所有人 | 「测一下你在不在线」 |
| `/id` | `get_my_id` | 所有人 | 「这是哪个群」「我的QQ是多少」 |
| `/help` | `get_help` | 所有人 | 「你能做什么」「 help 」 |
| 「关于星潮」 | `get_about` | 所有人 | 「你是谁做的」「开发者是谁」 |
| `/stats [day]` | `get_active_stats` | 所有人 | 「今天群里活跃吗」 |
| `/天气 <城市>` | `get_weather` | 所有人（需配置 QWEATHER_JWT_*） | 「北京今天天气怎么样」 |
| `/状态` | —（第三方插件 nonebot-plugin-status，硬编码指令，未接入 AI 工具） | 超管 | 群里发 `/状态` 查看服务器 CPU/内存/磁盘；私聊戳一戳也可触发 |
| `/trending [since] [lang]` | `get_github_trending` | 所有人 | 「今天 GitHub 有什么火的项目」 |
| `/mute @某人 [分钟]` | `mute_member` | 超管 | 「把TA禁言十分钟」 |
| `/unmute @某人` | `unmute_member` | 超管 | 「解除他的禁言」 |
| `/kick @某人` | `kick_member` | 超管 | 「把TA踢出去」 |
| `/banall on/off` | `set_whole_ban` | 超管 | 「开启全体禁言」 |
| `/recall` | `recall_message` | 超管 | 「把上面那条消息撤回」 |
| `/group list/add/del` | `list_whitelist` / `add_whitelist_group` / `remove_whitelist_group` | 超管 | 「把群 123 加入白名单」 |
| `/superuser list/add/del` | `list_superusers` / `add_superuser` / `remove_superuser` | 超管 | 「添加超管 123456」 |
| `/plugin reply on/off` | `set_reply_enabled` | 超管 | 「关闭关键词回复」 |
| `/reply list/reload` | `list_replies` / `reload_replies` | 超管 | 「看看词库里有什么」 |
| `/welcome view/set/on/off` | `get_welcome` / `set_welcome` / `set_welcome_enabled` | 超管 | 「把欢迎语改成……」 |
| `/notice <内容>` / `/notice list` | `publish_group_notice` / `get_group_notices` | 超管 | 「发个公告说周五维护」 |
| `/task list` | `list_scheduled_tasks` | 超管 | 「现在有哪些定时任务」 |
| `/通过 /approve <序号>` | `approve_join_request` | 超管 | 「通过 3 号申请」 |
| `/拒绝 /reject <序号>` | `reject_join_request` | 超管 | 「拒绝 3 号申请，理由：不符合要求」 |
| `/pending` | `get_pending_join_requests` | 超管 | 「有没有人申请进群」 |
| `/ai on/off` | `set_ai_enabled` | 超管 | 「把 AI 关掉」 |
| `/ai status` | `get_ai_status` | 超管 | 「AI 今天用了多少次」 |
| `/ai clear` | `clear_ai_context` | 超管 | 「忘掉我们刚才聊的」 |
| —（内置能力） | `get_group_info` / `get_member_list` / `get_member_info` | 所有人 | 「群里多少人」「他是什么身份」 |
| —（内置能力） | `get_current_time` / `calculate` | 所有人 | 「现在几点」「(3+4)*2 等于多少」 |

## 5. 消息格式规约（Markdown 处理）

**QQ 群聊/私聊不渲染标准 Markdown**。LLM 默认会输出 `**加粗**`、`# 标题`、
`` ```代码块`` ``` 等源码，若直接发送，用户看到的就是一堆符号。

规则：

1. 所有「LLM 生成的文本」在发送前**必须**经过
   `bot/src/markdown.py` 的 `md_to_qq()` 降级转换
   （标题→【】、加粗去标记、链接→文字（url）、表格→全角竖线等）；
2. 新增任何 AI 输出口径（新指令、新面板功能、新播报）同样适用；
3. 指令类固定文案（/help 等）本来就手写成纯文本，无需转换，但**不要**
   在固定文案里使用 Markdown 语法；
4. 系统提示词已要求 AI 回答简洁、口语化，`md_to_qq` 是最后兜底，两者配合使用。

## 6. 修订记录

- 2025-XX-XX 初版：建立规约；AI 工具注册表补齐全部斜杠指令能力；
  修复纯 @ 无响应；新增 Markdown 降级转换。
