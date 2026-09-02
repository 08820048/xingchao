# 星潮 Xingchao 部署指南（新服务器 / 迁移）

> 适用：在一台全新的 Linux 服务器（推荐 Ubuntu 22.04+，≥2GB 内存）上从零部署，
> 或从旧服务器完整迁移。
> 部署完成后可对照文末「验收清单」逐项确认。

---

## 0. 前置条件

| 项 | 要求 |
|---|---|
| 服务器 | Linux x86_64，1GB 内存可用（**强烈建议 2GB+ 并配置 swap**，QQ 客户端较重） |
| 系统 | Ubuntu 20.04+ / Debian 11+（systemd） |
| 网络 | 可访问 GitHub、Docker Hub、腾讯 QQ 服务器 |
| 账号 | 一个小号 QQ（勿与大号同设备登录）；接受非官方协议封号风险 |
| 域名（可选） | 如需公网访问官网/面板：域名托管到 Cloudflare |
| AI（可选） | OpenAI 兼容 API（如智谱 GLM：`https://open.bigmodel.cn/api/paas/v4`）；服务器需能直连服务商 API |

> 🌏 **国内/网络受限服务器**：若 GitHub、Docker Hub 访问不畅，见 1.1/1.3 的镜像加速与 SSH 方案，
> 以及文末「常见问题排查」。

---

## 1. 路径 A：全新安装（没有旧服务器数据）

### 1.1 安装 Docker

```bash
apt-get update && apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker
docker --version && docker compose version
```

**国内服务器拉不动镜像？** Docker Hub 可能被阻断，配置镜像加速后重试：

```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run",
    "https://docker.1panel.live",
    "https://dockerproxy.net"
  ]
}
EOF
systemctl restart docker
```

> 加速源时效性强，若全部失效可搜索“docker registry mirror 可用”换新源。

### 1.2 小内存服务器加固（<2GB 内存强烈建议）

```bash
fallocate -l 2G /swapfile2 && chmod 600 /swapfile2
mkswap /swapfile2 && swapon /swapfile2
echo '/swapfile2 none swap sw 0 0' >> /etc/fstab
```

### 1.3 获取代码

```bash
# 方式一：git clone（需 GitHub 访问权限）
apt-get install -y git
git clone https://github.com/08820048/xingchao.git /root/xingchao

# 方式二：从旧服务器整体拷贝（见第 2 节迁移）
```

**GitHub 访问受限（HTTPS 超时/404）？** 仓库私有或 HTTPS 被阻断时，走 SSH 协议：

1. 服务器上生成密钥：`ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519`，复制 `~/.ssh/id_ed25519.pub`
2. 仓库页 → Settings → Deploy keys → 添加公钥，**勾选 Allow write access**（后续要 push）
3. 克隆并让 git 全局走 SSH：

```bash
git clone git@github.com:08820048/xingchao.git /root/xingchao
git config --global url."git@github.com:".insteadOf "https://github.com/"
```

> 若 22 端口也被阻断，可改走 443：`ssh://git@ssh.github.com:443/08820048/xingchao.git`。

### 1.4 配置环境变量

```bash
cd /root/xingchao
cp .env.example .env
```

编辑 `.env`，至少填写：

```ini
# 超管 QQ（多个逗号分隔）
XINGCHAO_SUPERUSERS=你的QQ
# 群白名单（逗号分隔；留空 = 不处理任何群）
XINGCHAO_GROUP_WHITELIST=群号1,群号2
# OneBot 反向 WS 令牌（生产必须 ≥16 位随机串）
ONEBOT_ACCESS_TOKEN=$(openssl rand -hex 16 生成后粘贴)
# Web 管理面板密码
XINGCHAO_PANEL_PASSWORD=自定义强密码
# AI 问答（可选，不填则 AI 功能自动禁用；示例为智谱 GLM，任何 OpenAI 兼容服务均可）
XINGCHAO_AI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
XINGCHAO_AI_API_KEY=sk-xxx
```

> 💡 AI 密钥也可以部署后直接在管理面板「AI」页填写保存（无需改 .env 重启）；
> 模型名可在面板「获取模型列表」按钮拉取后下拉选择。

### 1.5 构建并启动

```bash
docker compose up -d --build
docker compose ps        # 两个容器均应为 Up
docker logs xingchao-bot # 应看到「星潮 Xingchao bot 启动完成」
```

**国内构建 pip 下载慢/超时？** 可指定国内 PyPI 镜像重建：

```bash
docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
docker compose up -d
```

### 1.6 NapCat 扫码登录

1. 确认 SSH 隧道（见 1.7）后，浏览器打开 `http://127.0.0.1:6099/webui?token=<token>`
   （token 见 `napcat/config/webui.json` 或 `docker logs xingchao-napcat`）
2. 手机 QQ 扫码登录**小号**
3. 「网络配置」→ 新建 **WebSocket 客户端**：
   - URL：`ws://xingchao-bot:8080/onebot/v11/ws`（⚠️ 首尾不能有空格，否则连接 403/失败）
   - Token：与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 完全一致
   - 消息格式：Array
4. 登录成功后建议在 WebUI「设置」中把小号设为**自动登录/快速登录**，
   否则容器重启（如服务器重启）后需要重新扫码。

> 💡 也可以不走 WebUI：`http://127.0.0.1:8081/panel/qq-login-qr` 直接查看最新登录二维码
>（该地址始终显示最新二维码，无需登录 WebUI）。

### 1.7 SSH 隧道（在自己电脑上执行）

```bash
ssh -N -L 6099:127.0.0.1:6099 -L 8081:127.0.0.1:8081 -i <私钥> root@服务器IP
```

### 1.8 验证

群内发 `/ping` → 应回 `pong`；详见文末验收清单。

---

## 2. 路径 B：从旧服务器迁移（推荐，几分钟恢复全部状态）

机器人所有状态都在项目目录内，整体拷贝即可零配置迁移：

### 2.1 旧服务器打包

```bash
cd /root
tar czf xingchao-migrate.tar.gz \
  --exclude='xingchao/web/node_modules' \
  --exclude='xingchao/.git' \
  xingchao
# 拷贝到新服务器（示例）
scp xingchao-migrate.tar.gz root@新服务器IP:/root/
```

包含的关键状态：
| 目录/文件 | 内容 |
|---|---|
| `napcat/qq/` | QQ 登录会话（**迁移后免扫码**，泄露=交出账号） |
| `napcat/config/` | WS 客户端配置、WebUI token、自动登录设置 |
| `data/xingchao.db` | 全部 kv 配置（面板设置/开关/运行时白名单/超管）、统计、定时任务 |
| `data/replies.json` | 关键词词库 |
| `data/logs/` | 历史聊天日志（可选，体积大可排除） |
| `.env` | 生产密钥配置 |

### 2.2 新服务器恢复

```bash
# 安装 Docker（见 1.1）与 swap（见 1.2）
tar xzf /root/xingchao-migrate.tar.gz -C /root/
cd /root/xingchao
docker compose up -d --build
```

小号会**自动快速登录**（webui.json 已配置 autoLoginAccount），无需重新扫码。
若会话失效再走 1.6 扫码。

---

## 3. Cloudflare 公网部署（可选：官网 + 管理面板上公网）

前提：域名 DNS 托管在 Cloudflare。

### 3.1 授权与 Tunnel 创建

```bash
npm install -g wrangler
wrangler login --browser=false   # 浏览器授权（需要 SSH 隧道 -L 8976:127.0.0.1:8976）
wrangler whoami                  # 确认登录
```

```bash
# 通过 Cloudflare API 创建 Tunnel（account_id 在 wrangler whoami 输出中）
TOKEN=<wrangler 的 OAuth token，见 ~/.config/.wrangler/config/default.toml>
AID=<你的 Account ID>
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$AID/cfd_tunnel" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"xingchao-bot","config_src":"cloudflare"}'
# 记录返回的 tunnel id，并 GET /cfd_tunnel/<id>/token 获取 connector token
```

### 3.2 安装 cloudflared 连接器

```bash
curl -Lo /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg -i /tmp/cloudflared.deb
cloudflared service install <connector-token>
systemctl enable --now cloudflared
```

### 3.3 配置流量入口（ingress）

```bash
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$AID/cfd_tunnel/<tunnel_id>/configurations" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"config":{"ingress":[
        {"hostname":"xingchao.dev","service":"http://localhost:8080"},
        {"hostname":"panel.xingchao.dev","service":"http://localhost:8080"},
        {"service":"http_status:404"}]}}'
```

### 3.4 DNS 记录（wrangler OAuth 无 DNS 写权限，需手动添加）

Cloudflare Dashboard → DNS → 添加两条 CNAME（均开启橙色云代理）：

| Type | Name | Target |
|---|---|---|
| CNAME | `@` | `<tunnel_id>.cfargotunnel.com` |
| CNAME | `panel` | `<tunnel_id>.cfargotunnel.com` |

> 另有纯静态镜像方案：`wrangler pages deploy web/dist --project-name=xingchao`
> 部署到 `<project>.pages.dev`（仅官网展示，面板 API 不在 Pages 上）。

### 3.5 安全建议

- 管理面板公网暴露后依赖**面板密码 + Cookie 鉴权**；建议再加 Cloudflare Access（Zero Trust）套在 `panel.xingchao.dev` 上
- NapCat WebUI（6099）与 bot（8080）**永远不要**直接映射公网

---

## 4. 运维速查

- 日常启停、日志、备份、掉线处理：见 [docs/OPERATIONS.md](OPERATIONS.md)
- 指令与功能总览：README 与 OPERATIONS.md 第 6 节
- 本地联调（无需真实 QQ）：`scripts/mock_napcat.py`

---

## 6. 常见问题排查（实战踩坑记录）

| 现象 | 原因与处理 |
|---|---|
| 拉镜像 `dial tcp ... i/o timeout` | Docker Hub 被阻断，见 1.1 配置镜像加速 |
| `git clone` 提示输用户名/404 | 仓库私有或 HTTPS 被阻断，见 1.3 换 SSH + Deploy Key |
| 构建时 pip 下载极慢/超时 | 见 1.5 用 `--build-arg PIP_INDEX_URL` 指定国内源 |
| NapCat 日志 `Login Error ErrCode: 3` | 二维码过期（约 2 分钟刷新）。**刷新二维码页面后立即扫码**并手机确认 |
| NapCat 重启后要求重新扫码 | 未设快速登录；按 1.6 第 4 步设置 `autoLoginAccount` |
| NapCat 反向 WS 报 `403 Forbidden` | ① Token 与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 不一致；② URL 有首尾空格。核对后保存 |
| bot 日志出现 `AI 问答未启用` | 面板「AI」页填 Base URL + Key，或改 `.env` 后重启；模型用面板「获取模型列表」选择 |
| AI 调用报 `APITimeoutError` | 服务器连不上 AI 服务商（海外中转常被墙），换可直连的服务商如智谱 GLM |
| 面板打不开 | 确认只映射在 127.0.0.1（安全设计），需走 SSH 隧道访问（见 1.7） |
| 群里发指令无反应 | ① 群号不在 `XINGCHAO_GROUP_WHITELIST`；② Bot 未连接（看日志有无 `Bot xxx connected`） |

---

## 7. 验收清单（部署完成后逐项打勾）

- [ ] `docker compose ps` 两个容器均 Up
- [ ] bot 日志出现「星潮 Xingchao bot 启动完成」与 `Bot xxx connected`
- [ ] 小号 QQ 在线（NapCat WebUI 或日志确认）
- [ ] 群内 `/ping` → `pong`；`/help` 显示菜单（管理员/成员视图正确）
- [ ] `@机器人 现在几点了` → 北京时间正确回答
- [ ] 面板可登录（`http://127.0.0.1:8081/panel`），各页数据正常
- [ ] 面板「加群审批」「敏感词」「定时任务」「AI」配置符合预期
- [ ] AI 就绪：bot 日志出现「AI 问答就绪」（或确认 AI 功能本就未启用）
- [ ] （如部署公网）`https://xingchao.dev` 与 `panel.xingchao.dev` 均可 HTTPS 访问
- [ ] 定时任务触发一次并确认送达
- [ ] 已做首次备份：`tar czf backup.tar.gz -C /root/xingchao data napcat/config`
