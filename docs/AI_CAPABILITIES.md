# AI 能力同步规约（斜杠指令 ⇄ 自然语言对话）

> 本文档是机器人功能的「单一事实来源」。**新增任何功能时必须查阅并遵循本规约。**

## 1. 背景

机器人有两套交互方式：

1. **斜杠指令**：`/help`、`/禁言`、`/trending` 等，由各插件 `on_command` 注册；
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

- **自我认知与权限边界**：`chat()` 每次都会把
  `_capability_prompt(_build_tools(), is_superuser)` 作为一条 system 消息注入。
  `_build_tools()` 返回**全部**工具（含仅超管的），清单里用【仅超管】标注权限，
  并同时注入「当前对话者权限」与「能力与边界规约」，让 AI 清楚：
  - 自己具备哪些能力（避免误答「我没有这个功能」）；
  - 哪些能力需要超管权限（应回答「这个操作需要超管权限」而非「没有这个功能」）；
  - 群管理操作需机器人为群管理员、禁言上限 30 天、群操作只能作用于当前群等边界。
  真正的权限过滤在执行层：只有 `permitted`（按 `is_superuser` 过滤）的工具 schema
  会传给模型，且工具查表也只从 `permitted` 查找，双重保证非超管无法越权。
- **场景注入**：`_build_scene()` 还会注入提问者身份、群信息，以及**机器人在本群的身份**
  （是否群管理员），帮助 AI 判断管理操作能否成功、失败时如何解释。
- **独立人格 / 不硬推销**：规约明确要求 AI 以群友身份自然交流，能力清单是「技能」而非广告；
  除非用户明确询问会什么、或某项能力与当前需求直接相关，否则禁止在回答末尾附加
  「要 XX 可以找我」式的功能推销。问「你能做什么」时用自己的语气举例介绍，
  只有明确索要完整菜单/指令列表时才调用 `get_help` 或提示发送 `/help`。
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
| `/状态` | —（第三方插件 nonebot-plugin-status，硬编码指令，未接入 AI 工具） | 超管 | 群里发 `/状态` 查看服务器 CPU/内存/磁盘；私聊戳一戳也可触发 |
| `/trending [since] [lang]` | `get_github_trending` | 所有人 | 「今天 GitHub 有什么火的项目」 |
| `/禁言 @某人 [分钟]` | `mute_member` | 超管 | 「把TA禁言十分钟」 |
| `/解除禁言 @某人` | `unmute_member` | 超管 | 「解除他的禁言」 |
| `/踢出 @某人` | `kick_member` | 超管 | 「把TA踢出去」 |
| `/全体禁言 开/关` | `set_whole_ban` | 超管 | 「开启全体禁言」 |
| `/recall` | `recall_message` | 超管 | 「把上面那条消息撤回」 |
| `/group list/add/del` | `list_whitelist` / `add_whitelist_group` / `remove_whitelist_group` | 超管 | 「把群 123 加入白名单」 |
| `/superuser list/add/del` | `list_superusers` / `add_superuser` / `remove_superuser` | 超管 | 「添加超管 123456」 |
| `/plugin reply on/off` | `set_reply_enabled` | 超管 | 「关闭关键词回复」 |
| `/plugin link on/off` | `set_link_preview_enabled` | 超管 | 「关闭链接自动解读」 |
| `/plugin vision on/off` | `set_vision_guard_enabled` | 超管 | 「关闭图片违规检测」 |
| `/plugin proactive on/off` | `set_proactive_enabled` | 超管 | 「关闭群聊主动性」 |
| `/活跃度 安静\|正常\|话痨 [群号\|全局]` | `set_proactive_mode` | 超管 | 「话痨一点」「这个群安静点」「全局调成正常」 |
| `/记忆` | `get_my_memory` | 所有人 | 「你记得我什么」「你了解我吗」 |
| `/忘记我` | `forget_my_memory` | 所有人 | 「忘了我吧」「别记我的信息」 |
| `/记住我` | `resume_my_memory` | 所有人 | 「可以重新记住我了」 |
| `/能力自检` | `run_capability_check` | 超管 | 「现在支持精华吗」「看看你有哪些 QQ 能力」 |
| `/精华`（回复消息）`/取消精华` `/精华列表` | `set_essence_message` / `delete_essence_message`（超管）、`list_essence_messages`（所有人） | 见左 | 「把这条设为精华」「看看本群精华」 |
| `/邀请列表` `/同意邀请 <序号>` `/拒绝邀请 <序号> [理由]` | `list_pending_invites` / `resolve_invite` | 超管 | 「有人加我好友吗」「通过 2 号邀请」 |
| —（内置能力） | `send_sticker` | 所有人 | 「发个表情包」「来个无语的表情」 |
| `/plugin ocr on\|off` | — | 超管 | 开关“图片文字检测” |
| `/龙王`（别名 `/群荣誉`） | `get_group_honor` | 所有人 | 「谁是龙王」「群里有哪些荣誉成员」 |
| `/名片 @某人 内容` | `set_member_card` | 超管 | 「把他的群名片改成 xxx」 |
| `/头衔 @某人 内容` | `set_member_title` | 超管（机器人需是群主） | 「给他一个“摸鱼王”头衔」 |
| `/plugin sign\|online\|urlsafe on\|off` | — | 超管 | 开关“群自动签到 / 动态在线状态（默认关）/ 危险链接提醒” |
| `/ai vision <模型>` | `set_vision_model` | 超管 | 「把视觉模型换成 glm-4v-flash」 |
| `/reply list/reload` | `list_replies` / `reload_replies` | 超管 | 「看看词库里有什么」 |
| `/敏感词 add/del/list` | `add_sensitive_words` / `remove_sensitive_words` / `list_sensitive_words` | 超管 | 「把“代刷”加入敏感词」「删掉敏感词 代刷」 |
| `/申诉 <理由>` | —（私聊指令：AI 私聊仅超管可用，被处罚者多为普通成员，故未接入 AI 工具；处理见下一行） | 被处罚者 | 对敏感词处罚有异议时私聊说明理由 |
| `/申诉列表` `/申诉查看 <编号>` `/申诉通过 <编号> [备注]` `/申诉驳回 <编号> [理由]` | `list_appeals` / `resolve_appeal` | 超管 | 「有没有待处理的申诉」「通过 5 号申诉」「驳回 5 号，理由：确实是广告」 |
| `/welcome view/set/on/off` | `get_welcome` / `set_welcome` / `set_welcome_enabled` | 超管 | 「把欢迎语改成……」 |
| `/notice <内容>` / `/notice list` | `publish_group_notice` / `get_group_notices` | 超管 | 「发个公告说周五维护」 |
| —（内置能力） | `send_group_message` | 超管 | 「在群 123 里说一句：今晚八点活动开始」 |
| `/task list` | `list_scheduled_tasks` / `create_scheduled_task` / `delete_scheduled_task` / `toggle_scheduled_task` | 超管 | 「现在有哪些定时任务」「每天 9 点在群里提醒大家签到」「把 3 号任务停掉」 |
| `/blog status/refresh/list/on/off` | `search_blog` / `get_blog_post`（所有人）、`refresh_blog` / `set_blog_enabled`（超管） | 见左 | 「有没有 Mac 剪贴板工具推荐」「博客里有没有讲 git 的文章」「刷新一下博客知识库」 |
| `/通过 /approve <序号>` | `approve_join_request` | 超管 | 「通过 3 号申请」 |
| `/拒绝 /reject <序号>` | `reject_join_request` | 超管 | 「拒绝 3 号申请，理由：不符合要求」 |
| `/pending` | `get_pending_join_requests` | 超管 | 「有没有人申请进群」 |
| `/ai on/off` | `set_ai_enabled` | 超管 | 「把 AI 关掉」 |
| `/ai status` | `get_ai_status` | 超管 | 「AI 今天用了多少次」 |
| `/ai clear` | `clear_ai_context` | 超管 | 「忘掉我们刚才聊的」 |
| —（内置能力） | `get_group_info` / `get_member_list` / `get_member_info` | 所有人 | 「群里多少人」「他是什么身份」 |
| `/说 <文字>` | `send_voice` | 所有人 | 「用语音说一下」「念给我听」「语音回复我」 |
| `/语音回复 开|关` | `set_voice_reply` | 超管 | 「以后都用语音回复我」「别发语音了」 |
| `/自拍` | `send_selfie` | 所有人 | 「看看你的照片」「发张自拍」「你长什么样」 |
| —（内置能力） | `get_current_time` / `calculate` | 所有人 | 「现在几点」「(3+4)*2 等于多少」 |

> 另有三大类**自动能力**（无需指令、无需 AI 显式调用工具，直接监听群消息）：
> - **链接自动解读**（`linkpreview.py`）：白名单群有人发网页链接时，抓取标题/描述并调用
>   AI 用一句话概括，引用原消息回复；开关 `link_preview_enabled`（`/plugin link on|off`）。
> - **图片识别与违规处理**（`visionguard.py`）：白名单群图片经视觉模型识别，**仅正常图片完全静默**，
>   只有命中翻墙/VPN、色情、血腥暴力时才自动撤回并禁言发送者（默认 15 分钟）；开关
>   `vision_guard_enabled`（`/plugin vision on|off`），视觉模型由 `/ai vision <模型>` 配置。
> - **群聊主动性**（`proactive.py`）：基于群聊感知缓冲，像群友一样参与聊天。触发（先过规则门槛，
>   过了才调一次模型）：求助/讨论、有人发图（按档位小概率看图）、接话窗口（机器人刚回复某人后，
>   对方继续说不用再 @）、复读（不调模型）。一次决策返回 skip / react（只贴表情）/ reply（可引用某条、
>   拆成最多 3 条短消息，回复前有拟人延迟）。档位 安静/正常/话痨 可按群设置（`/活跃度`、面板「AI」页、
>   AI 工具 `set_proactive_mode`），决定冷却/日限/门槛/看图与复读概率；夜间静默时段（默认
>   23:30–07:30）只保留接话；所有主动发送共用滚动频率上限。被认真问到是不是 AI 时承认是机器人。
>   依赖「群聊感知」开启；开关 `proactive_enabled`（`/plugin proactive on|off`）。
> - **人设与长期记忆**（`persona.py` + `memory.py`）：人设卡（名字/性格/说话风格/兴趣/底线/背景）
>   在面板「AI」页编辑，@ 对话与主动发言共用，并带一点随时间、群热度变化的轻量"状态"；
>   "不冒充真人"是强制保留的底线。长期记忆是模型从聊天里提炼的**短事实**（每人 ≤8 条、每条 ≤40 字，
>   不存原话、按群隔离、敏感内容本地过滤），后台按"新增发言数 + 间隔 + 每日次数"整理，对话时按字数预算
>   注入相关的人，并带"熟悉度"。成员可 `/记忆` 查看、`/忘记我` 清除并停止记录；超管可在面板查看改删；
>   总开关 `memory_enabled`。入群欢迎默认文案会告知这一点。
> - **表情与表情包**（`expressions.py` + `stickers.py`）：AI 回复里的 `[表情:笑哭]` 发送前换成 QQ 系统表情
>   （语音回复前会剥离占位符）；机器人账号收藏的表情包会被读取并打语义标签，主动发言可发 sticker 动作，
>   `send_sticker` 工具按心情挑一张。表情回应（给某条消息贴表情）也用系统表情 ID。
> - **图片文字检测**（`ocr.py`）：敏感词在文字里没命中时，再用 NapCat `ocr_image` 识别图片里的字并过同一套
>   敏感词判定（含 AI 复核、升级处罚、申诉）；接口不支持时自动关闭。开关 `ocr_guard_enabled`。
> - **历史回填与表情反馈**（`awareness.py` / `feedback.py`）：重启后用 `get_group_msg_history` 回填感知缓冲；
>   监听 `group_msg_emoji_like`，群友给机器人消息贴“踩/？”≥2 次则暂停主动发言 30 分钟，反馈概况也会写进主动发言提示词。
> - **精华推荐**（`essence.py`）：主动发言的 nominate 动作只会私聊管理员“建议设为精华”（每群每天 ≤3 次），不在群里发任何内容。
> - **好友申请与拉群邀请**（`invites.py`）：私聊通知超管并附指令；超管本人的申请/邀请自动通过。
> - **QQ 小能力**（`nctools.py` 等）：群自动签到（`groupsign.py`，每群每天 08:00–10:00 之间一次）；
>   动态在线状态（`onlinestatus.py`，默认关闭）；危险链接提醒（`urlsafety.py`，用 QQ 安全中心判定，只提醒不处罚，
>   linkpreview 也会跳过危险链接）；定时任务 @全体前检查剩余次数，用不了就降级为普通消息并通知超管；
>   @ 机器人并附带/引用合并转发时，会读取转发内容；内容推送里 GitHub 简介翻译在模型不可用时用 NapCat 免费英译中兜底；
>   周报附当前龙王。
> - **群聊感知**（`awareness.py`）：每个白名单群在**内存**里保留最近一段聊天（文字、引用、@、
>   表情包、语音/视频/文件标记、机器人自己的发言），被 @ 时作为"群里最近的聊天"注入 AI，
>   于是能回答"刚才大家在聊什么""上面那张图是什么"。图片说明按需生成并缓存（违规审查已识别的
>   描述直接复用）；用户 @ 时附带或引用的图片会即时识别。不落盘、重启清空、撤回即移除。
>   开关 `awareness_enabled`（`/plugin awareness on|off`），其余参数见模块文档字符串。
> - **博客知识关联推荐**（`blog.py` + `src/blog_kb.py`）：本地索引开发者博客 xuyi.dev 的
>   文章与作品集（RSS + sitemap 全量补齐，24 小时自动刷新）。对话与主动插话时按话题
>   相关性自动注入「博主内容·优先参考」，引导 AI 优先推荐；强相关 + 求助语气时，
>   主动接话门槛降为 1 条消息（保留 5 分钟/群冷却与每日上限）。开关 `blog_enabled`
>   （`/plugin blog on|off`、面板「博客」页、仪表盘模块开关）。

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
- 2026-10-05：新增 `/敏感词 add|del|list` 斜杠指令（超管，支持批量增删/查看），
  同步注册 AI 工具 `add_sensitive_words` / `remove_sensitive_words` / `list_sensitive_words`。
- 2026-10-05：敏感词支持变体归一化（全角/空格标点/繁简/可选拼音）与豁免短语；
  新增处罚记录（面板「违规记录」页）与私聊 `/申诉`（处理后自动解除禁言）；
  新增群活跃周报（面板「定时任务」页配置）。
- 2026-10-06：新增博客知识关联推荐（xuyi.dev）：本地索引 73+ 篇文章与 12 款作品集，
  对话/主动插话自动按相关性注入并优先推荐；新增 `/blog` 指令与 `search_blog` /
  `get_blog_post` / `refresh_blog` / `set_blog_enabled` 工具；面板「博客」页 + 模块开关。
