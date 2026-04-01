# 强制交流协议
这是不可跳过的强制协议

## 核心规则：每次回复交流都必须走以下两种交流通道之一
1. **feishu通道** -> 飞书交流，通过`ask_user_via_feishu`mcp工具中的`send_text_message`和`ask_user_via_feishu`工具交流回复。
2. **local通道** -> 本地交流，通过copilot终端和`ask_user`/`askQuestions`工具交流回复。

## 交流准则

1. 当我让你切到飞书通道交互时，你必须通过`ask_user_via_feishu`mcp工具跟我交流，每次任务完成或者需要决策时都从飞书询问我，除非我主动停止或让你切到别的通道。永远都不能主动结束对话，除非我让你结束。`send_text_message`只能通知不能等待回复，`ask_user_via_feishu`可以通知消息或决策选项然后等待回复。可以先通过`send_text_message`输出长的总结消息，紧接着通过`ask_user_via_feishu`等待询问下一步指示。

2. 当我让你切到本地通道交互时，你必须通过`ask_user`/`askQuestions`工具跟我交流，每次任务完成或者需要决策时都通过`ask_user`/`askQuestions`询问我，除非我主动停止或让你切到别的通道。永远都不能主动结束对话，除非我让你结束。`ask_user`/`askQuestions`通知消息并给出决策选项然后等待。可以先输出长的总结消息，紧接着通过`ask_user`/`askQuestions`等待询问下一步指示。

## 交流链路
feishu mode: `ask_user_via_feishu`/`send_text_message`
local mode: `ask_user`/`askQuestions`

