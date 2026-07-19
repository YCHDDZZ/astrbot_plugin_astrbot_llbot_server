<div align="center">

# MC与BOT服务监控插件

**集成 BOT服务器（astrbot + QQ）和 Minecraft 游戏服务器状态监控的 AstrBot 插件**

<img src="https://img.shields.io/badge/version-v1.0.0-76bad9" alt="version">
<img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="python">
<img src="https://img.shields.io/badge/astrbot->=4.16-76bad9" alt="astrbot">

<br>

<a href="https://github.com/YCHDDZZ/astrbot_plugin_astrbot_llbot_server">GitHub</a> ｜
<a href="https://github.com/YCHDDZZ/astrbot_plugin_astrbot_llbot_server/issues">Issue Tracker</a>

</div>

## ✨ 功能特点

- 🤖 **BOT 服务器监控** — 定时监控多台服务器上的 astrbot 和 QQ（LLBOT）存活状态
- 🎮 **MC 服务器查询** — 通过 MineBBS API 实时查询 Minecraft 服务器状态（在线玩家/版本/MOTD/延迟）
- 🔔 **离线/恢复通知** — 服务离线或恢复时自动推送 QQ 群消息
- 🧠 **自然语言主动查询** — 可选小模型意图识别，用户可直接说"查BOT状态""查MC服务器"触发查询
- 🛡️ **恢复确认机制** — 可配置恢复确认次数，避免误报
- 📁 **双类型服务器管理** — WebUI 中分别配置 BOT 服务器和 MC 服务器列表

## 📦 安装

1. 将插件目录放入 AstrBot 的 `data/plugins/` 目录
2. 重启 AstrBot 或在插件管理页面重载插件
3. 在 WebUI 配置面板中添加需要监控的服务器

## ⚙️ 配置说明

### BOT 服务器配置

在 WebUI 的 `servers` 模板中添加 BOT 服务器：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | string | "" | 自定义名称 |
| `astrbot_url` | string | "" | astrbot 地址 |
| `astrbot_api_key` | string | "" | astrbot API Key |
| `enable_astrbot` | bool | true | 启用 astrbot 检查 |
| `llbot_url` | string | "" | QQ(LLBOT) 地址 |
| `llbot_api_token` | string | "" | QQ API Token |
| `enable_llbot` | bool | true | 启用 QQ 检查 |
| `llbot_health_path` | string | "/get_login_info" | QQ健康检查端点 |

### MC 服务器配置

在 WebUI 的 `mc_servers` 模板中添加 Minecraft 服务器：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | string | "" | 服务器名称 |
| `mc_ip` | string | "" | 服务器 IP/域名 |
| `mc_port` | int | 25565 | 服务器端口 |
| `mc_server_type` | string | "java" | java 或 bedrock |
| `enable_mc` | bool | true | 启用 MC 检查 |

### 主动查询配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `active_query_enabled` | bool | false | 启用自然语言主动查询 |
| `active_query_provider` | string | "" | 意图识别模型（不填则使用主对话模型） |
| `active_query_cooldown` | int | 30 | 意图识别冷却秒数 |
| `active_query_prompt` | text | *内置* | 自定义意图识别提示词 |

### 通用配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `check_interval` | int | 60 | 健康检查间隔（秒） |
| `http_timeout` | int | 10 | HTTP 请求超时（秒） |
| `recovery_confirm_count` | int | 1 | 恢复确认次数 |
| `notification_cooldown` | int | 300 | 通知冷却时间（秒） |
| `enable_notification` | bool | true | 启用通知推送 |
| `notification_groups` | text | "" | 通知群聊 ID |
| `enable_at_users` | bool | false | 启用 @ 指定用户 |
| `at_users` | text | "" | 需要 @ 的用户 |

## 🤖 命令列表

### 查询命令（所有人可用）

| 命令 | 说明 |
|------|------|
| `/server_query [bot\|mc]` | 查询服务器状态（无参数则询问类型） |
| `/bot_status` / `/bm_check [名称]` | 实时查询 BOT 服务器状态 |
| `/mc_status [名称]` | 实时查询 MC 服务器状态 |
| `/bm_list` | 查看 BOT 服务器缓存状态 |
| `/bm` | 显示帮助信息 |

### 管理命令（需要管理员权限）

| 命令 | 说明 |
|------|------|
| `/bm_add [名称] [astrbot_url] [astrbot_api_key] [llbot_url] [llbot_api_token]` | 添加 BOT 服务器 |
| `/bm_remove [名称]` | 删除 BOT 服务器 |
| `/bm_edit [名称] [字段] [值]` | 编辑 BOT 服务器字段 |
| `/bm_interval [秒数]` | 设置检查间隔 |
| `/bm_notif [on\|off\|add\|del] [参数]` | 管理通知 |
| `/bm_at [on\|off\|add\|del] [参数]` | 管理 @用户 |

## 🔍 输出格式示例

### BOT 服务器

```
━━━━ 千早爱音 ━━━━
  astrbot  🟢 在线
         ─  HTTP 200  ·  21ms
  QQ       🟢 在线
         ─  LLBOT在线，通信正常，但无法验证QQ是否已登录  ·  1ms
  ⏱ 2026-07-20 00:51:59
```

### MC 服务器

```
===== 1. 爱音的MC一服 =====
🟢 服务器: 爱音的MC一服
🌐 地址: xm.rainplay.cn:56824
🔧 类型: Java版
🎮 版本: 1.20.1
👥 在线玩家: 2/20
📋 玩家列表: PlayerA, PlayerB
🕒 更新时间: 2026-07-19 16:29:14
⏱ 延迟: 34ms
```

## 🧠 自然语言主动查询

开启 `active_query_enabled` 并选择意图识别模型后，用户可以直接说：

- "查一下 BOT 服务器状态"
- "BOT 还活着吗"
- "查看 MC 服务器"
- "查一下我的世界的服务器"
- "服务器状态怎么样" → 反问"BOT 还是 MC？"

插件会实时查询并返回状态，绕过主角色扮演模型。

### 意图分类

| 标签 | 触发条件 |
|------|----------|
| `BOT_QUERY` | 明确提到 BOT/机器人/astrbot/QQ 等关键词 |
| `MC_QUERY` | 明确提到 MC/Minecraft/我的世界等关键词 |
| `ASK_CLARIFY` | 提到"服务器"但未指明类型 |
| `OTHER` | 无关消息，放行 |

## 🔔 自动通知

当检测到服务离线或恢复时，自动向配置的 QQ 群发送通知消息。

**通知格式：**
```
[服务监控]
服务器：千早爱音
状态：astrbot 掉线
```

## 🛡️ 恢复确认机制

`recovery_confirm_count` 控制服务恢复前连续检查正常的次数：

- 设为 **1**（默认）→ 1 次正常即视为恢复
- 设为 **3** → 连续 3 次检查正常才视为恢复（适合避免 LLBOT 重启后 QQ 未登录误报）

## ⚠️ 注意事项

1. BOT 服务器检查：astrbot 检查根 URL（HTTP 状态），QQ 优先检查 `/get_login_info`（验证 QQ 登录），兜底检查根 URL
2. MC 服务器通过 MineBBS API 查询，需要服务器能访问 `motd.minebbs.com`
3. 主动查询若未配置 `active_query_provider`，会自动使用主对话模型
4. 通知使用 QQ 官方 bot 的 `group_openid`，不是 QQ 群号
5. QQ 健康检查端点在 Milky 协议下为 `/api/get_login_info`，OneBot11 下为 `/get_login_info`

## 📄 更新日志

### v1.0.0

- 初始发布
- 支持 BOT 服务器（astrbot + QQ）定时监控与通知
- 支持 MC 游戏服务器实时查询（MineBBS API）
- 支持自然语言主动查询（可选小模型意图识别）
- 支持恢复确认机制
- 支持 QQ 群通知

## ❤️ 作者

[YCHDDZZ](https://github.com/YCHDDZZ)

## ⭐ Star History

> 如果这个项目对你有帮助，请给项目一个 Star ❤️

## 📄 许可证

[MIT](LICENSE)
