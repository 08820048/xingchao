# 第三方插件使用文档

> 已安装的 NoneBot 商店插件清单、指令用法与注意事项。
> 修改插件配置后需 `docker compose up -d --build xingchao-bot` 重建生效。

## 一、安装原则（重要）

1. **内存红线**：本服务器仅 1.4G 内存且常态吃 swap。**任何需要 chromium /
   playwright 等大依赖的插件一律先停下汇报，由管理员决定装不装**——
   当前已因此放弃 heweather（天气图片）与 dialectlist（聊天榜图片）。
2. **依赖冲突**：依赖钉子过老（如 pydantic<2、httpx<0.24）导致与现有插件
   无法共存的，跳过或用 `--no-deps` 单独安装（见 bot/Dockerfile 注释）。
3. **指令冲突**：安装前检查插件指令是否与现有指令撞车。

## 二、已安装插件

### 1. nonebot-plugin-status — 服务器状态
| | |
|---|---|
| 指令 | `/状态`；私聊**戳一戳**机器人头像 |
| 权限 | 仅超管 |
| 输出 | CPU / 内存 / Swap / 磁盘占用、NoneBot 运行时长 |

### 2. nonebot-plugin-blacklist — 黑名单
| 指令 | 说明 |
|---|---|
| `/拉黑用户 <QQ>` / `/屏蔽用户` | 禁止该用户使用机器人（全功能拦截） |
| `/拉黑群 <群号>` / `/屏蔽群` | 禁止该群使用机器人 |
| `/拉黑私聊 <QQ>` | 禁止私聊 |
| `/解禁用户` / `/解封用户`、`/解禁群`、`/解禁私聊` | 解除 |
| `/查看用户黑名单` 等同构指令 | 查看列表 |

仅超管。通过 `event_preprocessor` 在**最前端**拦截黑名单目标，优先级高于一切插件。
黑名单持久化由插件自行管理（json 文件）。

### 3. nonebot-plugin-cloudsignx — 已移除
本项目的本地按群积分（`/签到`、`/积分`、`/商店`、`/轮盘`）已经覆盖签到、积分、排行、
消费与游戏闭环，因此移除了第三方云签到插件，避免两套积分并存。
原云端积分与挖矿/钓鱼等玩法不再可用；历史云端数据无法迁移到本地。

### 4. nonebot-plugin-crazy-thursday — 疯狂星期四
| 指令 | 说明 |
|---|---|
| 发送 `疯狂星期X`（如 疯狂星期四） | 返回对应的 KFC 疯四文案 |
| 发送 `狂乱X曜日` | 日语版触发 |

无需前缀、无需 @，所有人可用。

### 5. nonebot-plugin-githubcard — GitHub 链接卡片
| 触发方式 | 说明 |
|---|---|
| 消息中出现任意 `https://github.com/...` 链接 | **自动**发送仓库卡片（star 数、简介等） |
| `/github <链接>` | 手动触发 |

所有人可用，白名单群外也会响应链接。

### 6. nonebot-plugin-groupmate-waifu — 娶群友
| 指令 | 说明 |
|---|---|
| `/娶群友 @某人` | 随机娶（每日重置，可配置） |
| `/离婚` / `/分手` | 解除 CP |
| `/本群CP` | 查看本群 CP 列表 |
| `/查看娶群友卡池` | 查看卡池 |
| `/透群友 @某人` | 玩梗功能 |
| `/色色记录`、`/涩涩记录` | 记录查询 |
| `/重置娶群友记录`、`/设置娶群友保护 @某人` 等 | 群管理/超管 |

卡片图片用 PIL 渲染，容器内已装文泉驿微米黑字体（GROUPMATE_WAIFU_FONTNAME）。

### 7. nonebot-plugin-handle — 猜成语
| 指令 | 说明 |
|---|---|
| `/handle` 或 `/猜成语` | 开始游戏，根据拼音提示猜成语 |
| `/提示` / `/猜成语提示` | 游戏中获取提示 |

所有人可用，每群同时只进行一局。

### 8. nonebot-plugin-miao — 口癖（被动）
**无指令**。机器人所有**单段纯文本**回复有概率在末尾加「喵」。
当前配置（.env.prod，可改）：
```
MIAO_WORDS=["喵"]     # 口癖词
MIAO_PROBABILITY=0.15 # 概率（默认 0.5 太高已调低）
MIAO_POSITION=end     # 加在末尾
MIAO_COUNT=1
```
注意：AI 回复是「@+文本」多段消息，不会被加口癖；管理指令的纯文本回复会。

### 已移除：nonebot-plugin-revolver（俄罗斯轮盘）
原版把子弹/轮次放在进程级全局变量里，多个群会串台；大厅和回合没有超时，容易永久卡住；
「空白名单时是否禁言」还要靠 Dockerfile 对上游源码打 `sed` 补丁，升级即失效。
现已改为本地插件 `bot/src/plugins/roulette.py`：按群隔离状态、白名单群生效、超时自动推进，
中枪不再禁言，改为按本群积分零和结算（详见 README 与 `/轮盘 规则`）。

## 三、未安装清单及原因

| 插件 | 原因 |
|---|---|
| nonebot-plugin-txt2img | 硬冲突：钉死 `pydantic<2` + `localstore<0.7`，与 heweather/dialectlist 等现代插件依赖无法共存 |
| nonebot-plugin-heweather | 需 chromium 渲染天气卡片，内存峰值高（1.4G 服务器风险）→ 放弃 |
| nonebot-plugin-dialectlist | 需 chromium 渲染榜单图片 → 功能与内置 `/stats` 高度重叠，放弃 |

## 四、新增第三方插件流程

1. `pip download <plugin> --no-deps` 检查 Requires-Dist：
   - 有 playwright/chromium 等大依赖 → **停下汇报，等管理员决定**
   - 依赖钉子与现有冲突 → 评估 `--no-deps` 可行性
2. 正常依赖加入 `bot/pyproject.toml`；`--no-deps` 的加进 `bot/Dockerfile`
3. 在 `bot/bot.py` 的 `THIRD_PARTY_PLUGINS` 元组中登记
4. 需要配置的写进 `bot/.env.prod`（生产值可由根 `.env` 覆盖）
5. `docker compose build && up -d`，看日志确认 `Succeeded to load plugin`
6. 更新本文档

## 五、Dockerfile 特殊处理说明

`bot/Dockerfile` 中有三个插件用 `--no-deps` 安装：

| 插件 | 跳过原因 | 手动保证的运行时依赖 |
|---|---|---|
| nonebot_plugin_crazy_thursday | 钉死 httpx<0.24 | httpx≥0.27（主安装已有） |
| nonebot_plugin_handle | 钉死 Pillow<11 | Pillow≥11.3（主安装已有）、alconna、uninfo、pypinyin（pyproject 已加） |
| pokepoke_miss | 声明 pil_utils==0.1.10（钉死 Pillow<11），但源码实际未 import pil_utils，属过时声明 | 仅 nonebot2 / adapter（主安装已有） |

升级这些插件时注意检查其代码是否用到了被跳过的新依赖。
