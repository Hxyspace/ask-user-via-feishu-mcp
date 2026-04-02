# ask-user-via-feishu

[![GitHub Release](https://img.shields.io/github/v/release/Hxyspace/ask-user-via-feishu-mcp)](https://github.com/Hxyspace/ask-user-via-feishu-mcp/releases)
![Python](https://img.shields.io/badge/python-3.10%2B-green.svg)
![Protocol](https://img.shields.io/badge/protocol-MCP-purple.svg)
![Platform](https://img.shields.io/badge/platform-Feishu%20%7C%20Lark-00B96B.svg)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](./LICENSE)
[![Build](https://github.com/Hxyspace/ask-user-via-feishu-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/Hxyspace/ask-user-via-feishu-mcp/actions/workflows/build.yml)

将 LLM 的消息发送和人工确认能力接入飞书——一个面向单一 owner 的 [MCP](https://modelcontextprotocol.io/) Server。

```
┌──────────┐  stdio   ┌───────────────┐  shared  ┌───────────────────┐  Feishu API  ┌──────┐
│ MCP Host │ ───────> │  MCP Server   │ ───────> │   Shared Daemon   │ ───────────> │Feishu│
│  (LLM)   │ <─────── │  (ask/send)   │ <─────── │  (longconn+event) │ <─────────── │ User │
└──────────┘          └───────────────┘   IPC    └───────────────────┘              └──────┘
```

## 🎯 适用场景

- **远程人工确认**：人不在电脑旁时，LLM 通过飞书向你发起确认，你在手机上回复即可继续。
- **执行结果推送**：LLM 把执行进度、告警或中间产物推送到飞书会话。
- **文件资源交换**：通过飞书上传文件或图片，LLM 自动下载到本地继续处理。
- **多会话路由**：将消息路由到指定群聊，或在首次使用时通过飞书卡片选择目标会话。
- **节省 LLM 额度**：按次计费的 LLM（如 GitHub Copilot）中，通过飞书通道与 LLM 持续对话，交互不额外消耗对话次数。配合 [copilot-instructions.md](.github/copilot-instructions.md) 将飞书设为默认交流通道，可显著降低来回切换的开销。

## 🚀 主要特性

| 特性 | 说明 |
|---|---|
| **Owner-only** | 只接受配置的 owner 的消息和操作，不响应其他用户 |
| **5 个 MCP 工具** | 发送文本 / 图片 / 文件 / 富文本、提问并等待回复 |
| **共享 Daemon** | 后台单例 daemon 维护飞书长连接，多个 MCP 进程共享，空闲自动退出、按需自动拉起 |
| **卡片交互** | 提问时可附带按钮选项，用户点选或直接文本回复均可 |
| **会话选择** | 支持 `CHAT_ID` 直接路由到群聊，或首次调用时弹出卡片让 owner 选择 / 新建群聊 |
| **Per-chat FIFO 队列** | 同一会话内多个提问自动排队；不同会话的提问可并行 |
| **资源下载** | 用户回复的图片/文件自动流式下载到本地，路径随结果返回 |
| **超时自动召回** | 可配置超时提醒、默认答案、`[AUTO_RECALL]` 让 LLM 自动重新提问 |
| **Reaction 标记** | 收到回复后自动添加 emoji reaction，标记处理状态 |

## 📦 安装

### 方式一：下载 `.whl` 安装（推荐）

从 [GitHub Releases](https://github.com/Hxyspace/ask-user-via-feishu-mcp/releases) 下载最新的 `.whl` 文件：

```bash
python -m pip install ask_user_via_feishu-<version>-py3-none-any.whl
```

<details>
<summary>使用国内镜像源</summary>

```bash
python -m pip install ask_user_via_feishu-<version>-py3-none-any.whl -i https://pypi.tuna.tsinghua.edu.cn/simple/
```
</details>

### 方式二：从源码安装

```bash
git clone https://github.com/Hxyspace/ask-user-via-feishu-mcp.git
cd ask-user-via-feishu-mcp
python -m pip install -e .
```

<details>
<summary>使用 venv 隔离依赖</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```
</details>

### 方式三：一键启动脚本

仓库根目录的 `start_mcp.py` 会自动创建 `.venv`、安装依赖并启动服务：

```bash
python start_mcp.py
```

## ▶️ 快速开始

### 方式一：一键创建飞书应用（推荐）

安装完成后，运行以下命令自动创建或选择已有飞书应用，并生成 MCP 配置：

```bash
python -m ask_user_via_feishu.new_bot
```

按提示扫码确认（或复制链接到浏览器），即可自动获取 App ID、App Secret 和 Owner Open ID，并输出可直接使用的 MCP 配置。

> 💡 安装 `qrcode` 库可在终端直接显示二维码：`pip install qrcode`

将输出的配置复制到你的 MCP Host 配置文件（如 `mcp.json`）中即可。

<details>
<summary>方式二：手动创建飞书应用</summary>

#### 1. 创建飞书应用

前往 [飞书开放平台](https://open.feishu.cn/app) 创建一个企业自建应用，获取 **App ID** 和 **App Secret**。

需要开通以下权限：
- `im:message` — 发送消息
- `im:message:send_as_bot` — 以机器人身份发消息
- `im:chat` — 读取群信息
- `im:resource` — 下载消息中的资源文件

启用 **机器人** 能力，并在事件订阅中启用长连接模式。

#### 2. 获取 Owner Open ID

在飞书开放平台的 API 调试台中，查询你自己的 `open_id`（以 `ou_` 开头的字符串）。

#### 3. 配置 MCP Host

在你的 MCP Host 配置文件（如 `mcp.json`）中添加：

```json
{
  "mcpServers": {
    "ask-user-via-feishu": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "ask_user_via_feishu"],
      "timeout": 36000000,
      "env": {
        "APP_ID": "cli_xxx",
        "APP_SECRET": "xxx",
        "OWNER_OPEN_ID": "ou_xxx"
      }
    }
  }
}
```

> 💡 `timeout` 建议设为较大值（如 36000000 ms = 10h），因为 `ask_user_via_feishu` 工具会长时间等待用户回复。

</details>

### 开始使用

MCP Host 启动后，LLM 即可调用以下工具与你通过飞书交互。

## 🔧 MCP 工具

| 工具 | 说明 |
|---|---|
| `send_text_message` | 发送文本消息到当前激活会话 |
| `send_image_message` | 发送本地图片到当前激活会话 |
| `send_file_message` | 发送本地文件到当前激活会话（支持 opus/mp4/pdf/doc/xls/ppt/stream） |
| `send_post_message` | 发送飞书 post 富文本消息（支持 text/a/at/img/media/emotion/hr/code_block/md 节点） |
| `ask_user_via_feishu` | 发送提问卡片并等待 owner 回复（文本、按钮选择或文件/图片） |

### `ask_user_via_feishu` 详细行为

**提问流程**：

1. 若未配置 `CHAT_ID` 且尚未选择目标会话，先在 owner P2P 会话中弹出选择卡片（当前会话 / 现有群聊 / 新建群聊）
2. 向目标会话发送问题卡片（可附带按钮选项）
3. 等待 owner 的文本回复、按钮点选、或图片/文件回传
4. 如果收到图片/文件，自动下载到 `attachments/YYYY-MM-DD/` 目录
5. 返回 `{ status, user_answer, downloaded_paths }`

**队列机制**：
- 不同会话的提问可并行进行
- 同一会话同时只有一个活跃提问，后续提问按 FIFO 排队
- 会话选择流程不占用提问队列

**超时机制**：
- 超时后按配置发送提醒消息（最多 `ASK_REMINDER_MAX_ATTEMPTS` 次）
- 所有提醒用完后，根据 `ASK_TIMEOUT_DEFAULT_ANSWER` 决定行为：
  - `[AUTO_RECALL]`（默认）：返回提示文案让 LLM 自动重新提问
  - 自定义文本：作为默认回答返回
  - 空字符串：返回 `status: "timeout"`

## ⚙️ 配置

项目支持 **环境变量** 和 **JSON 配置文件** 两种方式。两者同时存在时，环境变量优先。

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|:---:|---|---|
| `APP_ID` | ✅ | — | 飞书应用 App ID |
| `APP_SECRET` | ✅ | — | 飞书应用 App Secret |
| `OWNER_OPEN_ID` | ✅ | — | Owner 的 open_id |
| `CHAT_ID` | | 空 | 固定路由到该群聊；不配置则首次调用时弹卡选择 |
| `LOG_LEVEL` | | `INFO` | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| `BASE_URL` | | `https://open.feishu.cn` | 飞书开放平台地址 |
| `API_TIMEOUT_SECONDS` | | `10` | API 请求超时（秒） |
| `RUNTIME_CONFIG_PATH` | | 空 | JSON 配置文件路径 |

**Reaction 配置**：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `REACTION_ENABLED` | `true` | 收到回复后是否添加 reaction 标记 |
| `REACTION_EMOJI_TYPE` | `Typing` | Reaction 使用的 emoji 类型 |

**提问超时配置**：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ASK_TIMEOUT_SECONDS` | `600` | 单次等待回复超时（秒） |
| `ASK_REMINDER_MAX_ATTEMPTS` | `10` | 超时后最多提醒次数 |
| `ASK_TIMEOUT_REMINDER_TEXT` | `请及时回复！！！` | 超时提醒文案 |
| `ASK_TIMEOUT_DEFAULT_ANSWER` | `[AUTO_RECALL]` | 提醒用完后的默认返回值 |

**共享 Daemon 配置**：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DAEMON_IDLE_TIMEOUT_SECONDS` | `600` | 无请求后多久空闲退出（秒） |
| `DAEMON_IDLE_CHECK_INTERVAL_SECONDS` | `10` | 空闲检查轮询间隔（秒） |
| `DAEMON_MIN_UPTIME_SECONDS` | `60` | 启动后至少存活时间（秒） |

### JSON 配置文件

通过 `RUNTIME_CONFIG_PATH` 环境变量指定路径，参考 [`runtime_config.example.json`](./runtime_config.example.json)：

```json
{
  "app_id": "cli_xxx",
  "app_secret": "xxx",
  "owner_open_id": "ou_xxx",
  "chat_id": "oc_xxx",
  "reaction": {
    "enabled": true,
    "emoji_type": "Typing"
  },
  "ask": {
    "timeout_seconds": 600,
    "reminder_max_attempts": 10,
    "timeout_reminder_text": "请及时回复！！！",
    "timeout_default_answer": "[AUTO_RECALL]"
  },
  "daemon": {
    "idle_timeout_seconds": 600,
    "idle_check_interval_seconds": 10,
    "min_uptime_seconds": 60
  }
}
```

## 🏗️ 工作原理

```
MCP Host Process                       Shared Daemon Process
┌──────────────────────┐               ┌─────────────────────────────┐
│  MCP Server (stdio)  │    HTTP IPC   │  Shared Long-Connection     │
│  ┌────────────────┐  │ ────────────> │  ┌───────────────────────┐  │
│  │ ask/send tools │  │ <──────────── │  │ Feishu WebSocket Conn │  │
│  └────────────────┘  │               │  └───────────────────────┘  │
│                      │               │  ┌───────────────────────┐  │
│  daemon bootstrap:   │               │  │ Event Intercept       │  │
│  auto discover/start │               │  └───────────────────────┘  │
└──────────────────────┘               │  ┌───────────────────────┐  │
                                       │  │ Per-chat Ask Queue    │  │
Multiple MCP Host processes            │  └───────────────────────┘  │
    share one Daemon  ─────────────>   │     Auto-exit on idle,      │
                                       │    auto-start on demand     │
                                       └─────────────────────────────┘
```

- **MCP Server**：stdio 进程，挂载到 MCP Host 中，暴露 5 个工具。
- **Shared Daemon**：后台单例进程，维护与飞书的 WebSocket 长连接，接收事件并分发。多个 MCP 进程共享同一个 daemon。
- **自动生命周期**：MCP 客户端启动时会 best-effort 探测已有 daemon 版本，不一致时请求旧 daemon 退出；首次调用 ask/send 时自动启动 daemon；daemon 空闲超时后自动退出；下次调用时再自动拉起。
- **Per-chat 队列**：每个目标会话有独立的 FIFO 提问队列，不同会话可并行提问。

## 🧪 开发

```bash
# 创建隔离环境并安装
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# 运行测试
python -m unittest discover -s tests -v

# 本地打包
python -m pip install build
python -m build
```

## 🧩 项目结构

```text
.
├── src/ask_user_via_feishu/
│   ├── clients/
│   │   └── feishu_sdk.py       # 飞书 SDK 封装（消息/文件/群组/reaction）
│   ├── services/
│   │   └── message_service.py  # 消息发送/上传/下载的业务层
│   ├── daemon/
│   │   ├── app.py              # 共享 daemon 应用主体
│   │   ├── server.py           # daemon HTTP 服务器
│   │   ├── bootstrap.py        # daemon 发现/启动/健康检查
│   │   └── runtime.py          # runtime 目录和元数据管理
│   ├── ipc/
│   │   └── client.py           # MCP 进程 → daemon 的 IPC 客户端
│   ├── config.py               # 配置加载（环境变量 + JSON）
│   ├── server.py               # MCP Server 工具注册和路由
│   ├── ask_runtime.py          # ask 流程编排（发卡片/等回复/超时处理）
│   ├── ask_state.py            # per-chat FIFO 队列状态机
│   ├── shared_longconn.py      # 共享长连接运行时（事件拦截/问题分发）
│   ├── longconn.py             # 飞书长连接订阅器
│   ├── event_processor.py      # 事件路由
│   ├── event_handlers.py       # 消息/卡片事件处理
│   ├── schemas.py              # 类型定义（post 元素/文件类型）
│   ├── errors.py               # 异常类型
│   ├── runtime.py              # 工厂函数
│   ├── new_bot.py              # 一键创建飞书应用（python -m ask_user_via_feishu.new_bot）
│   ├── main.py                 # 入口
│   └── logging_utils.py        # 日志配置
├── tests/                      # 单元测试
├── examples/
│   └── mcpServers.ask-user-via-feishu.json
├── pyproject.toml
├── runtime_config.example.json
├── start_mcp.py                # 一键启动脚本
└── LICENSE
```

## ⚠️ 已知限制

- **Owner-only**：只接受配置的 owner 的回复，不适用于多人共享机器人场景。即使路由到群聊，也不会接受其他群成员的回答。
- **会话选择不持久**：未配置 `CHAT_ID` 时，选择的目标会话只保存在 MCP 进程内存中，进程重启后需要重新选择。
- **队列不跨重启**：提问队列和 pending 状态不会跨 daemon 重启持久化。
- **Stdio 模式**：当前只支持 stdio 传输，不支持 HTTP/SSE 模式。

## 📄 许可证

[MIT License](./LICENSE)
