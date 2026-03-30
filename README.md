# ask-user-via-feishu

![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-green.svg)
![Protocol](https://img.shields.io/badge/protocol-MCP-purple.svg)
![Platform](https://img.shields.io/badge/platform-Feishu-00B96B.svg)
![License](https://img.shields.io/badge/license-MIT-yellow.svg)
[![Build](https://github.com/Hxyspace/ask-user-via-feishu-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/Hxyspace/ask-user-via-feishu-mcp/actions/workflows/build.yml)

## 📋 项目介绍

`ask-user-via-feishu` 是一个面向单一 owner 的 Feishu MCP Server，用来把 LLM 的“发消息”和“向用户提问并等待回复”能力接到飞书 owner 会话中。当前版本仍然只接受配置 owner 的回复，但消息与提问目标已经可以落到 owner 当前会话或选定的群聊。

项目基于 Python 与 `mcp[cli]` 实现，默认通过 stdio 运行，挂到 MCP Host 中使用。它支持发送文本、图片、文件、Feishu post 富文本消息，也支持通过共享的飞书长连接在后台等待 owner 的文本回复、卡片按钮选择，或图片/文件资源回传。

> 迁移说明（Phase 1）
>
> 当前版本已经把消息发送链路统一到 `lark_oapi`：共享长连接和 send/upload/download/update/reaction 都走 Feishu SDK。
> MCP 工具层的输入输出契约保持不变；如果你在仓库内部直接引用过旧的 `clients.feishu_auth`、`clients.feishu_messages` 或 `services.token_manager`，现在应改为使用 `clients.feishu_sdk.FeishuSDKClient`，或更高层的 `runtime.build_message_service()`。

## 🚀 主要特性

- **owner-only 模式**：只接受配置的 `OWNER_OPEN_ID` 对应用户事件，避免多人混用。
- **标准 MCP 工具接口**：默认暴露 5 个工具，便于 MCP Host 直接调用。
- **双向交互**：既能主动发消息，也能等待飞书侧用户回复。
- **共享长连接运行时**：`ask_user_via_feishu` 使用共享长连接监听飞书事件，避免每次提问都单独建立事件通道。
- **卡片按钮选择**：提问时可附带选项，飞书侧会渲染交互卡片按钮。
- **会话目标切换**：可通过静态 `CHAT_ID` 直接路由到群聊；未配置时，首次 send/ask 会先在 owner 当前会话里弹出一张 1.0 结构的选群卡片，支持点击切回当前会话、选择已发现群聊，或在卡片中输入群名后新建群聊。
- **资源回传下载**：用户如果回复图片或文件，服务会以流式写盘方式下载到共享 daemon runtime 目录下的 `attachments/YYYY-MM-DD/` 目录，并在返回结果中给出本地路径。
- **超时提醒与默认答案**：支持提醒重试、默认答案，以及 `[AUTO_RECALL]` 自动召回模式。

## 🎯 适用场景

- 让 LLM 通过飞书当前会话或项目群聊向用户发送执行结果、告警或中间状态。
- 让 LLM 在需要人工确认时，通过飞书发起提问并等待用户的回复。
- 让用户通过飞书上传文件或图片，再把资源路径返回给 LLM 继续处理。
- 适合人在外面、不在电脑旁时，也能直接通过飞书继续给 LLM 下一步指示。

## 📦 安装部署

### 下载预构建版本（推荐）

可从 [GitHub Releases](https://github.com/Hxyspace/ask-user-via-feishu-mcp/releases) 下载预构建安装包：

- `ask_user_via_feishu-<version>-py3-none-any.whl`

### 安装 `.whl` 包

```bash
python -m pip install ask_user_via_feishu-<version>-py3-none-any.whl
```

指定镜像源安装：

```bash
python -m pip install ask_user_via_feishu-<version>-py3-none-any.whl -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

安装完成后，可以通过以下任一方式启动服务：

```bash
ask-user-via-feishu
```

或者：

```bash
python -m ask_user_via_feishu
```

### MCP Host 接入示例

在 MCP Host 的对应配置文件中加入以下配置（`mcp.json`）：

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

### 从源码安装

如果需要开发、调试或维护这个仓库，可以直接从源码安装：

```bash
python -m pip install -e .
```

如果希望在项目目录中隔离依赖，也可以使用 `.venv`：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

也可以继续使用仓库根目录下的 `start_mcp.py`，会自动在项目目录中隔离依赖，安装并启动：

```bash
python start_mcp.py
```

## ▶️ 使用说明

### 本地直接运行

```bash
export APP_ID="cli_xxx"
export APP_SECRET="xxx"
export OWNER_OPEN_ID="ou_xxx"
python -m ask_user_via_feishu
```

如果使用项目内 `.venv`：

```bash
export APP_ID="cli_xxx"
export APP_SECRET="xxx"
export OWNER_OPEN_ID="ou_xxx"
.venv/bin/python -m ask_user_via_feishu
```

自动隔离、安装并启动：

```bash
python start_mcp.py
```

### 使用运行时配置文件

```bash
export RUNTIME_CONFIG_PATH="/absolute/path/to/runtime_config.json"
python -m ask_user_via_feishu
```

### MCP Host 示例配置文件

仓库已经提供示例配置：

- `examples/mcpServers.ask-user-via-feishu.json`

如果你希望通过 JSON 文件注入更多配置，也可以在 `env` 中增加：

```json
{
  "RUNTIME_CONFIG_PATH": "/absolute/path/to/runtime_config.json"
}
```

## ⚙️ 配置说明

项目支持两种配置来源：

- 环境变量
- `RUNTIME_CONFIG_PATH` 指向的 JSON 配置文件

当两者同时存在时，**环境变量优先**。

### 环境变量

可参考 `.env.example`：

| 变量名 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `APP_ID` | 是 | - | 飞书应用 App ID |
| `APP_SECRET` | 是 | - | 飞书应用 App Secret |
| `OWNER_OPEN_ID` | 是 | - | 允许交互的唯一 owner |
| `CHAT_ID` | 否 | 空 | 固定把 send/ask 路由到该群聊；未配置时首次调用会走会话选择 bootstrap |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `BASE_URL` | 否 | `https://open.feishu.cn` | 飞书开放平台地址 |
| `API_TIMEOUT_SECONDS` | 否 | `10` | Feishu SDK / API 请求超时时间 |
| `RUNTIME_CONFIG_PATH` | 否 | 空 | 运行时 JSON 配置文件路径 |
| `REACTION_ENABLED` | 否 | `true` | 是否为回复消息添加处理中 reaction |
| `REACTION_EMOJI_TYPE` | 否 | `Typing` | 处理中 reaction 类型 |
| `ASK_TIMEOUT_SECONDS` | 否 | `600` | 单次等待回复超时秒数 |
| `ASK_REMINDER_MAX_ATTEMPTS` | 否 | `10` | 超时后最多提醒次数 |
| `ASK_TIMEOUT_REMINDER_TEXT` | 否 | `请及时回复！！！` | 超时提醒文案 |
| `ASK_TIMEOUT_DEFAULT_ANSWER` | 否 | `[AUTO_RECALL]` | 超时后的默认返回值；留空则返回 `timeout` |

### 运行时 JSON 配置

可参考 `runtime_config.example.json`：

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
  }
}
```

启动前至少要保证 `APP_ID`、`APP_SECRET` 和 `OWNER_OPEN_ID` 最终可被解析到，否则服务会在启动校验阶段报错。`CHAT_ID` 是可选项：配置后会直接把后续 send/ask 路由到该会话；不配置则在第一次 send/ask 时，通过 owner 当前 P2P 会话弹卡选择目标。

## 🔧 MCP 工具

当前默认暴露以下 5 个工具：

| 工具名 | 作用 |
| --- | --- |
| `send_text_message` | 向当前激活会话发送文本消息 |
| `send_image_message` | 向当前激活会话发送图片 |
| `send_file_message` | 向当前激活会话发送文件 |
| `send_post_message` | 向当前激活会话发送 Feishu post 富文本消息 |
| `ask_user_via_feishu` | 向当前激活会话发起提问，并等待 owner 的文本回复、按钮选择或资源回复 |

### `ask_user_via_feishu` 的核心行为

- 若未配置 `CHAT_ID` 且当前进程内还没有选中过目标，会先在 owner 当前 P2P 会话里发一张“选择会话”卡片；
- 若要新建群聊，可在选择卡片中输入群名并点击提交；
- 选定目标后，向当前激活会话发送问题卡片；
- 可选附带按钮选项；
- 后台等待 owner 在该目标会话中的下一条文本回复、卡片按钮操作，或资源消息；
- 如果回复包含图片/文件，会以流式方式自动下载到共享 daemon runtime 目录；
- 返回结构中包含 `status`、`user_answer`、`downloaded_paths`。

超时行为：

- 若达到超时阈值，会按配置发送提醒消息；
- 若 `ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]"`，会返回一段提示文案，通知上层 LLM 重新发起同一个问题；
- 若 `ASK_TIMEOUT_DEFAULT_ANSWER=""`，则返回 `status: "timeout"`；
- 不支持同一 owner 并发等待多个 pending question。

## 📥 回复资源文件时的行为

当用户只发送图片或文件、没有文本时：

- 服务会把资源流式下载到共享 daemon runtime 目录下的 `attachments/YYYY-MM-DD/`
- `ask_user_via_feishu` 会返回这些本地路径
- 若同一天内文件名冲突，会自动追加 fallback 名称避免覆盖
- 同时返回一段提示文本，指导上层 LLM 基于这些文件继续发起下一轮问题

## 🏗️ 自动构建发布（推荐）

项目已配置 GitHub Actions 自动构建与发布。

### 🚀 一键构建发布 [![Build](https://github.com/Hxyspace/ask-user-via-feishu-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/Hxyspace/ask-user-via-feishu-mcp/actions/workflows/build.yml)

## 🧪 本地验证（开发者）

推荐在项目内 `.venv` 环境中测试：

```bash
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

如需本地打包验证：

```bash
python -m pip install build
python -m build
```

## 🧩 项目结构

```text
.
├── .github/
│   ├── copilot-instructions.md
│   └── workflows/
│       └── build.yml
├── examples/
│   └── mcpServers.ask-user-via-feishu.json
├── src/ask_user_via_feishu/
│   ├── clients/
│   │   └── feishu_sdk.py
│   ├── daemon/
│   ├── ipc/
│   ├── services/
│   ├── config.py
│   ├── longconn.py
│   ├── shared_longconn.py
│   ├── runtime.py
│   ├── server.py
│   └── main.py
├── tests/
├── LICENSE
├── start_mcp.py
├── runtime_config.example.json
└── pyproject.toml
```

## 🧠 配套参考

如果准备把这个项目和 GitHub Copilot / MCP 工作流一起使用，也可以参考仓库内的自定义规则文件：

- [`./.github/copilot-instructions.md`](./.github/copilot-instructions.md)

当前这份规则主要约束模型在 **feishu 通道** 与 **local 通道** 下的交流方式，适合需要把“任务执行”和“飞书交互”统一起来的工作流。

## ⚠️ 已知限制

- 这是一个 **owner-only** 服务，不适用于多人共享机器人场景。
- 当前只允许配置的 owner 作为合法回复 actor；即使 send/ask 路由到了群聊，也不会接受其他成员的回答。
- 只支持全局单 pending ask；暂不支持不同群聊上的并发 ask / queue。
- 未配置 `CHAT_ID` 时，激活目标只保存在当前 MCP 进程内存中；进程重启后需要重新选择。
- 同一 `OWNER_OPEN_ID` 同时只能存在一个 pending question。
- 项目默认运行模式是 stdio MCP Server。

## 📄 许可证

本项目采用 [MIT License](./LICENSE)。
