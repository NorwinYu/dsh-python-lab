# 上游一致性基线

## 固定版本

- 上游：`deepseek-ai/deepseek-harness`
- Commit：`fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`
- 对应包版本：`0.2.2-rc.1`
- 模型协议：`packages/llm/llm-deepseek`
- 提示词：`packages/core/system-prompt` 与各工具插件
- 审批与文件策略：`packages/interaction/user-approval`、`packages/sandbox/sandbox-policy`
- 文件工具：`packages/fs/tool-fs`、`packages/fs/tool-fs-search`
- Shell 与任务：`packages/shell/tool-bash`、`packages/jobs/{jobs-local,tool-jobs}`

一致性声明只覆盖下文列出的切片，不代表完整 DSH 已移植完成。

## 当前已对齐

### 系统提示词与消息

- 固定身份段：`You are an AI agent powered by DeepSeek Harness.`
- `bash/read/write/edit/glob/jobs` 分别排在 order 1000/1100/1200/1300/1400/1600，使用上游英文原文。
- `write` 是否附带 `and prefer edit for targeted changes` 随 `edit` 开关变化。
- 禁用工具会同时移除工具 schema 和对应提示词段；缺少 `jobs` 时 Shell/jobs 插件保持 pending。
- runtime context 依次包含工作目录、文件策略和审批策略，并作为独立用户快照放在用户指令之后。
- 助手消息包含有序的 `text / reasoning / tool-call` 内容块，工具结果通过 `toolCallId` 回填。

检查方式：

```bash
PYTHONPATH=src python3 -m minidsh prompt
PYTHONPATH=src python3 -m minidsh --disable tools.edit prompt
PYTHONPATH=src python3 -m minidsh --disable core.jobs plugins
```

### DeepSeek Messages 协议

- 默认根地址：`https://api.deepseek.com/anthropic`
- 请求地址：根地址以 `/v1` 结尾时追加 `/messages`，否则追加 `/v1/messages`
- 认证：`x-api-key`；协议头：`anthropic-version: 2023-06-01`
- 默认模型：`deepseek-flash`；默认 thinking：`high`
- 默认 `max_tokens`：256000；默认流空闲超时：300000ms
- 支持文本、reasoning、tool_use、tool_result 和工具 JSON Schema
- SSE 支持 text、thinking、signature、input_json_delta、usage 和 stop reason
- 保留 `deepseek-messages` replay envelope 与 reasoning signature；跨模型切换丢弃旧签名
- HTTP 重定向不跟随

### 文件工具与审批

- 实现官方 `read`、`glob`、`write`、`edit` 的名称、schema、提示词和主要模型输出。
- `read` 支持 2000 行上限、2000 字符单行上限、50KiB 字节上限、行号及输出信封。
- `glob` 支持 100 条上限、隐藏文件、VCS 排除和修改时间排序。
- `write` 返回 `Created file / Updated file` 信封；`edit` 支持唯一匹配和 `replace_all`。
- 覆盖或编辑前要求同 session 的有效文件版本观察；外部修改会使观察失效。
- `workspace-write` 限制绝对路径与符号链接；`danger-full-access` 不施加工作区路径限制。
- 默认 `read-only`；`ask` 无应答者返回 `unavailable`，`never` 返回 `rejected`。
- `approval/asked`、`approval/decided` 成对记录 request id、toolName、callId 和 outcome。
- `--yes` 是学习版的进程级显式授权，会允许本次进程内所有写/Shell 操作，应谨慎使用。

### Shell 与后台任务

- `bash` 使用新 `bash -c` 进程，不保留 cwd、变量或函数；通过 `workdir` 指定目录。
- 返回 stdout、标记后的 stderr 和 `[exit code: N]`；非零退出是命令结果，不是基础设施异常。
- 每次 Bash 调用都注册为 job；`run_in_background` 立即返回 `bash-<id>`。
- 前台等待超时后保留同一 job 并返回 `[still running after ...; moved to background job ...]`。
- `job_output` 按 stdout/stderr 独立游标消费新增输出，支持有界等待和状态行。
- `job_list` 列出当前 session 的任务；`job_kill` 终止进程组并记录原因。
- 每 session 最多 10 个 running/stopping 任务；其他 session 不能读取或终止该任务。
- 输出内存环为 256KiB，首次终态读取后收缩到 16KiB；完整 stdout/stderr 分别落盘并报告路径。
- 应用关闭时终止并回收存活任务。
- 会话记录 `job/registered` 和 `job/settled`。
- Shell 环境过滤 `KEY/TOKEN/SECRET/PASSWORD` 形态变量和原 `DSH_*`，再注入受管 `DSH_HOME`、`DSH_SESSION_ID`。

## 尚未对齐

- 官方完整工具集、profile/bundle 格式、运行时热重载和外部 npm 插件。
- Landlock/bwrap 等内核沙箱；当前 `read-only` 保守拒绝全部 Shell，而不是允许只读 Shell 并以内核机制阻止写入。
- `sandbox_permissions` 升级、会话中切换审批/文件策略和审批取消。
- PTY、持久 Shell、Windows 原生 Shell；当前进程后端固定使用 `/bin/bash`。
- 任务完成通知、Agent 自动唤醒、跨进程任务恢复和长期常驻宿主。
- 图片与 Files API、模型 listing、请求重试、扩展请求头和遥测。
- 流式 UI 增量事件、会话 resume/fork、消息投影、压缩和跨进程锁。
- `read` 尚未实现大文件流式扫描；`glob` 尚未完全复刻 ripgrep 语义和 spill 行为。
- 匿名用户 ID 只在进程内稳定，尚未持久化。
- 尚未对真实 DeepSeek 服务做带凭据的在线验收。

## 已执行验证

- Mock Agent 的 `glob/read` 回归、功能开关和提示词组合。
- 本地 Messages/SSE 两轮请求、reasoning signature 回放、工具结果回填和 JSONL 持久化。
- 交错 SSE 内容块保持顺序、跨模型签名丢弃、HTTP 302 拒绝跟随。
- 文件策略的允许、拒绝、无应答者失败关闭和审批审计。
- 未读覆盖、读取后外部修改、多次匹配、`replace_all`、路径穿越和符号链接检查。
- Mock CLI 的写入及先读后编辑流程。
- Shell 前台 stdout/stderr/exit code、fresh shell、workdir 和非零退出。
- 后台启动、任务列表、独立增量输出、有界等待、前台超时转后台、进程组 kill。
- 大输出内存裁剪和完整输出落盘；环境凭据清理；job 会话事件。
- 每 session 10 个任务上限、应用退出回收、read-only Shell 拒绝。
- Shell 接入后的 write/edit 回归。

后续修改如果改变上述已对齐行为，必须在 Git 变更中同时修改本文件并说明原因；升级上游 commit 时必须重新核对，不得静默替换提示词或协议。
