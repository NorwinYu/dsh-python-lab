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
- 会话与压缩：`packages/core/session`、`packages/session/session-persistence-jsonl`、`packages/compaction/compaction-basic`
- 产品组装：`apps/cli`、`apps/web`、`apps/desktop`；只借鉴分层边界，不声称协议或包格式兼容

一致性声明只覆盖下文列出的切片，不代表完整 DSH 已移植完成。

## 当前已对齐

### 系统提示词与消息

- 固定身份段：`You are an AI agent powered by DeepSeek Harness.`
- `bash/read/write/edit/glob/jobs` 分别排在 order 1000/1100/1200/1300/1400/1600，使用上游英文原文。
- `write` 是否附带 `and prefer edit for targeted changes` 随 `edit` 开关变化。
- 禁用工具会同时移除工具 schema 和对应提示词段；缺少 `jobs` 时 Shell/jobs 插件保持 pending。
- runtime context 依次包含工作目录、文件策略和审批策略，并作为独立用户快照放在用户指令之后。
- 助手消息包含有序的 `text / reasoning / tool-call` 内容块，工具结果通过 `toolCallId` 回填。

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

### 会话与压缩

- `resume` 从 JSONL 重建用户、助手和工具消息，递增 turn，写入 `session/resume`，并刷新当前提示词和 runtime context。
- `fs/observed` 随会话持久化，resume 时恢复文件版本观察。
- `fork` 可复制完整会话或指定事件序号前缀，生成独立 session id。
- 支持会话列表和 transcript 可读投影。
- 自动压力阈值为上下文容量的 80%，默认保留 16% 尾部消息。
- 使用上游 compaction-engine prompt 和 `<compacted-summary>` 标记，写入 `compaction/start/end`。
- 压缩只影响发给模型的投影，不删除原始事件；压缩后的会话可以继续 resume。
- token meter 当前是学习版 UTF-8/JSON 启发式估算，不是官方精确 tokenizer。

### HTTP、SSE 与并发

- 默认仅绑定 `127.0.0.1`；绑定非回环地址必须配置 `MINIDSH_TOKEN`，否则拒绝启动。
- 提供健康、插件、工具、提示词、会话列表/事件/transcript、run/resume/fork/compact、jobs/output/kill API。
- SSE 包含 turn、assistant、`llm/retry`、工具执行、run result/error/cancelled 和 `job/settled`；内部 run/done 用于关闭当前流。
- 每次运行都有唯一 `run_id`，捕获 session id、等待结果和流事件都按 `run_id` 匹配，其他运行结束不会提前关闭当前 SSE。
- 相同 prompt 的并发运行也能获得独立 session id；JSONL 追加和读取使用锁避免序号冲突与半行读取。
- 提供一个使用 EventSource 的 Web 工作台，复用同一 Application，支持停止运行，不复制 Agent 逻辑。

### 模型列表、重试与取消

- `GET /models` 使用 Messages base URL 的 origin，不继承路径；认证请求不跟随重定向，默认超时 5000ms。
- 成功列表默认缓存 600000ms，`DEEPSEEK_MODELS_MAX_AGE_MS=0` 时每次刷新；返回深拷贝避免调用方修改缓存。
- 列表保留有效 id 的顺序，跳过空 id 和重复 id，识别 text/image、上下文容量和 Messages 能力；尚未实现官方 badge 和客户端版本要求。
- Messages 默认 normal 模式最多重试 5 次，覆盖 `EMPTY_RESPONSE/RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT`；初始退避 500ms、最大 10000ms、抖动 0.1。
- 支持有界 `Retry-After` 秒数或 HTTP 日期、认证/配额/请求/限流/服务端错误分类、`llm/retry` 事件，以及显式 always 模式。
- `llm.retry` 和 `llm.model-listing` 可分别关闭；列表请求本身不自动重试。
- `core.cancellation` 控制 HTTP/Web 取消；令牌传播到模型流、退避等待、压缩、工具和前台 Shell，取消产生 ABORTED 和 `run/cancelled`。
- 已建立响应流通过关闭 response 取消；前台 Bash 终止对应进程组，取消 `job_output` 等待不会终止后台任务。
- 网络连接建立前仅能在边界检查取消，尚不是官方 AbortSignal 全生命周期兼容实现。

### 外部插件与产品组装（学习版等价分层）

- 使用 Python 标准 `minidsh.plugins` entry points 发现独立发行包；外部功能默认禁用，只有显式启用才导入插件代码。
- 外部插件复用 `Plugin`、服务依赖、工具注册表和功能开关，不能覆盖内置插件名称。
- Headless、Web、Desktop 是独立 Python 发行入口，复用同一 Application；核心和 Web 不依赖 GUI 包。
- Desktop 的 `pywebview` 通过可选依赖安装，仅在 Desktop 启动路径延迟导入。
- 这些是学习版设计，不对齐官方 Cordis profile/bundle、npm 插件加载器、Electron 壳、安装器或自动更新协议。

## 尚未对齐

- 官方完整工具集、profile/bundle 格式、运行时热重载和外部 npm 插件。
- 官方 Web Host 的认证、鉴权、页面、RPC 与多路复用协议；当前 HTTP/SSE 是学习 API，不是官方兼容实现。
- Landlock/bwrap 等内核沙箱；当前 `read-only` 保守拒绝全部 Shell。
- `sandbox_permissions` 升级、会话中切换审批/文件策略和审批取消。
- PTY、持久 Shell、Windows 原生 Shell；当前进程后端固定使用 `/bin/bash`。
- 任务完成后的 Agent 自动唤醒、跨进程任务恢复和长期常驻宿主。
- 图片与 Files API、扩展请求头和遥测；完整模型目录元数据、后台 listing 刷新及官方通用 LLM 重试插件仍未移植。
- 官方 surface generation/replacement、会话格式迁移、崩溃修复、跨进程锁和原子多进程发布。
- 官方压缩区间选择、token 校准、tool-result pruner、模型切换压缩、重试和取消。
- `read` 尚未实现大文件流式扫描；`glob` 尚未完全复刻 ripgrep 语义和 spill 行为。
- 匿名用户 ID 只在进程内稳定，尚未持久化。
- 尚未对真实 DeepSeek 服务做带凭据的在线验收；当前 Web 页面不支持自定义 Authorization 请求头，token 主要用于 API 客户端。

## 已执行验证

- Mock Agent 的 `glob/read` 回归、功能开关和提示词组合。
- 本地 Messages/SSE 两轮请求、reasoning signature 回放、工具结果回填和 JSONL 持久化。
- 交错 SSE 内容块保持顺序、跨模型签名丢弃、HTTP 302 拒绝跟随。
- 文件策略的允许、拒绝、无应答者失败关闭和审批审计。
- 未读覆盖、读取后外部修改、多次匹配、`replace_all`、路径穿越和符号链接检查。
- Mock CLI 的写入及先读后编辑流程。
- Shell 前台/后台、超时转后台、增量读取、kill、并发上限和应用退出回收。
- 大输出裁剪和落盘、环境凭据清理、任务会话事件。
- 跨进程 resume、turn 递增、观察恢复、transcript、fork 及分支独立性。
- 手动压缩保留原始事件并支持 resume；自动压力压缩、token meter 和功能开关。
- 无效 context window 被拒绝；Shell 接入后的 write/edit 回归。
- HTTP 健康、HTML、工具、run/resume/fork/compact 和会话 API。
- SSE turn、assistant、run result 和 job 完成通知。
- Bearer token 接受/拒绝；相同 prompt 的 10 个并发请求生成独立 session 且 JSONL 完整。
- Python 3.9 editable 安装中同一发行包元数据重复时去重，不同发行包或导出发生同名冲突时拒绝。
- 外部时钟插件默认 disabled 且不导入模块；配置文件和 CLI 启用后 active，工具真实执行并进入提示词。
- Headless 实际运行 Mock 读取；Web 实际启动后健康、工具和外部插件元数据 API 正常，并完成浏览器端 SSE 运行。
- 核心、Headless、Web、Desktop、外部插件均完成 wheel 构建；离线 wheel 安装的轻量包不含 pywebview，Web 静态资源完整。
- Desktop wheel 安装 pywebview 后真实创建 macOS 窗口、加载新版页面、请求健康 API 并正常关闭；未验证官方 Electron 打包、签名或安装器。
- 本地 Messages 服务模拟 429→500→成功，产生 RATE_LIMIT/SERVER 两次重试且成功；401 不重试，禁用重试后 429 立即失败。
- `/models` 同源路由、认证、默认缓存、零缓存刷新、无效能力条目保留和返回值深拷贝隔离；列表超时归类 TIMEOUT。
- 慢速 SSE 响应在取消后关闭 socket 并返回 ABORTED；重试等待也能取消且不会发起下一次请求。
- 完整 HTTP 运行取消使 Bash 进程组 killed、JSONL 记录 aborted；并发 SSE 在其他运行完成后仍保持当前流。
- 浏览器实际点击“停止运行”收到 `run/cancelled`；新开关和原有 Mock 文件读取回归通过。
- 最终 wheel 包含取消、重试和 Web 资源，离线安装后实际执行 Mock `models` 成功。

后续修改如果改变上述已对齐行为，必须在 Git 变更中同时修改本文件并说明原因；升级上游 commit 时必须重新核对，不得静默替换提示词或协议。
