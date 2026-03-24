# ask-user-via-feishu

一个面向单一 owner 的 Feishu MCP Server，用来把 LLM 的“发消息”和“向用户提问并等待回复”能力接到飞书私聊中。

项目基于 Python 与 `mcp[cli]` 实现，默认通过 stdio 运行，适合挂到 MCP Client 中使用。它支持主动发送文本、图片、文件、富文本消息，也支持通过共享的飞书长连接在后台等待 owner 的文本回复、卡片按钮选择，或上传的图片/文件。

## 功能特性

- **owner-only 模式**：只接受配置的 `OWNER_OPEN_ID` 对应用户的事件，避免多人混用。
- **标准 MCP 工具接口**：默认暴露 5 个工具，便于 MCP Client 直接调用。
- **双向交互**：既能主动发消息，也能等待飞书侧用户回复。
- **共享长连接运行时**：`ask_user_via_feishu` 使用共享长连接监听飞书事件，避免每次提问都单独建立事件通道。
- **卡片按钮选择**：提问时可附带选项，飞书侧会渲染交互卡片按钮。
- **资源回传下载**：用户如果回复图片或文件，服务会自动下载到本地 `receive_files/<question_id>/` 目录。
- **超时提醒与默认答案**：支持提醒重试、默认答案，以及 `[AUTO_RECALL]` 自动召回模式。
- **启动简单**：既可以直接运行，也可以通过 `start_mcp.py` 自动创建 `.venv` 后启动。

## 适用场景

- 让 LLM 通过飞书私聊向你发送执行结果、告警或中间状态。
- 让 MCP Client 在需要人工确认时，通过飞书发起提问并等待你的回复。
- 让用户通过飞书上传文件或图片，再把资源路径返回给 LLM 继续处理。
- 适合人在外面、不在电脑旁时，也能直接通过飞书继续给 LLM 下一步指示。

## 工作原理

项目的核心流程如下：

1. MCP Client 通过 stdio 调用工具，例如 `send_text_message` 或 `ask_user_via_feishu`。
2. 服务通过 `FeishuAuthClient` 获取租户访问令牌，并由 `TokenManager` 做缓存。
3. `MessageService` 调用飞书开放平台消息接口发送文本、图片、文件、卡片或富文本消息。
4. 当调用 `ask_user_via_feishu` 时，服务会：
   - 发送一条交互卡片给 owner；
   - 启动或复用共享长连接运行时；
   - 注册当前 pending question；
   - 等待 owner 的文本、按钮选择、图片或文件回复；
   - 将结果整理为 MCP 工具返回值。
5. 如果用户仅回复资源文件而没有文本，工具会返回下载路径，并给出提示文本，引导 LLM 继续发起下一轮问题。

关键模块：

- `src/ask_user_via_feishu/server.py`：MCP 工具注册与问答主流程。
- `src/ask_user_via_feishu/shared_longconn.py`：共享长连接运行时、pending question 管理、事件拦截。
- `src/ask_user_via_feishu/longconn.py`：飞书长连接订阅器封装。
- `src/ask_user_via_feishu/services/message_service.py`：消息发送、资源下载、卡片更新、reaction 处理。
- `src/ask_user_via_feishu/clients/feishu_auth.py`：租户 token 获取。
- `src/ask_user_via_feishu/clients/feishu_messages.py`：飞书消息相关 HTTP API 调用。
- `src/ask_user_via_feishu/config.py`：环境变量与运行时配置加载。

## 目录结构

```text
.
├── .github/
│   └── copilot-instructions.md
├── examples/
│   └── mcpServers.ask-user-via-feishu.json
├── src/ask_user_via_feishu/
│   ├── clients/
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

## 环境要求

- Python 3.10+
- 一个可用的飞书应用，并已获取：
  - `APP_ID`
  - `APP_SECRET`
  - `OWNER_OPEN_ID`
- MCP Client 能以 stdio 方式启动该服务

## 安装

### 方式一：推荐使用项目内 `.venv`

如果你希望和仓库测试方式保持一致，推荐直接在项目根目录创建 `.venv`：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

启动服务：

```bash
.venv/bin/python -m ask_user_via_feishu
```

运行测试：

```bash
.venv/bin/python -m unittest discover -s tests
```

### 方式二：开发/本地运行

```bash
pip install -e .
```

安装后可以直接执行：

```bash
ask-user-via-feishu
```

或者：

```bash
python -m ask_user_via_feishu
```

### 方式三：给 MCP Client 使用 `start_mcp.py`

`start_mcp.py` 会自动：

- 创建项目根目录下的 `.venv`
- 安装当前项目依赖
- 启动 `python -m ask_user_via_feishu`

这更适合 MCP Client 直接拉起服务。

## 配置

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
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `BASE_URL` | 否 | `https://open.feishu.cn` | 飞书开放平台地址 |
| `API_TIMEOUT_SECONDS` | 否 | `10` | HTTP 请求超时时间 |
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

启动前至少要保证 `APP_ID`、`APP_SECRET` 和 `OWNER_OPEN_ID` 最终可被解析到，否则服务会在启动校验阶段报错。

## 运行

### 直接运行

```bash
export APP_ID="cli_xxx"
export APP_SECRET="xxx"
export OWNER_OPEN_ID="ou_xxx"
python -m ask_user_via_feishu
```

如果你使用项目内 `.venv`，可以直接执行：

```bash
.venv/bin/python -m ask_user_via_feishu
```

### 使用运行时配置文件

```bash
export RUNTIME_CONFIG_PATH="/absolute/path/to/runtime_config.json"
python -m ask_user_via_feishu
```

## 在 MCP Client 中配置

仓库已经提供示例：`examples/mcpServers.ask-user-via-feishu.json`

示例配置如下：

```json
{
  "mcpServers": {
    "ask-user-via-feishu": {
      "type": "stdio",
      "command": "python",
      "args": ["/absolute/path/to/ask_user_via_feishu/start_mcp.py"],
      "timeout": 36000000,
      "env": {
        "APP_ID": "cli_xxx",
        "APP_SECRET": "xxx",
        "OWNER_OPEN_ID": "ou_xxx",
        "REACTION_ENABLED": "true",
        "REACTION_EMOJI_TYPE": "Typing",
        "ASK_TIMEOUT_SECONDS": "600",
        "ASK_TIMEOUT_REMINDER_TEXT": "请及时回复！！！"
      }
    }
  }
}
```

如果你希望通过 JSON 文件注入更多配置，也可以在 `env` 中增加：

```json
{
  "RUNTIME_CONFIG_PATH": "/absolute/path/to/runtime_config.json"
}
```

## 配套参考

如果你准备把这个项目和 GitHub Copilot / MCP 工作流一起使用，也可以参考仓库内的自定义规则文件：

- [`./.github/copilot-instructions.md`](./.github/copilot-instructions.md)

当前这份规则主要约束模型在 **feishu 通道** 与 **local 通道** 下的交流方式，适合需要把“任务执行”和“飞书交互”统一起来的工作流。

## MCP 工具

当前默认暴露以下 5 个工具：

| 工具名 | 作用 |
| --- | --- |
| `send_text_message` | 向 owner 发送文本消息 |
| `send_image_message` | 向 owner 发送图片 |
| `send_file_message` | 向 owner 发送文件 |
| `send_post_message` | 向 owner 发送 Feishu post 富文本消息 |
| `ask_user_via_feishu` | 向 owner 发起提问，并等待文本回复、按钮选择或资源回复 |

### `send_text_message`

发送纯文本通知。

### `send_image_message`

上传并发送图片，适合截图、图表等内容。

### `send_file_message`

上传并发送文件。支持 `opus`、`mp4`、`pdf`、`doc`、`xls`、`ppt`、`stream`，其他类型建议用 `stream`。

### `send_post_message`

发送 Feishu post 富文本消息，支持：

- `text`
- `a`
- `at`
- `img`

### `ask_user_via_feishu`

这是项目最核心的工具，行为如下：

- 向 owner 发送问题卡片；
- 可选附带按钮选项；
- 后台等待 owner 的下一条私聊回复或卡片按钮操作；
- 如果回复包含图片/文件，会自动下载到本地；
- 返回结构中包含：
  - `status`
  - `user_answer`
  - `downloaded_paths`

超时行为：

- 若达到超时阈值，会按配置发送提醒消息；
- 若 `ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]"`，会返回一段提示文案，通知上层 LLM 重新发起同一个问题；
- 若 `ASK_TIMEOUT_DEFAULT_ANSWER=""`，则返回 `status: "timeout"`；
- 不支持同一 owner 并发等待多个 pending question。

## 回复资源文件时的行为

当用户只发送图片或文件、没有文本时：

- 服务会下载资源到 `receive_files/<question_id>/`
- `ask_user_via_feishu` 会返回这些本地路径
- 同时返回一段提示文本，指导上层 LLM 基于这些文件继续发起下一轮问题

这类行为对“先收资料，再继续追问”的工作流很有用。

## 测试

仓库测试主要使用 `unittest`。

运行全部测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -q
```

如果你按推荐方式使用项目内 `.venv`，也可以直接运行：

```bash
.venv/bin/python -m unittest discover -s tests
```

测试覆盖了以下关键行为：

- 配置加载与默认值
- `start_mcp.py` 的虚拟环境与依赖安装逻辑
- 长连接事件注册与共享问答运行时
- 消息发送、reaction、资源下载
- `ask_user_via_feishu` 的超时、自动召回、资源回复和工具注册逻辑

## 开发说明

- 入口脚本为 `src/ask_user_via_feishu/main.py`
- `server.run(transport="stdio")` 说明它默认作为 stdio MCP Server 运行
- `start_mcp.py` 更偏向“客户端启动器”
- 当前版本号为 `0.1.0`

## 已知限制

- 这是一个 **owner-only** 服务，不适用于多人共享机器人场景。
- 当前仅处理飞书私聊（P2P）上下文，不处理群聊交互。
- 同一 `OWNER_OPEN_ID` 同时只能存在一个 pending question。
- 项目默认运行模式是 stdio MCP Server。

## 为什么用这个项目

如果你已经在使用 MCP Client，希望把“问用户”和“发通知”接到飞书里，而不是停留在本地终端交互，那么这个项目可以提供一个非常轻量、清晰且可扩展的桥接层。

它特别适合需要“LLM 执行任务 → 飞书询问 owner → owner 回复 → LLM 继续执行”这类闭环工作流的场景。

## License

本项目采用 [MIT License](./LICENSE)。
