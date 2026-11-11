# 星潮 Xingchao 运维与操作手册

> 适用环境：生产服务器（Docker Compose 部署）
> 读者：项目维护者 / 运维人员
> 最后更新：2026-09-01

---

## 1. 项目概览

| 项 | 值 |
|---|---|
| 项目名 | 星潮 Xingchao（QQ 群助手） |
| 官网 | <https://xingchao.dev> |
| 仓库 | `git@github.com:08820048/xingchao.git` |
| 服务器公网 IP | `191.223.213.202` |
| 服务器项目目录 | `/root/xingchao` |
| 协议 | NapCatQQ + OneBot v11（非官方协议，**有小号封号风险**） |
| 技术栈 | NoneBot2（Python 3.12）+ FastAPI + SQLite + Docker Compose |

### 架构

```
QQ 群消息
  ↓
xingchao-napcat 容器（QQ 协议端，小号登录）
  ↓ 反向 WebSocket（ws://xingchao-bot:8080/onebot/v11/ws）
xingchao-bot 容器（NoneBot2 业务端）
  ├─ data/xingchao.db        SQLite（统计 / 模块开关 / 白名单）
  ├─ data/replies.json       关键词词库
  └─ data/logs/*.jsonl       白名单群全量文本日志（按群按天）
```

### 容器清单

| 容器 | 作用 | 端口（宿主机） |
|---|---|---|
| `xingchao-bot` | NoneBot2 业务端 + Web 管理面板 | `127.0.0.1:8081 → 8080`（不对公网） |
| `xingchao-napcat` | NapCatQQ 协议端，WebUI 扫码登录 | `127.0.0.1:6099 → 6099`（仅本机/隧道） |

---

## 2. 重要凭据与配置位置（不在本文档存储明文）

| 凭据 | 位置 |
|---|---|
| AI API 地址 / 密钥 | 优先在面板「AI」页配置（存 SQLite）；环境变量 `XINGCHAO_AI_BASE_URL` / `XINGCHAO_AI_API_KEY` 仅作初始兜底 |
| OneBot 反向 WS 令牌 | 服务器 `/root/xingchao/.env` 的 `ONEBOT_ACCESS_TOKEN` |
| 超管 QQ 号 | 同上 `XINGCHAO_SUPERUSERS`（多个用英文逗号分隔，如 `2217021563,1217284058`；修改后需 force-recreate bot） |
| 群白名单（env 基础） | 同上 `XINGCHAO_GROUP_WHITELIST` |
| Web 管理面板密码 | 同上 `XINGCHAO_PANEL_PASSWORD` |
| NapCat WebUI Token | `napcat/config/webui.json` 的 `token` 字段，或 `docker logs xingchao-napcat` 查看（已配置 autoLoginAccount，QQ 崩溃后自动快速登录） |

> ⚠️ `.env`、`data/`、`napcat/config/`、`napcat/qq/` 均已 gitignore，**严禁**提交或截图外发。

---

## 3. 日常操作

### 3.1 SSH 隧道（在自己电脑上执行）

访问 NapCat WebUI 和管理面板都需要隧道：

```bash
ssh -N \
  -L 6099:127.0.0.1:6099 \
  -L 8081:127.0.0.1:8081 \
  -i "<本机私钥路径>" \
  root@191.223.213.202
```

- `-N` 只做端口转发，窗口保持开启即可
- 需要加端口时再补 `-L 本地端口:127.0.0.1:服务器端口`

### 3.2 启停与重建

```bash
cd /root/xingchao

docker compose up -d              # 启动（已构建）
docker compose up -d --build      # 代码有改动后重建并启动
docker compose restart xingchao-bot     # 只重启 bot
docker compose restart xingchao-napcat  # 只重启 napcat
docker compose down               # 停止并移除容器（数据在挂载目录，不丢）
docker compose ps                 # 查看运行状态
```

> ⚠️ 改了 `.env`（超管/白名单/令牌/面板密码）后需要 `docker compose up -d --force-recreate xingchao-bot` 才生效。

### 3.3 日志查看

```bash
docker logs -f xingchao-bot            # bot 实时日志（收发消息、插件日志）
docker logs -f xingchao-napcat         # QQ 协议端日志（登录、消息收发）
docker logs --tail 100 xingchao-bot    # 最近 100 行
docker logs --since 10m xingchao-napcat  # 最近 10 分钟
```

群聊消息的完整文本日志在 `data/logs/group-{群号}-YYYY-MM-DD.jsonl`，也可直接在管理面板「日志」页查看。

### 3.4 数据备份

```bash
# 打包运行数据（数据库 + 词库 + 日志）
tar czf xingchao-data-$(date +%F).tar.gz -C /root/xingchao data
```

- `xingchao.db` 是唯一状态存储（统计、开关、运行时白名单），建议定期备份
- QQ 登录会话在 `napcat/qq/`，备份它可免重新扫码（泄露等同于交出账号，注意保管）

---

## 4. QQ 小号登录（掉线后必看）

非官方协议**掉线是常态**。症状：群里 @ 机器人无反应、`/连通测试` 无响应、面板正常但 bot 日志无消息流入。

### 处理步骤

1. 开 SSH 隧道（见 3.1）
2. 打开 NapCat WebUI：`http://127.0.0.1:6099/webui?token=<token>`（token 见第 2 节；URL 带参数可自动登录）
   - 若提示 token invalid：强制刷新（Cmd+Shift+R）或用无痕窗口；仍不行则重置 token：
     ```bash
     # 修改 napcat/config/webui.json 的 token 字段后重启
     docker compose restart xingchao-napcat
     ```
3. 「QQ 登录」页 → 手机 QQ 扫码登录小号
4. 登录后反向 WS 配置已持久化（`napcat/config/onebot11_<QQ号>.json`），会自动重连，无需重填
5. 验证：`docker logs -f xingchao-bot` 出现 `Bot xxx connected`，群内发 `/连通测试` 回 `pong`

### 注意事项

- 小号**不要**与电脑 QQ 同时登录同一账号
- 扫码后短时间内反复刷新/重连可能触发安全验证，按手机 QQ 提示处理
- 使用非官方协议存在封号风险，**只用小号**

---

## 5. Web 管理面板

- 地址：`http://127.0.0.1:8081/panel`（需 SSH 隧道）
- 密码：服务器 `.env` 的 `XINGCHAO_PANEL_PASSWORD`（改动后需重建 bot 容器生效）
- 前端：Vite + React + Tailwind v4 + [coss ui](https://coss.com/ui/)，源码在 `web/`，Docker 多阶段构建自动编译
- 主题：浅色 / 深色 / 跟随系统，右上角切换，持久化到浏览器

| 页面 | 功能 |
|---|---|
| 仪表盘 | 运行时长、白名单群数、词条数、日志文件数；关键词/进群欢迎模块开关 |
| 统计 | 按日期查看各群消息量、参与人数、发言 Top5 |
| 日志 | 查看任意群任意日期的 jsonl 日志（尾部 N 条 + 关键字过滤） |
| 词库 | 关键词词条可视化增删改（精确/包含匹配、冷却时间），保存即热重载 |
| 白名单 | 群列表与来源；运行时添加/移除群；每个群独立业务开关 |

---

## 6. 机器人指令一览

### 所有人可用（白名单群内）

| 指令 | 说明 |
|---|---|
| `/连通测试` | 连通测试 |
| `/身份` | 查看群号 / 用户 / 机器人 ID |
| `/统计 [昨日]` | 群活跃统计（默认当天） |
| `/搜索 <关键词>` `/天气 <城市>` | 联网搜索 / 天气（搜索提供商见下方环境变量） |
| `/提醒 <时间> <内容>` `/提醒列表` `/取消提醒 <编号>` | 个人提醒（到点 @ 你；每人最多 10 条） |
| `/投票 <题目> <选项…>` `/投 <序号>` `/投票结果` `/结束投票` | 群投票（到时自动公布结果） |
| `/画 <描述>` | AI 绘图（需先配置 kv `image_model`；每人每天 3 张） |
| `/龙王`（别名 `/群荣誉`） | 查看龙王等群荣誉成员 |
| `/更新日志` | 查看最近一次更新公告 |
| `/记忆` `/忘记我` `/记住我` | 查看 / 清除并停止记录 / 恢复机器人对自己的印象 |
| `/转文字`（回复语音） | 识别语音内容 |
| `@星潮 <内容>` 或 `星潮<内容>` | @响应（同群 8 秒冷却）；内容含「帮助/help」回复帮助菜单 |

### 仅超管（群聊或私聊）

| 指令 | 说明 |
|---|---|
| `/运行状态` | 运行状态 |
| `/禁言 @某人 [分钟]` | 禁言（默认 10 分钟，上限 30 天；**需机器人是群管理员**） |
| `/解除禁言 @某人` | 解除禁言 |
| `/全体禁言 开\|关` | 全体禁言 |
| `/踢出 @某人` | 移出群聊 |
| `/撤回` | 撤回消息（回复目标消息发送，或 `/撤回 <message_id>`） |
| `/词库 重载\|列表` | 词库热重载 / 列表 |
| `/白名单 列表\|添加\|删除 <群号>` | 白名单管理（add/del 即时生效，重启保留） |
| `/模块 回复\|链接\|视觉\|主动 开\|关` | 关键词 / 链接自动解读 / 图片违规处理 / 群聊主动性 开关 |
| `/欢迎语 开\|关` | 进群欢迎开关 |
| `/欢迎语 查看` | 查看当前欢迎语 |
| `/欢迎语 设置 <欢迎语>` | 自定义欢迎语（占位符：`{at}`=@新人、`{qq}`=新人QQ、`{group}`=群号；也可在面板「仪表盘」页编辑） |
| `/入群验证 开\|关\|提醒\|踢出\|<分钟>` | 新人入群验证（默认关；超时默认仅提醒超管，选「踢出」需机器人是管理员） |
| `/备份` | 立即备份数据库并列出现有备份 |
| `/推送 列表\|立即 <编号>` | 查看内容推送订阅 / 立即推送一次 |
| `/定时任务 列表` | 查看定时任务（增删改在面板「定时任务」页） |
| `/清理群员 [群号]` / `… 确认` | 群满员时预览 / 执行清理最不活跃成员 |
| `/能力自检` | 探测当前 NapCat 支持哪些 QQ 能力（只读） |
| `/名片 @某人 内容`、`/头衔 @某人 内容` | 改群名片（需管理员）/ 专属头衔（需群主） |
| `/音色 …` | 查看 / 切换语音音色 |
| `/活跃度 安静\|正常\|话痨 [群号\|全局]` | 调群聊主动性档位 |
| `/邀请列表` `/同意邀请 <序号>` `/拒绝邀请 <序号>` | 好友申请与拉群邀请审批 |
| `/敏感词 add\|del\|list`、`/申诉列表` `/申诉通过 <编号>` `/申诉驳回 <编号>` | 敏感词与申诉处理 |
| `/精华`（回复消息）`/取消精华` `/精华列表` | 群精华管理 |

### 行为规则

- 仅白名单群进入业务；env 白名单 + 运行时添加的群合并生效
- 每个群可在面板独立开关业务（关闭后完全不响应，重启保留）
- 关键词回复：多条命中只回顺序第一条；同群同词条默认 8 秒冷却
- 群消息全量写入 jsonl 日志（含指令与关键词命中）

---

## 7. 常见故障排查

| 症状 | 排查顺序 |
|---|---|
| 群里 @ / 指令无反应 | ① 小号是否掉线（第 4 节重扫）→ ② `docker compose ps` 容器是否都 Up → ③ bot 日志有无 `connected` → ④ 群是否在白名单 / 被面板关闭业务 |
| 禁言/踢人失败 | 机器人小号是否为**群管理员**；看 bot 日志中的 API 错误信息 |
| 面板打不开 | ① SSH 隧道是否开启 → ② `curl -I http://127.0.0.1:8081/panel/` 在服务器上确认服务 → ③ 面板密码见 `.env` |
| NapCat WebUI token invalid | 强制刷新或无痕窗口；仍不行按第 4 节重置 token |
| bot 起不来 | `docker logs xingchao-bot` 看导入错误；多半是插件代码问题或 `.env` 格式错误 |
| `docker compose up` 提示 6099 被占用 | 有别的进程占了端口：`ss -tlnp | grep 6099`，停掉或改 compose 端口映射 |
| 改了 `.env` 不生效 | `docker compose up -d --force-recreate xingchao-bot` |

---

## 8. 开发与部署约定

- **仓库结构**：`bot/`（NoneBot2 应用，插件在 `bot/src/plugins/`）、`web/`（面板前端）、`docs/`、`napcat/`、`scripts/mock_napcat.py`
- **本地联调**：无需真实 QQ，`scripts/mock_napcat.py` 可模拟 NapCat 反向 WS 全链路（支持 at 段）
- **前端改动**：改 `web/src/` 后 `cd web && npm run build`，或直接 `docker compose up -d --build`
- **依赖管理**：bot 依赖在 `bot/pyproject.toml`；前端在 `web/package.json`；不装商店插件，全部自写
- **代码约定**：ruff 风格、类型注解、中文用户文案、异常兜底不抛崩、不把聊天内容写进 SQLite
- **提交规范**：feat/fix/chore/test 前缀 + 中文描述，直接推 `main`

### 8.1 官网更新日志同步（xingchao_site）

官网 <https://xingchao.dev/updates> 的更新日志有两层来源：

1. **运行时实时拉取**：页面加载时请求 GitHub API（机器人仓库最近 6 条提交），自动按 feat/fix 归类；
2. **兜底列表**：`xingchao_site/src/lib/github-updates.ts` 的 `projectUpdates`
   （GitHub API 不可用或限流时展示；国内网络下多数访客实际看到的是这一层，需保持较新）。

**产品更新后同步兜底列表**：

```bash
git clone git@github.com:08820048/xingchao_site.git && cd xingchao_site
# 1) 更新 src/lib/github-updates.ts 的 projectUpdates（最新在前；sha/date/url 取自本仓库提交）
#    并同步 scripts/check-github-updates.ts 里的条数断言
# 2) 校验 + 构建
npm run check:updates && npm ci && npm run build
# 3) 提交推送
git commit -am "docs(updates): 同步更新日志" && git push
# 4) 发布（Cloudflare Pages 为 Direct Upload 模式，push 不会自动部署）
npx wrangler pages deploy dist --project-name=xingchao --branch=main
```

> 本服务器只有机器人仓库的只读 Deploy Key，无法推送/发布官网仓库；
> 若要在服务器上直接完成同步与发布，需给官网仓库的 Deploy Key 开启写权限，
> 并提供 Cloudflare API Token（Pages:Edit）。

## 9. 相关文档

- `docs/DEPLOYMENT.md` — **换新服务器/迁移部署必读**
- `docs/REQUIREMENTS.md` — 需求说明书（第一期规格 + 第二期 17.x 各节）
- `docs/QQ机器人-星潮Bot需求文档.md` — 原始需求文档
- `napcat/README.md` — NapCat 扫码与 WS 客户端配置步骤
- `README.md` — 项目简介与快速开始

---

## 10. 稳定性说明（小内存服务器）

- 服务器内存 956MB，QQ（Electron）进程较重；已配置 3GB swap（/swapfile + /swapfile2，fstab 持久化）
- QQ 偶发 `Network service crashed` 属 Electron 已知问题，崩溃后 NapCat 会自动快速登录
  （webui.json 的 autoLoginAccount 已设为小号 QQ）；会话彻底失效时才需要重新扫码
- WebUI 会话会过期自动退出：用带 token 参数的完整网址打开即可免输入
  （`http://127.0.0.1:6099/webui?token=<token>`），建议保存为书签

---

## 11. 版本更新公告

- 每次发版（部署）前，在 `bot/src/changelog.json` **最前面**追加一条记录：
  `version`、`date`、`added` / `improved` / `fixed`（用户能感知的改动，写人话，别写内部实现）
- 部署后机器人连上 NapCat 时，若最新版本与上次已公告版本不同，会自动向白名单群推送一条规范公告
  （同时代替「已上线」通知）；推送成功后记录版本，不会重复发
- 不想打扰群友的小改动：该条记录加 `"notify": false`，只记录不推送
- 临时关闭公告：kv `update_notify` 设为 `false`；群内 `/更新日志` 可随时查看最近一次更新

---

## 12. 数据库备份与恢复

- 每天本地时间 03:30，机器人会对 `data/xingchao.db` 做在线备份（不锁库）并 gzip，
  存到 `data/backups/xingchao-YYYYMMDD-HHMMSS.db.gz`，默认保留最近 7 份；启动时若最近一份超过 24 小时也会补做
- 超管发 `/备份` 可立即备份并查看现有备份；kv `backup_enabled=false` 关闭，`backup_keep=N` 调整份数
- 恢复：`docker compose stop bot` → 解压需要的备份覆盖 `data/xingchao.db`（`gunzip -c xxx.db.gz > data/xingchao.db`）→ `docker compose start bot`
- 备份与数据库在同一块盘上，**不能防整机故障**：建议再定期把 `data/backups/` 同步到另一台机器或对象存储

## 13. 敏感词升级处罚的踢人防线

同一人在同一群 30 天内：第 1 次沿用规则，第 2 次禁言翻倍，第 3 次禁言 30 天，第 4 次起移出群聊。
踢出不可撤销，所以自动踢人前会过这些防线，任一不通过就改判「最长禁言」并私聊超管说明原因：

| 防线 | 默认 | 调整 |
|---|---|---|
| 群主 / 管理员 / 超管永不自动踢 | 固定 | — |
| 踢人豁免名单（仍会撤回、禁言） | 空 | `/敏感词 豁免 <QQ…>` · `取消豁免` · `豁免列表` |
| 每群每日自动踢出上限 | 3 人 | kv `sensitive_kick_daily_cap`（0 = 不限） |
| 试运行期：升级处罚首次生效起只禁言不踢，只通知「本应踢出」 | 7 天 | kv `sensitive_trial_days`（0 = 无试运行） |

试运行期内请观察面板「敏感词」页底部的词条命中统计与「违规记录」，确认误判率可接受后再放开。

## 14. 自动化测试

`bot/tests/` 覆盖升级处罚与踢人防线、词库匹配、数据库迁移与计数、满员清理选人、备份与恢复、更新公告。
本地运行（只装被测逻辑的依赖，不需要第三方插件）：

```bash
cd bot
uv run --no-project --python 3.12 --with "nonebot2[fastapi]" --with nonebot-adapter-onebot \
  --with aiosqlite --with httpx --with openai --with pypinyin --with opencc-python-reimplemented \
  --with pydantic --with pytest --with pytest-asyncio python -m pytest -q
```

推送到 main 或提 PR 时，GitHub Actions 会自动跑同样的测试。改动处罚规则、选人逻辑或数据库结构前后都请先跑一遍。

---

## 15. 后台自动任务速查

| 功能 | 触发 | 开关 / 参数（kv 或面板） | 排错 |
|---|---|---|---|
| 上线 / 下线通知（`presence.py`） | 连上 NapCat / 正常停机；后台每 30 秒写心跳 `presence_last_seen` | `presence_notify=false` 关闭 | 强杀或 QQ 掉线发不出下线通知，恢复后补发「已上线」并带离线时长 |
| 版本公告（`updates.py`） | 连上 NapCat 且 changelog 最新版本 ≠ `update_announced_version` | `update_notify=false` | 见第 11 节 |
| 内容推送（`feeds.py`） | 到点；内容未就绪每 10 分钟重试，超 120 分钟放弃并私聊超管 | 面板「内容推送」页；`feed_last_{id}` 去重 | 想重发：`/推送 立即 <编号>` |
| 定时任务（`scheduler.py`） | 每 30 秒扫描，北京时间 | 面板「定时任务」页 | @全体次数不足时降级为普通消息并通知超管 |
| 群活跃周报（`weeklyreport.py`） | 配置的星期 / 时间，每群每天最多一次 | `report_enabled` / `report_weekday` / `report_time` / `report_scope` | 同上页 |
| 群满员清理（`groupfull.py`） | 入群时 + 每 30 分钟巡检；满员 24 小时内只提醒一次 | `groupfull_enabled`(true) / `groupfull_auto_kick`(false) / `groupfull_kick_count`(100) / `groupfull_activity_days`(30) / `groupfull_protect_days`(7) | 默认只通知超管，需 `/清理群员 确认`；执行需机器人是管理员 |
| 数据库备份（`backup.py`） | 每天 03:30；启动时最近备份超 24 小时则补做 | `backup_enabled` / `backup_keep` | 见第 12 节 |

---

## 16. 新功能的配置项

| 功能 | 配置 | 说明 |
|---|---|---|
| 联网搜索 | 环境变量 `XINGCHAO_SEARCH_PROVIDER`=`duckduckgo`(默认)\|`brave`\|`searxng`；`XINGCHAO_SEARCH_API_KEY`（brave）；`XINGCHAO_SEARCH_URL`（searxng） | duckduckgo 靠解析页面，结构变了会搜不到结果，此时换 brave / searxng；全局每日 300 次 |
| AI 绘图 | kv `image_model`（留空 = 不启用） | 要求默认 AI 提供商支持 OpenAI 兼容的 `images.generate`；提示词先过敏感词库；每人每天 3 张、全局每天 30 张 |
| 入群验证 | kv `joinverify_enabled`(false) / `joinverify_action`(notify\|kick) / `joinverify_minutes`(5) | 待验证名单在内存里，重启后不再追踪重启前入群的人 |
| 红包提醒 | kv `redpacket_notify`(默认开) | 只提醒不领取；NapCat 不上报红包时不会触发，看日志里有没有"检测到疑似红包" |
| 已读标记 | kv `mark_read_enabled`(默认开) | 每 10 分钟对白名单群标记已读 |
| 个人提醒 / 投票 | 无 | 提醒存 SQLite `reminders` 表，投票只存内存 |
