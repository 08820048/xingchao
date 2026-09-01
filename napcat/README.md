# NapCat 配置步骤（扫码登录 + WebSocket 客户端）

NapCat 的 OneBot 网络配置文件名包含 QQ 号（`onebot11_<QQ号>.json`），
**必须等小号登录成功后才能生成**，因此无法预先写入仓库，需按下面步骤在 WebUI 手动配置一次。
配置会持久化到 `napcat/config/` 挂载目录，之后重启容器无需重复操作。

## 1. 启动

```bash
cp .env.example .env
# 编辑 .env：填超管 QQ 号、群白名单、一个至少 16 位的随机 ONEBOT_ACCESS_TOKEN
docker compose up -d
```

## 2. 获取 WebUI token 并扫码登录

```bash
docker logs xingchao-napcat
```

在日志里找 WebUI token（形如 `[WebUi] WebUi Local Panel Url: http://0.0.0.0:6099/webui?token=xxxx`）。

WebUI 只映射在 `127.0.0.1:6099`。服务器上请用 SSH 隧道：

```bash
ssh -L 6099:127.0.0.1:6099 user@your-server
```

然后本机浏览器打开 `http://127.0.0.1:6099/webui`，输入日志里的 token 进入。

在「QQ 登录」页选择二维码登录，用**手机 QQ 扫码**登录小号。
（注意：小号不能和电脑 QQ 同时在线；登录后快速刷新验证可能触发安全验证，按提示处理。）

## 3. 添加 WebSocket 客户端（反向 WS）

登录成功后，进入「网络配置」→「新建」→ **WebSocket 客户端**，填写：

| 项 | 值 |
|----|----|
| URL | `ws://xingchao-bot:8080/onebot/v11/ws` |
| Token | 与根目录 `.env` 中 `ONEBOT_ACCESS_TOKEN` 完全一致 |
| 消息格式 | Array |
| 启用 | 是 |

保存并启用。回到 bot 容器日志确认连接：

```bash
docker logs -f xingchao-bot
# 应出现 "WebSocket Connection from ... " / OneBot V11 连接成功类日志
```

## 4. 验证

把小号拉进白名单群，群内发送 `/ping`，机器人应回复 `pong`。

## 数据目录

- QQ 登录会话：`napcat/qq/`（已 gitignore，勿提交、勿泄露）
- NapCat 配置（含 OneBot 网络配置）：`napcat/config/`
