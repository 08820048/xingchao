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

## 功能（第一期）

- 仅白名单群进入业务；空白名单 = 不处理任何群（安全默认）
- 白名单群全量文本日志（`data/logs/group-{群号}-日期.jsonl`）
- 指令：`/help` `/ping` `/id` `/status`（status 仅超管）
- 关键词回复（精确 / 包含），同群同词条默认 8 秒冷却
- 超管指令：`/reply reload|list`、`/group list`、`/plugin reply on|off`

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
├── napcat/README.md        # 扫码与 WS 客户端配置步骤
├── bot/                    # NoneBot2 应用（python:3.12-slim 自建镜像）
│   ├── bot.py              # 仅 init / 注册适配器 / 加载插件
│   ├── .env / .env.prod    # dev / prod 配置
│   └── src/
│       ├── config.py       # pydantic 配置
│       ├── permission.py   # 白名单 / 超管规则
│       ├── store.py        # SQLite (kv, msg_stat)
│       └── plugins/
│           ├── whitelist.py  # 非白名单群忽略日志
│           ├── logger.py     # 白名单群 jsonl 日志
│           ├── basic.py      # /help /ping /id /status
│           ├── reply.py      # 关键词回复（冷却）
│           └── admin.py      # 超管指令
└── data/                   # 运行数据（compose 挂载，不入库）
```

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

## License

[MIT](LICENSE)
