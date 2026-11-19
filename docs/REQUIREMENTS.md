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
- `/禁言 @某人 [分钟]`（默认 10，上限 30 天）、`/解除禁言 @某人`、`/全体禁言 开|关`、
  `/踢出 @某人`、`/recall`（回复目标消息或 `/recall <message_id>`）
- API 失败（权限不足等）时回复友好提示，不抛崩
- 新人进群欢迎：白名单群 GroupIncrease 事件，默认开启，`/plugin welcome on|off` 开关
  （写 kv `welcome_enabled`，重启保留）；机器人自己进群不触发

### 17.4 mock 脚本增强

- `scripts/mock_napcat.py` 支持把 `@QQ号` 文本解析为 at 消息段，可联调群管指令

### 17.5 Web 管理面板 `panel/`

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

- LLM 接入：通用 OpenAI Chat Completions 兼容协议（任意兼容服务商均可，如智谱 GLM `https://open.bigmodel.cn/api/paas/v4`；面板「AI」页可改并支持拉取模型列表）
- 触发：to_me 且非指令的群消息（@机器人 / 昵称唤起）；AI 未开启时由 mention 回固定问候语
- 配置分层：`XINGCHAO_AI_BASE_URL` / `XINGCHAO_AI_API_KEY` 走环境变量（缺失则功能自动禁用）；
  开关 / 模型 / 系统提示词 / 会话轮数 / 每日限额存 SQLite kv（面板可改，即时生效）
- 上下文：每群保留最近 N 轮会话（内存），`/ai clear` 清空
- 护栏：每群 / 每人每日调用上限（持久化到 kv，按天统计），超限静默；回复超长截断
- 指令：`/ai on|off|status|clear|vision <模型>|test <问题>`（超管）
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
- 系统提示词只描述「是个什么样的人」，不写岗位与能力清单（工具 schema 已随请求传给模型）；
  敏感操作（禁言/踢人/改配置）要求 AI 先确认

### 17.13 入群申请智能审批 + 退群通知管理员 `request.py`

- 入群申请处理链：AI 智能判断（首选）→ 程序规则兜底（关键词命中=通过；空/敷衍=转人工）
  → 转人工（通知超管私聊，携带序号）
- 审批执行：`set_group_add_request`；转人工凭证存内存（重启失效）
- 指令：`/通过 <序号>`、`/拒绝 <序号> [理由]`（中文主命令，英文别名 /approve、/reject 保留）、`/pending`（仅超管）
- 模式：ai / manual / auto_approve / auto_reject；AI 失败兜底：manual / approve / reject
- 配置（面板可改，持久化 kv）：模式、验证问题、兜底策略、规则关键词、退群通知开关
- 退群：私聊通知超管（可开关），群内不播报；机器人被踢（kick_me）同样通知超管
- 面板：GET/POST `/panel/api/join`、POST `/panel/api/join/resolve`；「加群审批」页
  （待审批列表 + 通过/拒绝按钮 + 策略配置 + 退群通知开关）

### 17.14 AI 场景身份注入与开发者信息

- @机器人提问时自动注入场景上下文：提问者 QQ、群昵称、入群日期、身份
  （群主/管理员/普通成员）、所在群名称与人数 —— AI 天然回答"我是谁"类问题
- 开发者信息（xingchao_developer_id/blog/site，env 可配）注入系统上下文：
  用户问开发者/作者时，AI 介绍 QQ 2217021563、博客 https://xuyi.dev、
  官网 https://xingchao.dev，并回复中包含 {dev_at} 占位符（程序替换为真实@开发者）
- 场景获取失败不影响回答（best-effort）

### 17.15 GitHub 趋势 + 定时任务

- github 插件：解析 github.com/trending（10 分钟缓存），/trending 指令 + AI 工具
  get_github_trending（自然语言"看看今天GitHub热门"即触发）
- scheduler 插件：SQLite scheduled_tasks 表；后台协程每 30 秒扫描，
  重复规则 daily/weekdays/weekend/weekly(指定星期)/once(指定日期)；北京时间触发；
  支持 @全体成员；面板「定时任务」页增删改；/task list 指令查看

### 17.16 AI 接管入群审批（工具）

- AI 工具新增 3 个（仅超管）：get_pending_join_requests（查看待审批）、
  approve_join_request / reject_join_request（通过/拒绝，直接调用 set_group_add_request）
- 修复：join_request 匹配器补上白名单规则（此前非白名单群的申请也会被处理）；
  转人工条目超 1 小时自动清理（QQ 侧凭证此时已过期）
- AI 可按自然语言批量审批（"回答正经的通过，敷衍的拒绝"）

### 17.17 博客知识关联推荐（xuyi.dev）

- blog 插件（`blog.py` + `src/blog_kb.py`）：本地索引开发者博客 xuyi.dev 的文章与作品集，
  群聊话题相关时 AI 优先推荐（如 Mac 剪贴板 → Clibo；学 Git → 《GitHub 小册》）
- 数据源：RSS（最新 50 篇，含正文节选）+ sitemap（全量 73+ 篇；较老文章单篇抓取，30 天缓存）
- 相关性：中文 2-4 字 n-gram + 英文词倒排索引 + IDF 打分；停用词与语气助词切分降噪；
  作品集别名人工维护（Clibo/剪贴板、Berth/端口、Chupin/简历 等 12 款）
- 注入：对话（ai.py chat()）与主动插话（proactive.py）自动注入「博主内容·优先参考」；
  强相关 + 求助语气时主动接话门槛降为 1 条消息（保留 5 分钟/群冷却与每日上限）
- 指令：`/blog status|refresh|list|on|off`（超管）；AI 工具 `search_blog` / `get_blog_post`（所有人）、
  `refresh_blog` / `set_blog_enabled`（超管）
- 开关 `blog_enabled`（默认开）；面板「博客」页（状态/刷新/文章/作品集）+ 仪表盘模块开关
- 刷新：启动即检查，每 6 小时检查一次、超 24 小时重新抓取；失败保留旧索引并记录错误

### 17.18 广告招商与群内广告治理 `ads.py`

- 招商信息（kv 可配，面板「广告」页）：`ad_text` 合作说明、`ad_price` 报价单、
  `ad_contact` 对接人 QQ（留空取首个超管）；`/广告` 给任何人看这三段，`/广告 报价` 只看报价
- 欢迎语新增 `{ad}` 占位符，展开成 `AD_WELCOME_LINE`
  「对于要打广告的朋友：如果你认为你的产品有价值，请联系管理员进行广告报价！」
  （带对接人 QQ）；默认欢迎语内置这一行，老部署改过 `welcome_text` 的需手动加 `{ad}`
- 群内自动治理（白名单群，`on_message` priority=4，排在敏感词之后）：
  - 识别：纯正则规则，不调 AI。强特征词（代发广告 / 接推广 / 招代理 / 低价代充 …）
    命中即判；组合特征词（推广 / 引流 / 返利 / 私聊 …）累计计分，链接与联系方式各加分；
    吐槽举报语境、引用回复、长文、问句分别降分或提高阈值，减少误伤
  - 咨询类（「能发广告吗」「推广怎么收费」）不撤回，直接回招商说明（转化入口）
  - 合作方名单 `ad_partners` 内的 QQ 完全放行；群主 / 管理员 / 超管发的不管
  - 动作 `ad_action`：reply（默认，撤回 + 群里 @ 提示走商务合作）/ silent（只撤回）/
    log（只通知超管，不动消息）；`ad_cooldown` 控制同群提示冷却，冷却内只撤回不提示
  - 命中记录写入 `punishments`（source='ad'，counted=0 不计入敏感词升级处罚），
    面板「违规记录」可查，当事人可 `/申诉`
- 指令（超管）：`/广告 设置 <内容>`、`/广告 报价 设置 <内容>`、`/广告 联系 <QQ>`、
  `/广告 合作 列表|添加|删除 <QQ>`、`/广告 动作 提示|静默|记录`、`/广告 冷却 <秒>`、
  `/广告 通知 开|关`、`/广告 开|关`；`/模块 广告 开|关` 也可切总开关
- AI 工具：`get_ad_policy`（所有人）、`set_ad_policy`（超管，改说明/报价/联系人/动作/合作方）
- 面板 API：`GET/POST /panel/api/ads`；「广告」页（开关 + 说明 + 报价 + 联系人 + 动作 +
  冷却 + 通知 + 合作方增删 + `{ad}` 占位符提示）；仪表盘模块开关支持 `ads`

### 17.19 群聊回忆 `recall.py`

- 目的：群里说过就沉底的问题（「上次谁发过那个教程」「团购表格在哪」「群规说过不能干啥」）
  用一句话翻回来，让机器人从"签到工具"变成"群记忆"
- 数据来源：只读 `logger.py` 已写的 `data/logs/group-*.jsonl`，**不额外落盘**；
  索引全部在内存（与 `awareness.py` 的群聊缓冲同一原则），重启后从日志重建，
  继续遵守「完整聊天内容不进数据库」的约定
- 增量：按文件记录已读**字节偏移**，只读新追加部分；末尾不完整的行不推进偏移，
  避免读到半行 JSON；文件被截断（覆盖写）时从头重读
- 容量：每群保留最近 `recall_max_docs`（默认 2 万）条，超出丢弃更早的；
  面板调小上限后立刻收敛，不用重启
- 检索：与博客知识库共用 `src/textindex.py` 的中文 2-4 字 n-gram + 英文单词分词；
  打分 = 命中词权重之和 × 覆盖率系数 × 近期加成（90 天内最多 +30%）；
  只命中停用词（「怎么」「有没有」）不返回结果
- 指令：`/回忆 <关键词>`（别名 `/recall`）、`/回忆 状态`（索引条数/覆盖时间）；
  AI 工具 `recall_chat`（query + days，模型可把「上周」翻成 7）
- 开关与配置（kv，面板「AI」页可改）：`recall_enabled`、`recall_days`（默认 90）、
  `recall_max_docs`、`recall_results`、`recall_min_score`；`/模块 回忆 开|关` 亦可
- 面板 API：`GET/POST /panel/api/recall`（状态 / 开关 / 天数 / 保留条数 / 立即重建索引）

### 17.20 群知识库 `kb.py`

- 目的：群里反复被问的事（群规、内测申请、报名入口）不必每次重打；命中直接回答。
  与 17.19 群聊回忆配合：回忆解决「翻旧账」，知识库解决「答新问」
- 存储：SQLite `kb_entries`（id / group_id / question / answer / keywords / hits /
  source / created_by / created_at / updated_at），**按群隔离**，只存管理员写入的问答，
  不存聊天原文
- 命中判定：复用 `src/textindex.py` 分词；命中问题原文按词项权重求和，命中人工关键词
  ×1.5（同义说法更可信）；覆盖率分母封顶 4（问句往往只含一两个核心词）
- 已知取舍：n-gram 在临界区间分不开「内测怎么搞」（该命中）与「今天会员日吗」
  （对「怎么加入会员群」不该命中），两者得分相同 → 默认阈值取 1.8（宁可命中），
  用三重兜底：`kb_require_question` 只答像提问的消息、回答里带上命中的问题
  （错了一眼可见）、面板可调 `kb_min_score` 且可见每条 hits
- 克制策略：同群回答冷却 `kb_cooldown`（默认 300 秒）；陈述句不抢答；
  `kb_auto_answer` 可整体关掉只留手动查询
- 问题发现 `suggest()`：从 17.19 的群聊索引里取最近 72 小时像提问的短消息，
  归一化（去客套前缀/标点）后聚类，出现次数达 `kb_suggest_min`（默认 2）且
  尚未能被命中才作为候选；面板一键采纳，避免手工录入没人愿意做
- 指令（超管）：`/知识 添加 问题 | 答案 [| 关键词]`、`/知识 列表`、`/知识 删除 <编号>`、
  `/知识 改 <编号> <答案>`、`/知识 测试 <一句话>`、`/知识 建议`、`/知识 开|关`
- AI 工具：`search_group_kb`（所有人）、`add_group_kb` / `suggest_group_kb`（超管）
- 面板：`GET/POST /panel/api/kb`（条目增删改、命中测试、开关、阈值、冷却、候选）；
  独立「知识库」页；`/模块 知识 开|关` 亦可

### 17.21 工作台账 `worklog.py`

- 动机：删广告、拦违规码、答问题都发生在群里却几乎不可见，导致「它好像啥也没干」，
  只剩签到有存在感。台账按周把做过的事摊开，用真实数字把价值显性化
- 数据来源（不新增隐私面）：
  - `punishments`（新增 `start`/`end` 日期区间参数）：按 `source` 归类为
    违规言论 / 违规图片 / 引流二维码 / 群内广告，并统计禁言人次
  - `action_stat`（新表，只计数不含内容）：`ai_reply` / `kb_answer` / `recall_query` /
    `link_preview` / `essence_set`；未知动作照原样列出，不吞数据
  - 打点位置：`ai.chat` 成功回复后、`kb.answer_for` 命中后、`recall.search` 真正翻到结果时、
    `linkpreview` 成功回复后、`essence.set_essence` 成功且知道群号时。
    全部 `try/except` 包住，计数失败绝不影响业务
- 输出：`/台账 [天数]`（1–90，默认 7）；群内按当前群统计，超管私聊为全部群合计；
  含环比（与上一个等长区间比较）；没有任何记录时**明确说「很安静」**，不编数字
- 每周播报：`worklog_weekly`（**默认关**，避免又多一条刷屏）、星期/时间/范围可配，
  按群 kv 去重，每群每天最多一次
- 面板：`GET/POST /panel/api/worklog`（开关、播报设置、近 7 天动作分布与分群条数、
  台账预览）；独立「工作台账」页；`/模块 台账 开|关`

### 17.22 主动发言与群知识库协作（`proactive.py` + `kb.py`）

- 问题：`kb`(priority 6)、`linkpreview`(6)、`ai`(11)、`proactive`(12) 全部 `block=False`，
  同一条提问有可能被「知识库」和「主动发言」各答一次，群里看起来像复读
- 避让：`kb.mark_answered(group_id, message_id)` 在回答后登记（TTL 180 秒）；
  `proactive` 处理入口先查 `kb.was_answered`，刚被答过的消息直接跳过
- 标准答案注入：`proactive` 在 help 判定阶段对最近 3 条人类消息调用 `kb.peek()`
  （只查**不计数**，避免把主动发言算成命中），命中就把「问题 + 答案」注入提示词，
  并明确要求「有人问就直接用这个答案，没人问就 skip」，防止模型另编一套
- 门槛调整：有标准答案时按「有人在问」处理，`min_messages = min_users = 1`，
  不必等群里攒够几条消息才肯回答

### 17.23 话题规约 `topics.py`

- 目的：机器人**自己**不参与的话题明确划出来，命中就说明原因、不展开。
  与 17.4/敏感词模块的分工必须分清：
  - `sensitive.py` 管**群友**：命中敏感词→AI 复核→确认违规才撤回/禁言，是**处罚**
  - `topics.py` 管**机器人自己**：碰到话题就不参与并告知，是**行为约束**；
    不撤回、不禁言、不处罚任何人
- 分类（词表与开关都在 kv / 面板可改）：
  - `proxy`：翻墙/代理/机场/节点一类。默认带保守词表，`~前缀` 为豁免短语，
    兜住「机场大巴」「梯子坏了」「游戏加速器」「k8s 节点」这类正常讨论
  - `politics`：涉政话题。**默认词表故意留空**——具体表述算不算敏感因群、因地区而异，
    硬编码容易误伤，程序只提供机制与话术，由管理员按自己的群规填
- 拦截点：`ai.chat()` **最前面**（quota 检查之前）→ 命中直接返回该分类的拒答话术，
  不调用模型。这样既不越界（不靠模型自觉），也不花 API 费用
- 兜底：`prompt_baseline()` 随每次对话注入系统提示词，覆盖规则拦不住的情况
  （隐晦说法、图片里的文字），并明确「不参与、不评价、不推测、不辩论、不输出相关关键词」
- 主动发言：`proactive` 命中即跳过，不评价、不接话
- 不受影响：关键词回复、群管指令、敏感词处罚流程
- 指令（超管）：`/规约`、`/规约 开|关`、`/规约 翻墙|涉政 开|关`、
  `/规约 词 <分类> <词组>`、`/规约 话术 <分类> <内容>`、`/规约 测试 <一句话>`
- 面板：`GET/POST /panel/api/topics`（分类开关、词表、话术、命中测试、底线预览）；
  独立「话题规约」页；`/模块 规约 开|关`

### 17.24 引用回复的上下文（`ai.py`）

- 问题：群友「引用某条消息 + @ 机器人 + 一句很短的话」（如引用入群验证提示后只发 `0000`），
  机器人答非所问。原因是引用原文只作为场景 system 提示注入，用户那一轮仍是光秃秃的 `0000`，
  模型把几个字当独立话题自由联想
- 改法：`quote_block()` 把引用原文拼进 **user 轮**，格式形如
  `【用户引用了 <谁> 的这条消息】<原文>【上面是引用内容，下面才是用户说的话…】`
  - 机器自己发的消息标注「（你自己）」，让模型明白这是对它上一条的回应
  - 无文字引用（图片/表情/卡片）单独说明，不硬猜
  - 超过 `QUOTE_MAX_CHARS`（300 字）截断，避免挤掉真正的问句
  - 纯 @ + 引用时提示「针对引用内容回应」
- 历史：引用随用户文本写入上下文（`compose_user_text()` 统一决定 text 与 history_text），
  多轮追问不丢；附图/转发说明仍由 `_attached_images_note()` 追加
- 场景：`_build_scene()` 的引用行只留元信息（谁发的、message_id、可用于撤回），不重复正文

### 17.25 入群验证的答案校验收紧（`joinverify.py`）

- 问题：旧实现 `check_answer` 是「文本里任意一个独立数字等于答案即通过」
  （`(?<!\d)(\d{1,3})(?!\d)` + `any(int(n) == answer)`），门槛形同虚设：
  答案 9 时回 `1+2+3+4+5+6+7+8+9`、`1e9`、`0x9`、`9.0`、`9的平方根` 全都能过
- 改法：改成**白名单**——先归一化（NFKC 全角转半角）并去掉口语填充
  （「答案是 / 等于 / 我算出来是 / 回答 / 哦 / 吧 …」）与标点，剩下的必须
  `isdigit()` 且等于答案；出现任何其它字符（运算符、字母、小数点、中文）一律不通过
- 附带：`looks_like_expression()` 识别算式糊弄，`wrong_answer_hint()` 在这种情况下
  回「不用算，直接回复 X-Y 的结果数字就行」，避免用户反复试错；
  算式消息仍由 `is_wrong_attempt()` 计入答错次数，不能无限重试
- 容忍的合法写法：`9`、`答案是 9`、`答案是 9 哦`、`我算出来是 9`、`回答：9`、
  `我的答案是9`、`９`（全角）、`9！`（语气）

### 17.26 入群验证的 AI 放宽通道（`joinverify.py`）

- 动机：算术题是防机器人的最低门槛，但真人也会答不上来——有人是手机端懒得算，
  有人直接说明来意（「我进来找人做个网站」）。一律按答错踢掉属于误杀
- 做法：`check_answer` 不通过、且消息「像人话」时，问一次 AI
  「这是不是真人说明了真实入群目的」，是则放行并**不计答错次数**
- 只在像人话时才问：`looks_like_natural_text()` 要求 ≥4 字符、含 ≥2 个汉字
  （或 ≥8 个英文字母）、且不是算式（`e^2`、`1+2+3` 直接按答错处理，不问 AI）
- 成本与防刷：每人每题最多问 2 次（`AI_PASS_MAX_TRIES`）；每群每日额度
  `joinverify_ai_daily_limit`（默认 30，0=关闭），按 `joinverify_ai_<群号>` 记 "日期:次数"
- **fail-closed**：AI 未配置/调用失败/返回不可解析/判定为 false → 一律不放行，
  走原有超时流程。宁可交管理员，也不能因为复核通道出问题而放机器人进来
- 配置：`joinverify_ai_pass`（默认开，`/入群验证 复核` 切换）、`joinverify_ai_daily_limit`
- 出题文案在开启时会补一句「算不出来就直接说说你进群想做什么，也行」，减少无谓失败

### 17.27 入群申请审批放宽到「说明来意」（`request.py`）

- 动机：验证问题（默认「GitHub是干什么的？」）只考知识点，答不上来但确实是真人的
  （如「我进来找人做个网站」）会被判「答非所问 → 拒绝」而误挡
- `_ai_verdict` 的判定顺序改为：
  1. 答对/基本符合验证问题 → `approve` + `kind=answer`
  2. **没答对但说明了真实入群目的**（找人做网站/开发、请教技术、求职招人、找合作、
     咨询产品或报价、交流学习、找资料）→ `approve` + `kind=intent`，
     并在提示词里明确「没答对题不是拒绝的理由」
  3. 广告引流/代充刷单/加微信领福利/群发模板/只丢数字或乱码 → `reject` + `kind=spam`
  4. 既没答对也看不出意图 → `manual` + `kind=unclear`（转人工）
  拿不准仍选 manual，不为了通过而硬放行
- 规则兜底同步放宽：新增 `join_intent_keywords`（默认含「找人做/做个网站/开发/请教/合作/
  求职/咨询/报价…」），`_rule_verdict` 返回 `(decision, kind)`，AI 不可用时也认来意关键词
- 通知与日志带上判定依据（答对验证题 / 说明了来意 / 广告模板 / 看不出意图），便于审计
- 面板「加群审批」页新增「来意关键词」输入框；API `GET/POST /panel/api/join` 增加
  `intent_keywords` 字段（限长 1000）
