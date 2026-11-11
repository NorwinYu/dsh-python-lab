# 上游一致性基线

## 固定版本

- 上游：`deepseek-ai/deepseek-harness`
- Commit：`fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`
- 对应包版本：`0.2.2-rc.1`
- 上游协议实现：`packages/llm/llm-deepseek/src/{adapter,serialize,sse,translate}.ts`
- 上游提示词实现：`packages/core/system-prompt/src/index.ts` 与各工具插件
- 上游审批与文件策略：`packages/interaction/user-approval`、`packages/sandbox/sandbox-policy`
- 上游文件工具：`packages/fs/tool-fs`、`packages/fs/tool-fs-search`

本项目的一致性声明只覆盖下文列出的切片，不代表完整 DSH 已移植完成。

## 当前已对齐

### 系统提示词与消息

- 固定身份段：`You are an AI agent powered by DeepSeek Harness.`
- `read/write/edit/glob` 分别排在 order 1100/1200/1300/1400，使用上游英文原文。
- `write` 是否附带 `and prefer edit for targeted changes` 随 `edit` 开关变化。
- 禁用工具会同时移除工具 schema 和对应提示词段。
- runtime context 使用官方引导语，依次包含工作目录、文件策略和审批策略，并作为独立用户快照放在用户指令之后。
- 助手消息包含有序的 `text / reasoning / tool-call` 内容块，工具结果通过 `toolCallId` 回填。

可用下面的命令检查当前组合结果：

```bash
PYTHONPATH=src python3 -m minidsh prompt
PYTHONPATH=src python3 -m minidsh --disable tools.edit prompt
PYTHONPATH=src python3 -m minidsh --sandbox-mode workspace-write prompt
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
- 跨模型切换丢弃旧模型签名；HTTP 重定向不跟随

### 工具

- 工具名为官方的 `read`、`glob`、`write`、`edit`。
- `read` 的描述、参数、2000 行默认上限、2000 字符单行上限、50KiB 字节上限、行号及输出信封按上游实现。
- `glob` 的描述、参数、100 条上限、隐藏文件搜索、VCS 目录排除和修改时间排序按上游模型行为实现。
- `write` 支持创建和完整覆盖，返回官方 `Created file / Updated file` 输出信封。
- `edit` 默认要求旧文本恰好出现一次，`replace_all` 替换全部匹配，返回上游确认文本。
- 覆盖或编辑前必须在同一 session 中有成功读取/写入观察；文件版本在读取后变化会拒绝写入。
- `workspace-write` 会解析并限制绝对路径与符号链接，`danger-full-access` 不施加工作区路径限制。

### 审批与文件策略

- 默认文件策略为 `read-only`，写操作在审批前直接拒绝。
- `workspace-write` 使用审批策略决定写操作是否执行。
- `ask` 在没有可用应答者时返回 `unavailable` 并失败关闭；`never` 返回 `rejected`。
- `allowed-once` 只允许当前操作，不自动转换为后续操作的授权。
- 未显式指定审批策略时，`danger-full-access` 对应 `never`；该模式不触发写前审批。
- `approval/asked` 和 `approval/decided` 成对记录，包含 request id、toolName、callId 和 outcome。
- CLI 的 `--yes` 是学习版提供的进程级显式授权，会允许本次进程内所有写操作，应谨慎使用。

## 尚未对齐

- 官方完整工具集、profile/bundle 格式、运行时热重载和外部 npm 插件。
- Landlock/bwrap 等内核沙箱、Shell、PTY、子代理、MCP、Web、Office、语音和 Computer Use。
- `sandbox_permissions` 升级请求、会话中切换审批/文件策略、审批取消和跨进程审批协调。
- 图片与 Files API、模型 listing、请求重试、扩展请求头和遥测。
- 流式 UI 增量事件、会话 resume/fork、消息投影、压缩和跨进程锁。
- `read` 当前一次性读取文本，尚未实现上游的大文件流式扫描。
- `glob` 使用 Python 文件遍历而非 ripgrep；复杂 glob 语义和完整结果 spill 尚未完全对齐。
- 匿名用户 ID 当前只在进程内稳定，尚未持久化。
- 当前只验证了本地 Messages/SSE 服务，尚未对真实 DeepSeek 服务做带凭据的在线验收。

## 已执行验证

- Mock Agent 的 `glob/read` 回归和功能开关检查。
- 本地 HTTP Messages/SSE 两轮请求、reasoning signature 回放、工具结果回填和 JSONL 持久化。
- 交错 SSE 内容块保持开始顺序；跨模型签名丢弃；HTTP 302 拒绝跟随。
- 默认 `read-only` 拒绝写操作且不产生审批请求。
- 无应答者返回 `unavailable`、`never` 返回 `rejected`，文件均未创建。
- 显式允许后完成创建、同会话读取、编辑和审批审计。
- 新会话未读覆盖拒绝、读取后外部修改拒绝、默认多次匹配拒绝、`replace_all` 成功。
- 路径穿越和符号链接逃逸被拒绝；`danger-full-access` 不触发审批。
- Mock CLI 完成 `write` 创建，以及先 `read` 再 `edit` 的两步流程。

后续修改如果改变上述已对齐行为，必须在 Git 变更中同时修改本文件并说明原因；升级上游 commit 时必须重新核对，不得静默替换提示词或协议。
