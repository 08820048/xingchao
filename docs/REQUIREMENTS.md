先核对 NoneBot2 和 NapCat 当前的项目模板与 Docker 接法，避免需求文档里的目录和配置过时。# 星潮 Xingchao 软件需求说明书（可直接开发）  
  
版本：1.0    
日期：2026-09-01    
项目名：星潮 / Xingchao    
域名：[https://xingchao.dev](https://xingchao.dev)    
仓库名`xingchao`    
读者：编码 Agent（按本文实现，未写明的不要自行加范围）  
  
---  
  
## 0. 一句话  
  
在 Linux 服务器上用 **NapCatQQ（小号登录）+ NoneBot2（Python 插件）** 做一个 QQ 群助手：只处理白名单群；默认只响应指令和关键词；不接腾讯官方开放平台。  
  
---  
  
## 1. 已拍板决策（Agent 不得再问）  
  
| 项 | 决定 |  
|----|------|  
| 名称 | 星潮，英文 `xingchao` |  
| 平台 | 仅 QQ 群 + 超管私聊指令 |  
| 协议 | 非官方。NapCat + OneBot v11 |  
| 框架 | NoneBot2 + `nonebot-adapter-onebot`（仅 V11） |  
| 群范围 | 白名单。配置里先放空列表，用环境变量填 |  
| 管理功能 | **第一期不做**禁言 / 撤回 / 踢人 / 入群审批 |  
| AI | **第一期不做** |  
| 触发 | 指令 + 可选关键词自动回复。不默认每条群消息都回 |  
| 数据 | 第一期 SQLite + JSON 配置 |  
| 部署 | Docker Compose，两个服务 |  
| 域名 | `xingchao.dev` 只写进帮助文案。第一期不挂 Web 站、不反代 NapCat WebUI |  
  
---  
  
## 2. 目标与非目标  
  
### 2.1 目标（P0）  
  
1. NapCat 扫码登录后，NoneBot 经反向 WebSocket 收到事件。    
2. 仅白名单 `group_id` 进入业务。    
3. 全量记录白名单群文本日志（本地文件）。    
4. 指令`/help` `/ping` `/id` `/status`。    
5. 关键词回复（精确 / 包含），带冷却。    
6. 超管可开关某群、开关关键词模块、热重载词库。    
7. Compose 一键起停，重启后自动连回。  
  
### 2.2 非目标（第一期禁止实现）  
  
- 腾讯开放平台 / 官方 Bot API    
- 多平台（Telegram、Discord 等）    
- LLM / RAG / 联网搜索问答    
- 禁言、撤回、踢人、改名片、处理入群申请    
- 点歌、游戏、涩图、签到金币    
- 公网暴露 NapCat WebUI    
- 把群消息同步到第三方    
  
商店插件第一期也不要装，全部自写本地插件，避免依赖漂移。  
  
---  
  
## 3. 系统架构  
  
```  
QQ 群  
  ↓  
xingchao-napcat   (mlikiowa/napcat-docker)  
  ↓  WebSocket Client  
ws://xingchao-bot:8080/onebot/v11/ws  
  ↓  
xingchao-bot      (NoneBot2 + OneBot V11)  
  ↓  
data/xingchao.db + data/replies.json + logs/  
```  
  
连接方式固定为：  
  
- NoneBot：反向 WS 服务端`0.0.0.0:8080`，路径 `/onebot/v11/ws`    
- NapCat：网络配置里加 **WebSocket 客户端**，URL 为 `ws://xingchao-bot:8080/onebot/v11/ws`    
- 双方同一 `ONEBOT_ACCESS_TOKEN`  
  
Docker 使用自定义 bridge 网络，**不要** `network_mode: host`。    
NapCat WebUI 映射 `127.0.0.1:6099:6099`，只本机或 SSH 隧道访问。  
  
---  
  
## 4. 仓库结构（必须按此创建）  
  
```  
xingchao/  
├── [README.md](http://README.md)  
├── LICENSE                 # MIT  
├── .gitignore  
├── .env.example  
├── docker-compose.yml  
├── docs/  
│   └── [REQUIREMENTS.md](http://REQUIREMENTS.md)     # 本文副本  
├── napcat/  
│   └── [README.md](http://README.md)           # 扫码与 WS 客户端配置步骤  
├── bot/  
│   ├── Dockerfile  
│   ├── [bot.py](http://bot.py)  
│   ├── pyproject.toml  
│   ├── .env  
│   ├── .[env.prod](http://env.prod)  
│   └── src/  
│       ├── __init__.py  
│       ├── [config.py](http://config.py)  
│       ├── [store.py](http://store.py)  
│       ├── [permission.py](http://permission.py)  
│       └── plugins/  
│           ├── __init__.py  
│           ├── [whitelist.py](http://whitelist.py)  
│           ├── [logger.py](http://logger.py)  
│           ├── [basic.py](http://basic.py)  
│           ├── [reply.py](http://reply.py)  
│           └── [admin.py](http://admin.py)  
└── data/                   # compose 挂载，git 忽略内容  
    ├── .gitkeep  
    ├── xingchao.db  
    └── replies.json  
```  
`bot.py` 只做 init、注册 OneBot V11、加载 `src/plugins`。业务不准写进 `bot.py`。  
  
---  
  
## 5. 技术栈与版本下限  
  
| 组件 | 要求 |  
|------|------|  
| Python | 3.11+ |  
| NoneBot2 | `nonebot2[fastapi]>=2.3` |  
| 适配器 | `nonebot-adapter-onebot>=2.4`，只启用 V11 |  
| 驱动 | FastAPI + websockets/httpx（NoneBot 默认即可） |  
| DB | `aiosqlite` |  
| NapCat 镜像 | `mlikiowa/napcat-docker:latest` |  
| Bot 镜像 | 自建`python:3.12-slim` |  
| 编排 | Docker Compose v2 |  
  
代码风格：ruff；类型注解要写；日志用 `nonebot.logger`。  
  
---  
  
## 6. 配置  
  
### 6.1 根目录 `.env.example`  
  
```  
XINGCHAO_SUPERUSERS=123456789  
XINGCHAO_GROUP_WHITELIST=111111111,222222222  
ONEBOT_ACCESS_TOKEN=change-me-long-random  
NAPCAT_UID=1000  
NAPCAT_GID=1000  
```  
  
### 6.2 `bot/.env.prod`  
  
```  
HOST=0.0.0.0  
PORT=8080  
ENVIRONMENT=prod  
DRIVER=~fastapi+~httpx+~websockets  
LOG_LEVEL=INFO  
SUPERUSERS=["123456789"]  
COMMAND_START=["/", "星潮"]  
NICKNAME=["星潮", "xingchao"]  
ONEBOT_ACCESS_TOKEN=change-me-long-random  
ONEBOT_V11_SECRET=  
XINGCHAO_GROUP_WHITELIST=111111111,222222222  
XINGCHAO_LOG_DIR=/app/data/logs  
XINGCHAO_DB_PATH=/app/data/xingchao.db  
XINGCHAO_REPLIES_PATH=/app/data/replies.json  
XINGCHAO_REPLY_COOLDOWN=8  
XINGCHAO_SITE=[https://xingchao.dev](https://xingchao.dev)  
```  
`SUPERUSERS` 必须与 `XINGCHAO_SUPERUSERS` 同步。Agent 实现时`src/config.py` 用 pydantic 读环境变量，白名单解析为 `set[int]`。  
  
空白名单 = **不处理任何群**（安全默认），只允许超管私聊 `/status`。  
  
---  
  
## 7. Docker Compose 要求  
  
服务名：  
  
- `xingchao-napcat`  
- `xingchao-bot`  
  
要点：  
  
1. 同一网络 `xingchao`。    
2. bot 先起，napcat `depends_on` bot。    
3. 卷：    
   - `./napcat/config` → NapCat 配置    
   - `./napcat/qq` → `/app/.config/QQ`    
   - `./data` → `/app/data`    
4. 端口：    
   - `127.0.0.1:6099:6099` WebUI    
   - bot 的 8080 **不要映射到公网**，只给内部网络。    
5. `restart: unless-stopped`    
6. `NAPCAT_UID` / `NAPCAT_GID` 从 env 传入。  
  
NapCat 的 OneBot 客户端配置文件可预置模板（若镜像支持挂载 network 配置），URL 写死：  
`ws://xingchao-bot:8080/onebot/v11/ws`  
  
token 与 `ONEBOT_ACCESS_TOKEN` 相同。若镜像必须在 WebUI 里手动加，把步骤写进 `napcat/README.md`，不要假装已经自动连上。  
  
---  
  
## 8. 功能规格  
  
### 8.1 白名单 `whitelist.py`  
  
- 依赖：所有群聊 matcher 先过白名单。    
- 实现`nonebot.rulegroup_id in config.group_whitelist`。    
- 私聊：仅 `SUPERUSERS` 可触发管理指令；普通私聊忽略。    
- 非白名单群：不回复、不写业务库；debug 日志可记一条 ignore。  
  
### 8.2 监听日志 `logger.py`  
  
- `on_message`，priority 低`block=False`。    
- 只处理 `GroupMessageEvent` 且在白名单。    
- 写入`data/logs/group-{group_id}-YYYY-MM-DD.jsonl`    
- 每行 JSON`time, group_id, user_id, message_id, raw_plain`    
- 不回消息。    
- 纯图片/无文本`raw_plain` 为空字符串，仍记一行。  
  
### 8.3 基础指令 `basic.py`  
  
均需白名单群，或超管私聊。  
  
| 指令 | 行为 |  
|------|------|  
| `/help` 或 `星潮帮助` | 短帮助 + 官网 `https://xingchao.dev` |  
| `/ping` | 回复 `pong` |  
| `/id` | 回复当前 `group_iduser_idself_id` |  
| `/status` | 仅超管。回复：在线、已加载插件名、白名单群数量、词条数量 |  
  
帮助文案固定开头：  
  
```  
星潮 Xingchao  
[https://xingchao.dev](https://xingchao.dev)  
```  
  
### 8.4 关键词回复 `reply.py`  
  
词库文件 `data/replies.json`：  
  
```json  
{  
  "version": 1,  
  "items": [  
    {  
      "id": "welcome",  
      "enabled": true,  
      "match": "exact",  
      "pattern": "你好星潮",  
      "reply": "在。发送 /help 查看指令。",  
      "cooldown": 8  
    }  
  ]  
}  
```  
  
规则：  
  
- `match`: `exact` 或 `contains`（第一期不要正则，避免 ReDoS）    
- 只对纯文本    
- 同一群同一词条冷却默认 8 秒，可被条目覆盖    
- 多条命中只回 **文件中第一条**    
- 回复失败打 error 日志，不抛崩    
  
预置 1 条示例词条即可。  
  
### 8.5 超管 `admin.py`  
  
仅 `SUPERUSERS`，群内或私聊可用。  
  
| 指令 | 行为 |  
|------|------|  
| `/reply reload` | 重载 `replies.json` |  
| `/reply list` | 列出 id / match / pattern / enabled |  
| `/group list` | 列出内存中白名单 |  
| `/plugin reply on` `/plugin reply off` | 进程内开关关键词模块（重启丢失可接受，或写入 sqlite） |  
  
第一期 **不提供**运行时改白名单到 env 的命令（改 env 后重启）。可把开关状态写入 sqlite，表 `kv(key, value)`。  
  
---  
  
## 9. 数据  
  
SQLite 表最少：  
  
```sql  
CREATE TABLE IF NOT EXISTS kv (  
  key TEXT PRIMARY KEY,  
  value TEXT NOT NULL  
);  
```  
  
可选统计表（有就更好，没有不阻塞验收）：  
  
```sql  
CREATE TABLE IF NOT EXISTS msg_stat (  
  group_id INTEGER,  
  day TEXT,  
  count INTEGER,  
  PRIMARY KEY (group_id, day)  
);  
```  
  
禁止把完整聊天内容写入 SQLite（内容只进 jsonl 日志）。  
  
---  
  
## 10. 权限与安全  
  
1. 必须配置非空 `ONEBOT_ACCESS_TOKEN`（至少 16 位）。空 token 启动时警告，文档写明生产禁止。    
2. WebUI 不映射 `0.0.0.0`。    
3. README 写明：只用小号；可能封号；与电脑 QQ 不要同时登录同一号。    
4. `.env` / `data/` / `napcat/qq` / `napcat/config` 进 `.gitignore`。    
5. 日志不要打印 token。    
6. 不实现任何「获取他人登录凭据」类功能。  
  
---  
  
## 11. 插件优先级  
  
| 插件 | priority | block |
|------|----------|-------|
| logger | 0 | False（必须在所有 block matcher 之前，保证全量日志） |
| admin 指令 | 1 | True |
| basic 指令 | 5 | True |
| reply | 20 | True（命中才 block） |
  
指令 matcher 用 `on_commandCOMMAND_START` 为 `["/", "星潮"]`，因此 `星潮帮助` 若不好实现，则 `/help` 与 `星潮 help` 即可。    
「星潮帮助」作为 `on_fullmatch({"星潮帮助", "星潮 help"})` 额外加一条。  
  
---  
  
## 12. README 必须包含  
  
1. 项目名、域名、架构图（文本即可）    
2. 风险：非官方协议、小号    
3. 填 `.env`    
4. `docker compose up -d`    
5. 看 napcat 日志拿 WebUI token，本机打开 `http://127.0.0.1:6099/webui` 扫码    
6. 配置 WS 客户端指向 `ws://xingchao-bot:8080/onebot/v11/ws`    
7. 小号进群，群内 `/ping`    
8. 验收清单（第 14 节）  
  
---  
  
## 13. 编码约定（Agent 必须遵守）  
  
- 插件目录作包：每个功能一个模块，不要一个 2000 行文件。    
- 禁止 `from nonebot.adapters.qq`（那是官方适配器）。    
- 只用 `nonebot.adapters.onebot.v11`。    
- 发消息用 `matcher.send` / `bot.send`，捕获异常。    
- 不要在事件处理里做阻塞同步 HTTP。    
- 中文用户可见文案；代码标识符英文。    
- 不提交 `__pycache__.venv`、QQ 数据。  
  
---  
  
## 14. 验收（全部勾上才算完成）  
  
- [ ] 仓库结构与第 4 节一致    
- [ ] `docker compose config` 能解析    
- [ ] bot 容器监听 8080，日志无适配器导入错误    
- [ ] 文档写清 NapCat 扫码 + WS 客户端    
- [ ] 白名单为空时群消息不回复    
- [ ] 白名单群 `/ping` → `pong`    
- [ ] `/id` 回显三个 ID    
- [ ] `/help` 含「星潮」和 `https://xingchao.dev`    
- [ ] 发送「你好星潮」能按词库回复    
- [ ] 冷却期内重复触发不刷屏    
- [ ] `data/logs/` 产生 jsonl    
- [ ] 非超管无法 `/status/reply reload`    
- [ ] `.gitignore` 排除密钥和 QQ 会话    
  
---  
  
## 15. 实现顺序（按此提交）  
  
1. 仓库骨架、gitignore、env 示例、README 框架    
2. `docker-compose.yml` + `bot/Dockerfile` + `bot.py` + `pyproject.toml`    
3. `config.py` `permission.py` `store.py`    
4. `whitelist` + `logger` + `basic`    
5. `reply` + 示例 `replies.json`    
6. `admin`    
7. `napcat/README.md` 补全登录步骤    
8. 自检第 14 节    
  
---  
  
## 16. 给 Agent 的启动指令  
  
&gt; 在当前工作区创建名为 `xingchao` 的项目，严格按本需求第 4–15 节实现第一期。不要添加 AI、群管、官方 QQ 适配器或商店插件。完成后输出：已创建文件列表、如何启动、以及尚未自动完成（必须人工扫码）的步骤。  
  
---  
  
把上面整份存成 `docs/REQUIREMENTS.md` 丢给 Agent 即可开工。人工只剩：填 QQ 号 / token、扫码、把小号拉进群。
---

## 17. 第二期规格（已实现部分）

### 17.1 活跃统计 `stats.py`

- 指令 `/stats [yesterday|昨日|昨天]`，默认当天
- 群内：当前群消息总数、参与人数、发言 Top5（user_id + 条数）
- 超管私聊：当日所有群总览（group_id + 消息数）
- 数据源：`msg_stat`（群级）、`msg_stat_user`（群+天+用户级），由 logger 插件落库；只计数不存内容

### 17.2 白名单运行时管理 `admin.py` + `permission.py`

- `/group add <群号>`：加入运行时白名单，立即生效，写 SQLite `kv(group_whitelist_runtime)`，重启保留
- `/group del <群号>`：移出运行时白名单；env 来源的群不允许运行时移除（提示改 env）
- `/group list`：显示合并白名单，标注来源（env / 运行时）
- 生效白名单 = `XINGCHAO_GROUP_WHITELIST` ∪ 运行时白名单；启动时从 kv 恢复

### 17.3 群管 `groupadmin.py`

- 全部仅超管（SUPERUSER），仅群聊可用
- `/mute @某人 [分钟]`（默认 10，上限 30 天）、`/unmute @某人`、`/banall on|off`、
  `/kick @某人`、`/recall`（回复目标消息或 `/recall <message_id>`）
- API 失败（权限不足等）时回复友好提示，不抛崩
- 新人进群欢迎：白名单群 GroupIncrease 事件，默认开启，`/plugin welcome on|off` 开关
  （写 kv `welcome_enabled`，重启保留）；机器人自己进群不触发

### 17.4 mock 脚本增强

- `scripts/mock_napcat.py` 支持把 `@QQ号` 文本解析为 at 消息段，可联调群管指令

### 17.5 Web 管理面板 `panel.py`

- 挂载在 NoneBot 的 FastAPI 应用（与反向 WS 共用 8080），compose 映射 `127.0.0.1:8081:8080`
- 页面 `/panel`（内嵌单页应用：状态 / 统计 / 日志 / 词库 / 白名单 五个标签页）
- 认证：`XINGCHAO_PANEL_PASSWORD`（键需在 bot/.env.prod 登记以便 NoneBot 识别，
  实际值由根 .env 注入覆盖）；留空则启动时随机生成并打印日志；Cookie 存 sha256(password)
- API（均校验 Cookie）：
  - `GET /panel/api/status` 运行状态（uptime、插件、白名单、词条数、模块开关）
  - `GET /panel/api/stats?day=` 各群消息量 / 参与人数 / Top5
  - `GET /panel/api/logfiles` + `GET /panel/api/logs?name=&tail=`（仅允许 group-*.jsonl
    纯文件名，防目录穿越）
  - `GET/POST /panel/api/replies` 词库读取 / 校验保存并热重载（校验 id 重复、match 枚举、必填项）
  - `GET/POST /panel/api/groups` 白名单查看 / 运行时 add|del
- 面板仅经 SSH 隧道访问，不暴露公网

### 17.6 面板前端升级（coss ui）

- 前端工程 `web/`：Vite + React + TypeScript + Tailwind CSS v4，
  组件采用 [coss ui](https://coss.com/ui/)（shadcn CLI 接入 `@coss/style` 预设，Base UI 基座）
- `vite.config.ts` 设置 `base: "/panel/"`，构建产物由 bot 的 FastAPI 以
  StaticFiles(html=True) 托管在 `/panel`（mount 在 API 路由之后注册，互不冲突）
- Dockerfile 改为多阶段：node:22-alpine 编译 `web/` → python:3.12-slim 拷贝 `web/dist`
- 构建上下文改为仓库根目录（`build.context: .`），根目录 `.dockerignore` 排除
  `web/node_modules`、`.git`、运行数据等
- 内嵌单页保留为 `web/dist` 缺失时的回退方案

### 17.7 @机器人 响应 `mention.py`

- 判定依据为 `event.to_me`（适配器会把指向自身的 at 段剥离并置 to_me），
  覆盖成员 @ 机器人、昵称（星潮/xingchao）前缀唤起两种方式
- 非指令内容：@发起者 + 简短回应；内容含「帮助 / help」→ 回复帮助菜单（basic 别名优先）
- 同群冷却默认取 XINGCHAO_REPLY_COOLDOWN（8 秒），避免刷屏
- 仅白名单群生效；指令消息不受影响

### 17.8 进群欢迎可配置化

- 欢迎语支持占位符：`{at}` = @新人、`{qq}` = 新人 QQ、`{group}` = 群号
- 配置持久化 SQLite `kv`：`welcome_enabled`（开关，默认开）、`welcome_text`（文案，缺省为内置默认语）
- 指令扩展：`/welcome view` 查看当前文案、`/welcome set <文案>` 更新（上限 1000 字）
- 面板 API：`GET/POST /panel/api/welcome`（enabled + text，POST 校验非空与长度）
- 面板「仪表盘」页新增欢迎配置卡片（开关 + 文案编辑 + 保存）
- 触发：GroupIncreaseNoticeEvent，机器人自己进群不触发；发送用显式
  `send_group_msg`（notice 事件不依赖 bot.send 的目标推导）
- 修复：GROUP_WHITELIST 规则函数注解放宽为 `Event`（按 group_id 属性判断），
  使消息与群通知（进群事件）都能过白名单规则

### 17.9 超管管理（面板 + 指令）

- 超管 = env 基础超管（XINGCHAO_SUPERUSERS，不可运行时移除）+ 运行时超管（可增删）
- 运行时超管持久化 SQLite `kv(superusers_runtime)`，启动恢复；增删立即同步 NoneBot
  SUPERUSERS 配置（合并基数必须取 env 基础配置，否则移除不生效）
- 指令：`/superuser list | add <QQ> | del <QQ>`（仅现有超管可用）
- 面板 API：`GET/POST /panel/api/superusers`；「超管」页展示列表与来源、运行时增删

### 17.10 AI 问答 `ai.py`

- LLM 接入：通用 OpenAI Chat Completions 兼容协议（当前对接 B.AI，base_url `https://api.b.ai/v1`）
- 触发：to_me 且非指令的群消息（@机器人 / 昵称唤起）；AI 未开启时由 mention 回固定问候语
- 配置分层：`XINGCHAO_AI_BASE_URL` / `XINGCHAO_AI_API_KEY` 走环境变量（缺失则功能自动禁用）；
  开关 / 模型 / 系统提示词 / 会话轮数 / 每日限额存 SQLite kv（面板可改，即时生效）
- 上下文：每群保留最近 N 轮会话（内存），`/ai clear` 清空
- 护栏：每群 / 每人每日调用上限（持久化到 kv，按天统计），超限静默；回复超长截断
- 指令：`/ai on|off|status|clear|test <问题>`（超管）
- 面板：GET/POST `/panel/api/ai`；「AI」页含开关、模型、人设、限额、今日用量

### 17.11 AI 凭据面板化管理

- API 地址与密钥存 SQLite kv（`ai_base_url` / `ai_api_key`），环境变量仅作初始兜底
- 面板「AI」页新增「API 连接」卡片：地址可编辑、Key 脱敏显示（留空=保持不变），保存即时生效
- 客户端按凭据签名自动重建，无需重启

### 17.12 AI 工具调用（自然语言操控群聊）

- chat() 支持多轮 Function Calling 循环（最多 4 轮），LLM 可连续调用工具后作答
- 工具注册表：schema（OpenAI function 格式，传 SDK）与执行器（本地闭包）分离
- 工具清单（14 个，按权限过滤）：
  - 所有人：get_group_info / get_member_list / get_member_info / get_active_stats
  - 仅超管：mute_member / unmute_member / kick_member / set_whole_ban /
    list_whitelist / add_whitelist_group / remove_whitelist_group /
    set_group_business / list_replies / reload_replies
- 权限：请求前按事件发起者是否超管过滤工具；越权调用返回错误说明
- 工具执行异常转述给 LLM（如机器人非群管理员），由 AI 向用户解释
- 系统提示词告知工具能力；敏感操作（禁言/踢人/改配置）要求 AI 先确认

### 17.13 入群申请智能审批 + 退群播报 `request.py`

- 入群申请处理链：AI 智能判断（首选）→ 程序规则兜底（关键词命中=通过；空/敷衍=转人工）
  → 转人工（通知超管私聊，携带序号）
- 审批执行：`set_group_add_request`；转人工凭证存内存（重启失效）
- 指令：`/approve <序号>`、`/reject <序号> [理由]`、`/pending`（仅超管）
- 模式：ai / manual / auto_approve / auto_reject；AI 失败兜底：manual / approve / reject
- 配置（面板可改，持久化 kv）：模式、验证问题、兜底策略、规则关键词、退群播报开关
- 退群：群内播报（可开关）；机器人被踢（kick_me）通知超管
- 面板：GET/POST `/panel/api/join`、POST `/panel/api/join/resolve`；「加群审批」页
  （待审批列表 + 通过/拒绝按钮 + 策略配置 + 退群播报开关）

### 17.14 AI 场景身份注入与开发者信息

- @机器人提问时自动注入场景上下文：提问者 QQ、群昵称、入群日期、身份
  （群主/管理员/普通成员）、所在群名称与人数 —— AI 天然回答"我是谁"类问题
- 开发者信息（xingchao_developer_id/blog/site，env 可配）注入系统上下文：
  用户问开发者/作者时，AI 介绍 QQ 2217021563、博客 https://xuyi.dev、
  官网 https://xingchao.dev，并回复中包含 {dev_at} 占位符（程序替换为真实@开发者）
- 场景获取失败不影响回答（best-effort）
