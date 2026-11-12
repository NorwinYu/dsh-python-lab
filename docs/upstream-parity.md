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

### 会话版本、迁移与崩溃恢复（学习版 v0/v1）

- 新会话包含 `minidsh-session-header` v1；版本头不占事件 seq，既有无头文件按 v0 只读兼容。
- 普通读取不迁移；首次追加、显式 migrate 或 resume 修复时才写当前格式，未来版本直接拒绝。
- 每个事件校验 session 身份、连续 seq、类型、payload 和 created_at；无效 JSON/UTF-8 失败关闭。
- 普通 load 拒绝不完整末尾；repair/resume 保留最后换行前的完整前缀，无法恢复任何记录时拒绝。
- 重写前把原始字节保存为内容哈希命名的备份，再通过临时文件、文件/目录 fsync 和 os.replace 发布。
- 未开始工具调用使用上游 TOOL_NOT_STARTED 文案；已开始无结果调用使用 TOOL_OUTCOME_UNKNOWN 和保守重试警告。
- 恢复先补 error tool result，再关闭开放 step/turn，原因是 interrupted；fork 使用 forked 文案且不改父会话。
- 审计事件为 `session/migrated` 和 `session/repaired`；CLI 支持 repair、单个/全部 migrate，HTTP 支持单会话 repair/migrate。
- resume/compact 使用 POSIX flock 覆盖整个写回合，锁文件保留稳定 inode，进程死亡由内核释放；读取不加写锁。
- HTTP 拒绝同 session 并发回合；watcher 可在同一 Application 的回合内通过存储串行锁追加 job 审计，条件唤醒位于 finally。
- 该格式不是官方多代际、surface replacement 或 Zstandard 文件格式；Windows 写锁尚未实现。

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

### 图片附件与 Files API

- 支持 PNG/JPEG/GIF/WebP 文件头识别，单张 32MiB、每请求 600 张和原始累计 128MiB；内联 base64 累计上限 20MiB。
- 内部附件记录路径、media type、大小和 SHA-256，不把 base64 写入 JSONL；DeepSeek 准备时重新读取并拒绝变更。
- 图片仅允许 user/tool 内容，普通文件策略限制在工作区内，`danger-full-access` 可读取外部路径。
- Messages 内联格式为 `source.type=base64`，文件引用为 `source.type=file`；后者携带 `files-api-2025-04-14`。
- Files 根目录复用 Messages `/v1` 规则；multipart 包含 `expires_after[anchor]=created_at`、秒数和文件，默认生命周期 604800 秒。
- Files 默认操作超时 60000ms；实现上传、列表、读取和删除的对象校验，拒绝重定向并保留错误分类。
- auto 默认优先上传，Files 失败或 Messages INVALID_REQUEST 时降级内联；required 失败关闭，inline 不上传。
- 文件缓存按路由、凭据哈希和图片 SHA-256 隔离，提前一小时视为需要刷新；并发上传有锁，Messages 重试复用文件 id。
- 被拒绝的文件引用会失效并在下次重新上传；提供 `llm/image-upload`、`llm/image-fallback` 和 `llm.files` 开关。
- 尚未实现官方图片变换、token 定价、持久上传索引、独立共享等待者、配额清理与附件存储协议。

### 持久 Bash 与 PTY（学习版纵向切片）

- `tools.bash-persistent` 默认关闭，启用时自动关闭一次性 `tools.bash`，模型工具名仍为 `bash`。
- 使用上游持久工具描述和仅含 `command` 的 schema；不保留一次性 Bash 的提示词段或后台参数。
- 每个 session 一个交互式 `/bin/bash` PTY，同 owner 串行执行；cwd、export、函数和 Shell 状态在进程内保留。
- 使用随机 start/end 标记和 eval 包装捕获退出码，结果以 `[Command finished with exit code N]` 收尾。
- 默认命令超时 300000ms，模型输出尾部最多 16000 字符；内部 scrollback 为 1MiB。
- 初始化关闭输入 echo 和历史扩展；环境沿用凭据清理，并注入受管 DSH/PTY 变量。
- 超时、取消、Shell 退出、HTTP kill 和应用关闭都会终止并重置终端，下一次从工作区新环境开始。
- 提供 `pty/created`、`pty/reset`、终端列表和 kill API；CLI 短进程退出后不能恢复 PTY 内存状态。
- 仅支持 Unix PTY；尚未实现官方终端行缓冲、OSC 就绪检测、ConPTY、跨进程恢复和 sandbox mode fence。

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
- 官方完整终端行缓冲、OSC 就绪协议、PTY 跨进程恢复和 Windows ConPTY；当前持久后端仅支持 Unix `/bin/bash`。
- 任务完成后的 Agent 自动唤醒、跨进程任务恢复和长期常驻宿主。
- 完整附件存储、图片变换/定价、持久上传索引和配额恢复、扩展请求头及遥测；完整模型目录元数据、后台 listing 刷新及官方通用 LLM 重试插件仍未移植。
- 官方 surface generation/replacement、格式目录、多代际不可变发布、Zstandard 和 Windows 写锁；学习版仅自定义 v0/v1 JSONL。
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
- 本地 Files/Messages 服务验证 multipart 字段、图片字节、beta header、文件引用和 base64 内联格式。
- 上传缓存复用、Messages 重试不重复上传、auto 上传失败/无效文件 id 降级、required 失败关闭均通过。
- 无效文件 id 缓存被清除，下一次重新上传；5 个并发请求只上传一次。
- Files 列表、读取、删除及模型列表命令在本地服务上通过；CLI/HTTP 图片会话不含 base64，支持 transcript 和 resume。
- 非图片内容、路径越界和附件哈希变化均被拒绝。
- 本阶段最终 wheel 包含图片、Files 和更新后的 Web 资源，离线安装及 Mock `models` 命令通过。
- 真实 Unix PTY 验证 cwd/export 跨调用保持、非零退出码、超时重置、exit 重置和应用回收。
- Agent/Mock 持久工具验证插件替换、schema、HTTP 终端列表/kill、运行取消和 JSONL 重置事件。
- 默认一次性 Bash 的 active 状态、工具描述和系统提示词保持不变。
- 最终 wheel 包含 PTY 和终端事件资源；离线安装后实际执行持久 Bash，输出及退出码 0 均正确。
- v0+截断尾部经 CLI repair 后保留原始字节备份、迁移 v1、补 TOOL_OUTCOME_UNKNOWN、关闭 step/turn 并可 resume。
- 未开始工具调用补 TOOL_NOT_STARTED；fork 开放回合补 forked 且父会话原始字节不变。
- 未来版本、seq 缺口和普通 load 截断均失败关闭；只有版本头加半行事件的恢复也通过。
- 两个真实进程验证 flock 互斥及释放后的后继追加；HTTP repair 和 CLI migrate 通过。
- resume 持锁时 job watcher 审计正常且条件等待及时唤醒；同 session HTTP 并发回合被拒绝，取消后释放。
- 最终 wheel 包含版本迁移和恢复模块；离线安装后实际 run、resume、repair 成功，正常会话 repair 返回 changed=false。

后续修改如果改变上述已对齐行为，必须在 Git 变更中同时修改本文件并说明原因；升级上游 commit 时必须重新核对，不得静默替换提示词或协议。
