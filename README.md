# 星潮 Xingchao

QQ 群助手：**NapCatQQ（小号登录）+ NoneBot2（Python 插件）**，只处理白名单群，
默认只响应指令和关键词。官网：<https://xingchao.dev>

> ⚠️ **风险提示**：本项目使用非官方协议（NapCat + OneBot v11），存在封号风险，
> 请只使用小号，且不要与电脑 QQ 同时登录同一账号。与腾讯官方开放平台无关。

## 架构

```
QQ 群
  ↓
xingchao-napcat   (mlikiowa/napcat-docker)
  ↓  WebSocket Client（反向 WS）
ws://xingchao-bot:8080/onebot/v11/ws
  ↓
xingchao-bot      (NoneBot2 + OneBot V11)
  ↓
data/xingchao.db + data/replies.json + data/logs/
```

## 功能

### 第一期

- 仅白名单群进入业务；空白名单 = 不处理任何群（安全默认）
- 白名单群全量文本日志（`data/logs/group-{群号}-日期.jsonl`，含指令与关键词命中消息）
- 指令：`/help` `/ping` `/id` `/stats` `/status`（status 仅超管）
- 关键词回复（精确 / 包含），同群同词条默认 8 秒冷却
- 超管指令：`/reply reload|list`、`/group list|add|del`、`/plugin reply on|off`

### 第二期

- 群活跃统计：`/stats` 查看消息总量、参与人数、发言 Top5；`/stats yesterday` 看昨日；
  超管私聊为全群总览
- 白名单运行时管理：`/group add|del <群号>` 热更新，持久化到 SQLite 重启保留；
  env 基础白名单仍需改环境变量重启
- 群管（仅超管，需机器人为群管理员）：`/禁言 @某人 [分钟]`、`/解除禁言 @某人`、
  `/全体禁言 开|关`、`/踢出 @某人`、`/recall`（回复目标消息或带 message_id）
- 链接自动解读（白名单群，`/plugin link on|off`）：有人发网页链接时，抓取页面信息并调用
  AI 用一句话概括，引用原消息回复
- 图片识别与违规处理（白名单群，`/plugin vision on|off`）：AI 视觉识别图片内容，
  **正常图片完全静默不回复**，只有命中翻墙/VPN、色情、血腥暴力、API/AI 中转站广告
  （低价代充、转售大模型 API、「来带量」招揽等价目表/推广图）时才自动撤回并禁言发送者
  （默认 15 分钟），并私聊通知超管；视觉模型用 `/ai vision <模型>` 配置
- 二维码引流风控（默认开，面板「仪表盘 → 图片与二维码审核」可配）：本地解码图片中的二维码
  （不耗 AI，AI 不可用也生效），命中微信群 / 微信个人号 / 企业微信 / QQ 群等引流码时
  撤回 + 禁言（默认 15 分钟）+ 通知超管；拦截类型、禁言时长可随时调整
- 群聊感知（白名单群，`/plugin awareness on|off`）：机器人在内存里保留每群最近的聊天（含图片说明、
  引用、@、机器人自己的发言），被 @ 时据此回答「刚才在聊什么 / 这张图是什么」；用户 @ 时附带或
  引用的图片会即时识别；不落盘，重启清空，消息被撤回即移除
- 群聊主动性（白名单群，`/plugin proactive on|off`）：基于群聊感知像群友一样参与聊天——有人求助或
  讨论时接话、看到有意思的图评论一句、只贴个表情回应、机器人回复后对方接着说不用再 @、偶尔跟着复读；
  一次决策可选 跳过 / 表情 / 回复（可引用、可拆成几条短消息）。`/活跃度 安静|正常|话痨 [群号|全局]`
  或面板「AI」页按群调档位；夜间静默时段只保留接话；主动发送有滚动频率上限与拟人延迟
- 人设与长期记忆：面板「AI」页编辑人设卡（名字/性格/说话风格/兴趣/底线/背景，“不冒充真人”强制保留）；
  机器人会把聊天里提炼的短事实（兴趣、在做的项目、群里的梗等，不存原话、按群隔离、敏感内容过滤）记成
  对群友的印象，并带“熟悉度”，聊天时自然提起。成员发送 `/记忆` 查看、`/忘记我` 清除并停止记录、
  `/记住我` 恢复；超管可在面板查看、改写、删除
- 表情与表情包：回复里会带 QQ 系统表情；读取机器人账号收藏的表情包并按心情发送；`/能力自检` 可探测当前
  NapCat 支持哪些 QQ 能力（只读）
- 群精华：`/精华`（回复消息）`/取消精华` `/精华列表`；机器人也会私聊管理员推荐值得设为精华的内容
- 图片文字检测：敏感词也会检查图片里的文字（NapCat OCR，不支持时自动关闭，`/plugin ocr on|off`）
- 好友申请与拉群邀请：私聊通知管理员，`/邀请列表` `/同意邀请 <序号>` `/拒绝邀请 <序号>`
- 群荣誉与群成员装扮：`/龙王` 查看龙王等荣誉成员；超管 `/名片 @某人 内容`、`/头衔 @某人 内容`
- 危险链接提醒（QQ 安全中心判定，只提醒不处罚）、群自动签到、定时任务 @全体前检查剩余次数、
  动态在线状态（默认关闭，`/plugin online on|off`）、@ 机器人并转发聊天记录时能读懂转发内容
- 互动小能力：`/赞我` 点赞、`/群文件 [关键词]` 搜索群文件、AI 可戳一戳群友；群里的长回答自动用合并转发；超管 `/签名` 改机器人签名
- 内容订阅推送：早报、GitHub 趋势榜等按时间推送到群，内容未就绪不发旧内容、失败会私聊超管（面板「内容推送」页，`/feed list|now`）
- 定时任务：定时发群消息、支持 @全体与多种重复规则（面板「定时任务」页，`/task list`）
- 群满员提醒与清理：满员后提醒并给出最不活跃成员名单，`/清理群员 确认` 才执行（默认不自动踢）
- 上线 / 下线 / 版本更新通知：机器人上线、停机、发新版本时自动在群里公告，`/更新日志` 可随时查看
- 数据库每日备份：每天 03:30 自动备份并保留最近 7 份，超管 `/备份` 可立即备份
- 管理面板各处显示群名称（群名（群号））
- 敏感词监控与误杀治理（面板「敏感词」页 + 超管 `/敏感词 add|del|list`）：命中候选先做
  变体归一化（全角 / 空格标点 / 繁简，可选拼音），再由 AI 结合语境复核，确认违规才撤回 /
  禁言；`~前缀` 为豁免短语；处罚记录可在面板「违规记录」页查询，被处罚者可私聊 `/申诉`
- 群活跃周报：面板「定时任务」页配置星期 / 时间 / 范围，定时向群内播报前一周消息量环比、
  参与人数、最活跃日与发言 Top3
- 统计图表（面板「统计」页）：消息趋势、24 小时分布、群消息对比、发言 Top10，
  使用 Chart.js 绘制、跟随明暗主题的黑白灰单色图表
- 新人进群欢迎（白名单群，`/welcome on|off` 开关，持久化 kv）
- Web 管理面板：`http://127.0.0.1:8081/panel`（公网为 `panel.xingchao.dev`）
  （compose 映射 `127.0.0.1:8081:8080`，
  远程用 SSH 隧道 `ssh -L 8081:127.0.0.1:8081 ...`；密码见 `XINGCHAO_PANEL_PASSWORD`，
  未设置则随机生成并打印在 bot 日志），含状态 / 统计 / 日志 / 词库编辑 / 白名单管理
  管理面板前端为 Vite + React + [coss ui](https://coss.com/ui/)（Base UI + Tailwind v4），
  多阶段 Docker 构建自动编译；`web/dist` 缺失时回退内嵌单页
  bot 根路径 `/` 重定向到官网 <https://xingchao.dev>
  支持浅色 / 深色 / 跟随系统三种主题
- 官网 xingchao.dev 部署在 Cloudflare Pages，源码在独立仓库
  [xingchao_site](https://github.com/08820048/xingchao_site)，与 bot 相互独立
- 群内 @机器人（或昵称唤起）自动回应，带冷却防刷屏
- 每个群可独立开关业务（面板操作，持久化 SQLite）

## 快速开始

1. **填环境变量**

   ```bash
   cp .env.example .env
   ```

   编辑 `.env`：填超管 QQ 号（`XINGCHAO_SUPERUSERS`）、群白名单
   （`XINGCHAO_GROUP_WHITELIST`，逗号分隔，留空则不处理任何群）、
   以及至少 16 位的随机 `ONEBOT_ACCESS_TOKEN`（生产环境禁止为空或示例值）。

2. **启动**

   ```bash
   docker compose up -d
   ```

3. **NapCat 扫码登录**

   `docker logs xingchao-napcat` 查看 WebUI token，本机打开
   `http://127.0.0.1:6099/webui`（服务器需 SSH 隧道），用手机 QQ 扫码登录小号。
   详见 [napcat/README.md](napcat/README.md)。

4. **配置反向 WS**

   WebUI → 网络配置 → 新建 **WebSocket 客户端**：
   URL 填 `ws://xingchao-bot:8080/onebot/v11/ws`，Token 与 `ONEBOT_ACCESS_TOKEN` 一致。

5. **验证**

   小号进白名单群，群内发送 `/ping`，应回复 `pong`。

## 仓库结构

```
xingchao/
├── docker-compose.yml      # xingchao-bot + xingchao-napcat，内部网络
├── .env.example            # 根环境变量模板（真实 .env 不入库）
├── docs/REQUIREMENTS.md    # 需求说明书
├── docs/AI_CAPABILITIES.md # AI 能力同步规约（斜杠指令 ⇄ 自然语言）
├── docs/THIRD_PARTY_PLUGINS.md # 第三方插件使用文档
├── docs/DEPLOYMENT.md      # 部署指南（新服务器/迁移/Cloudflare）
├── docs/OPERATIONS.md      # 运维与操作手册（凭据位置 / 日常操作 / 故障排查）
├── napcat/README.md        # 扫码与 WS 客户端配置步骤
├── bot/                    # NoneBot2 应用（python:3.12-slim 自建镜像）
│   ├── bot.py              # 仅 init / 注册适配器 / 加载插件
│   ├── .env / .env.prod    # dev / prod 配置
│   └── src/
│       ├── config.py       # pydantic 配置
│       ├── permission.py   # 白名单（env + 运行时）/ 超管规则
│       ├── store.py        # SQLite (kv, msg_stat, msg_stat_user)
│       └── plugins/
│           ├── whitelist.py  # 非白名单群忽略日志
│           ├── logger.py     # 白名单群 jsonl 日志
│           ├── basic.py / reply.py / admin.py / stats.py   # 基础指令 / 关键词回复 / 超管指令 / 活跃统计
│           ├── groupadmin.py / groupextras.py / groupfull.py / groupsign.py  # 群管+欢迎 / 荣誉名片头衔 / 满员清理 / 签到
│           ├── ai.py / mention.py / persona.py / memory.py   # AI 问答与工具 / @响应 / 人设 / 长期记忆
│           ├── awareness.py / proactive.py / feedback.py     # 群聊感知 / 主动发言 / 表情反馈
│           ├── expressions.py / stickers.py / selfie.py / voice.py  # 表情 / 表情包 / 自拍 / 语音
│           ├── sensitive.py / appeals.py / visionguard.py / ocr.py / urlsafety.py  # 内容安全
│           ├── linkpreview.py / blog.py / github.py / feeds.py  # 链接解读 / 博客知识 / GitHub / 内容推送
│           ├── scheduler.py / weeklyreport.py / presence.py / updates.py / backup.py  # 定时 / 周报 / 上下线 / 公告 / 备份
│           ├── interact.py   # 点赞 / 戳一戳 / 群文件 / 已读 / 签名
│           ├── request.py / invites.py / essence.py / capabilities.py / onlinestatus.py / nctools.py  # 入群申请 / 邀请 / 精华 / 能力自检 / 在线状态 / NapCat 封装
│           └── panel.py      # Web 管理面板（/panel）；/ 重定向官网
├── web/                    # 管理面板前端（Vite + React + Tailwind v4 + coss ui，base=/panel/）
└── data/                   # 运行数据（compose 挂载，不入库）
```

> 官网（xingchao.dev）源码在独立仓库 [xingchao_site](https://github.com/08820048/xingchao_site) 维护，
> 部署在 Cloudflare Pages，与本容器无关；本容器根路径 `/` 重定向到官网。

## 安全约定

- `ONEBOT_ACCESS_TOKEN` 必须为非空强随机串；NapCat WebUI 仅映射 `127.0.0.1:6099`
- bot 的 8080 端口不映射公网，仅容器网络内部可达
- `.env`、`data/`、`napcat/qq/`、`napcat/config/` 均在 `.gitignore` 中
- 日志不打印 token；完整聊天内容只落 jsonl 日志，不进数据库

## 验收清单

- [ ] 仓库结构与需求第 4 节一致
- [ ] `docker compose config` 能解析
- [ ] bot 容器监听 8080，日志无适配器导入错误
- [ ] 文档写清 NapCat 扫码 + WS 客户端配置
- [ ] 白名单为空时群消息不回复
- [ ] 白名单群 `/ping` → `pong`
- [ ] `/id` 回显 group_id / user_id / self_id
- [ ] `/help` 含「星潮」和 `https://xingchao.dev`
- [ ] 发送「你好星潮」按词库回复
- [ ] 冷却期内重复触发不刷屏
- [ ] `data/logs/` 产生 jsonl
- [ ] 非超管无法使用 `/status`、`/reply reload`
- [ ] `.gitignore` 排除密钥和 QQ 会话

## 部署

详见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)：全新安装、旧服务器迁移、Cloudflare 公网部署与验收清单。

## License

[Apache License 2.0](LICENSE)
