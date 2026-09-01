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

非官方协议**掉线是常态**。症状：群里 @ 机器人无反应、`/ping` 无响应、面板正常但 bot 日志无消息流入。

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
5. 验证：`docker logs -f xingchao-bot` 出现 `Bot xxx connected`，群内发 `/ping` 回 `pong`

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
| `/ping` | 连通测试 |
| `/id` | 查看群号 / 用户 / 机器人 ID |
| `/stats [yesterday]` | 群活跃统计（默认当天） |
| `@星潮 <内容>` 或 `星潮<内容>` | @响应（同群 8 秒冷却）；内容含「帮助/help」回复帮助菜单 |

### 仅超管（群聊或私聊）

| 指令 | 说明 |
|---|---|
| `/status` | 运行状态 |
| `/mute @某人 [分钟]` | 禁言（默认 10 分钟，上限 30 天；**需机器人是群管理员**） |
| `/unmute @某人` | 解除禁言 |
| `/banall on\|off` | 全体禁言 |
| `/kick @某人` | 移出群聊 |
| `/recall` | 撤回消息（回复目标消息发送，或 `/recall <message_id>`） |
| `/reply reload\|list` | 词库热重载 / 列表 |
| `/group list\|add\|del <群号>` | 白名单管理（add/del 即时生效，重启保留） |
| `/plugin reply on\|off` | 关键词模块开关 |
| `/welcome on\|off` | 进群欢迎开关 |
| `/welcome view` | 查看当前欢迎语 |
| `/welcome set <欢迎语>` | 自定义欢迎语（占位符：`{at}`=@新人、`{qq}`=新人QQ、`{group}`=群号；也可在面板「仪表盘」页编辑） |

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

## 9. 相关文档

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
