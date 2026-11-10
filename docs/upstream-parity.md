# 上游一致性基线

## 固定版本

- 上游：`deepseek-ai/deepseek-harness`
- Commit：`fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`
- 对应包版本：`0.2.2-rc.1`
- 上游协议实现：`packages/llm/llm-deepseek/src/{adapter,serialize,sse,translate}.ts`
- 上游提示词实现：`packages/core/system-prompt/src/index.ts` 与各工具插件

本项目的一致性声明只覆盖下文列出的切片，不代表完整 DSH 已移植完成。

## 当前已对齐

### 系统提示词与消息

- 固定身份段：`You are an AI agent powered by DeepSeek Harness.`
- `read` 段排在 order 1100，`glob` 段排在 order 1400，使用上游英文原文。
- 禁用工具会同时移除工具 schema 和对应提示词段。
- runtime context 使用官方引导语和 `Current working directory: "<absolute-path>".` 格式，并作为独立用户快照放在用户指令之后。
- 助手消息包含有序的 `text / reasoning / tool-call` 内容块，工具结果通过 `toolCallId` 回填。

可用下面的命令检查当前组合结果：

```bash
PYTHONPATH=src python3 -m minidsh prompt
PYTHONPATH=src python3 -m minidsh --disable tools.read prompt
```

### DeepSeek Messages 协议

- 默认根地址：`https://api.deepseek.com/anthropic`
- 请求地址：根地址以 `/v1` 结尾时追加 `/messages`，否则追加 `/v1/messages`
- 认证：`x-api-key`
- 协议头：`anthropic-version: 2023-06-01`
- 默认模型：`deepseek-flash`
- 默认 thinking：`high`，可切换 `off / low / high / max`
- 默认 `max_tokens`：256000
- 默认流空闲超时：300000ms
- 请求体支持文本、reasoning、tool_use、tool_result 和工具 JSON Schema
- SSE 支持 text、thinking、signature、input_json_delta、usage 和 stop reason
- 后续请求保留 `deepseek-messages` replay envelope 与 reasoning signature
- HTTP 重定向不跟随

### 工具

- 工具名为官方的 `read` 和 `glob`，不再使用学习版自定义的 `read_file` 和 `list_dir`。
- `read` 的描述、参数、2000 行默认上限、2000 字符单行上限、50KiB 字节上限、行号及输出信封按上游实现。
- `glob` 的描述、参数、100 条上限、隐藏文件搜索、VCS 目录排除和修改时间排序按上游模型行为实现。

## 尚未对齐

- 官方完整工具集、profile/bundle 格式、运行时热重载和外部 npm 插件。
- 审批、内核沙箱、写工具、Shell、PTY、子代理、MCP、Web、Office、语音和 Computer Use。
- 图片与 Files API、模型 listing、请求重试、扩展请求头和遥测。
- 流式 UI 增量事件、会话 resume/fork、消息投影、压缩和跨进程锁。
- `read` 当前一次性读取文本，尚未实现上游的大文件流式扫描。
- `glob` 使用 Python 文件遍历而非 ripgrep；复杂 glob 语义、符号链接和完整结果 spill 尚未完全对齐。
- 匿名用户 ID 当前只在进程内稳定，尚未持久化。
- 当前只验证了本地 Messages/SSE 服务，尚未对真实 DeepSeek 服务做带凭据的在线验收。

## 已执行验证

- Mock Agent 的 `glob`、`read` 正常调用和功能开关检查。
- 本地 HTTP Messages/SSE 服务完成两轮请求：第一轮 reasoning + `read` 工具调用，第二轮检查 reasoning signature 和工具结果回填。
- JSONL 中保存了 replay metadata、usage、assistant/tool 事件，未保存测试 API Key。
- `llm.deepseek` 可独立开关；缺少 `DEEPSEEK_API_KEY` 时返回 `MISSING_CREDENTIAL`，不读取其他密钥文件。
- 交错 SSE 内容块仍按开始顺序组装；跨模型切换会丢弃旧模型的 reasoning signature。
- HTTP 302 不会跟随或转发凭据；错误响应体连接重置也统一归为 `TRANSPORT`。

后续修改如果改变上述已对齐行为，必须在 Git 变更中同时修改本文件并说明原因；升级上游 commit 时必须重新核对，不得静默替换提示词或协议。
