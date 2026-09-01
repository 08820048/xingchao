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
| admin 指令 | 1 | True |  
| basic 指令 | 5 | True |  
| reply | 20 | True（命中才 block） |  
| logger | 100 | False |  
  
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