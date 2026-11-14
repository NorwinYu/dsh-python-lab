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

### 会话版本、迁移与崩溃恢复（学习版 v0/v1/v2）

- 新会话包含 `minidsh-session-header` v2 和完整事件快照；既有无头文件按 v0、单文件头按 v1 只读兼容。
- 每代位于 `<session-id>/generations/NNNNNNNN.jsonl`，禁止覆盖；`index.json` manifest 记录当前代、路径和 SHA-256。
- 追加、迁移、恢复和 fork 都发布新代，旧代只读保留；manifest 通过临时文件、fsync 和 os.replace 原子切换。
- 普通读取不迁移；首次追加、显式 migrate 或 resume 修复时才写当前格式，未来版本直接拒绝。
- 每个事件校验 session 身份、连续 seq、类型、payload 和 created_at；无效 JSON/UTF-8 失败关闭。
- 普通 load 拒绝不完整末尾；repair/resume 保留最后换行前的完整前缀，无法恢复任何记录时拒绝。
- 迁移或恢复前把原始字节保存为内容哈希命名的备份；读取 manifest 时验证代际哈希，外部篡改会失败关闭。
- 未开始工具调用使用上游 TOOL_NOT_STARTED 文案；已开始无结果调用使用 TOOL_OUTCOME_UNKNOWN 和保守重试警告。
- 恢复先补 error tool result，再关闭开放 step/turn，原因是 interrupted；fork 使用 forked 文案且不改父会话。
- 审计事件为 `session/migrated` 和 `session/repaired`；CLI 支持 repair、单个/全部 migrate，HTTP 支持单会话 repair/migrate。
- resume/compact 使用 POSIX flock 覆盖整个写回合，锁文件保留稳定 inode，进程死亡由内核释放；读取不加写锁。
- HTTP 拒绝同 session 并发回合；watcher 可在同一 Application 的回合内通过存储串行锁追加 job 审计，条件唤醒位于 finally。
- 该格式不是官方多代际或 Zstandard 文件格式；surface 仅为下述学习版切片，Windows 写锁尚未实现。

### Surface replacement 与工具结果裁剪

- 旧消息事件无 surfaceOp 时按 append 兼容；当前消息事件支持 append 和 `{op: replace, startSeq, endSeq}`。
- replacement 起止必须更早且仍在当前 surface，区间顺序有效；sourceEventSeqs 必须无重复并覆盖全部被遮蔽节点。
- 替换节点占据原区间位置而非追加到模型视图尾部；原始 JSONL 事件不改写。
- tool/result replacement 只能遮蔽一个当前工具结果且只能修改 content；首个系统提示词只能由单个 system replacement 改写。
- `derive_messages`、resume、fork 和 compact 使用当前 surface；transcript 排除 replacement 副本，保留原始追加内容。
- pruner 默认关闭，手动执行；阈值 8192、头 4096、尾 1024，使用上游固定 PRUNE_MARKER。
- 按 Unicode 码点裁剪文本并保留非文本块顺序；结果必须更小且不超过阈值。
- 每个裁剪先追加 `compaction/prune` 计量事件，再追加带来源引用的工具结果 replacement；重复执行不重复裁剪。
- 提供 CLI surface/prune、HTTP surface/prune 和 Web 裁剪按钮；配置使用 MINIDSH_PRUNE_* 环境变量。
- 尚未实现官方任意插件消息投影、完整事件类型系统和精确 token meter。

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
- 仅支持 Unix PTY；尚未实现官方终端行缓冲、OSC 就绪检测、ConPTY 和跨进程恢复；已支持持久终端打开时的 sandbox mode fence。

### 内核沙箱与策略升级

- `core.kernel-sandbox` 默认关闭；启用后 macOS Seatbelt 接管一次性 Bash、后台 jobs 和持久 PTY，默认行为不变。
- read-only 允许执行/读取但禁止写入；workspace-write 仅允许工作区、受管 jobs 和平台临时目录；danger-full-access 不启用隔离。
- 通过实际子进程验证 Operation not permitted，不是 ToolRegistry 模拟错误；`/tmp` 映射和 `subpath` 规则在本机验证。
- `sandbox/mode` 持久化会话覆盖，工具、运行时上下文和进程后端读取同一策略。
- `approval/policy` 切换 ask/never，runtime context 同步更新；审批取消记录 cancelled 并丢弃迟到回答。
- 持久终端打开时禁止 mode 切换，关闭后新终端按新模式创建。
- 提供 GET sandbox、POST sandbox、POST approval-policy API；尚无 Landlock/bwrap 或 Windows 对应实现。

### 增量模型流与 SSE 背压

- `core.streaming` 默认启用；DeepSeek 在原解析器中同步发出 text/reasoning/tool-call delta、工具 block-start 和 usage。
- Agent 添加 session/run/turn/step；每次请求带 attempt，重试后消费者可区分失败草稿与新流。
- 最终 ModelResponse、assistant/message 和 reasoning replay 与增量来自同一解析器，不改变已有持久消息结构。
- 无增量能力的模型由 Agent 将最终文本补发一次，标记 fallback=true；关闭 core.streaming 后完全不发送 delta。
- max-token 响应丢弃工具调用及对应 replay 块，避免执行不完整工具操作。
- Web 只拼接 text-delta，llm/retry 清空失败 attempt 草稿，原始事件区保留完整事件。
- 每个 SSE listener 队列上限 256，put_nowait 溢出时关闭并移除慢流，Agent 继续运行并持久化最终结果。
- 原始 chunk 的学习版压缩持久化和同进程续传见下节；不包含官方完整持久化协议、独立组装器 API 或跨重启 SSE 恢复。

### 原始流持久化与 SSE 续传

- `core.stream-recording` 默认启用；assistant/message payload 可携带压缩 stream 和 finish/replayState。
- 相邻 text/reasoning 按类型/index 打包为文本数组、time0 和 dt；工具参数按 index/id/name 打包为 args，保留原始分片边界。
- usage、block-start、finish 保留 raw chunk；Mock fallback 使用 index=0，最终消息和原始流来自同一响应。
- `stream_codec.expand_stream` 校验记录形状、成员数组、dt 长度和时间；会话读取会展开验证 stream。
- CLI `stream` 和 HTTP GET `/stream` 展开分片并附 seq/turn/step；fork 保留记录，关闭开关后不再写 stream。
- Broker 分配进程内递增事件 id，保留最近 2048 条；Last-Event-ID 只回放该 session 的更新事件并接入实时队列。
- 回放在同一锁内衔接历史与订阅，run/done 可回放，不启动重复 Agent；太旧、未来或未知断点明确失败。
- `core.sse-replay` 可关闭；历史环不跨进程重启，持久 chunk 没有全局 SSE 序号。

### 提问、待办与 Web 工具（学习版纵向切片）

- `tools.ask-user` 注册官方名称 ask_user_question 和 questions/id/question/options/multi_select schema；提示语包含上游跳过语义。
- 支持 CLI 逐题回答、HTTP 待答列表/回答、自定义文本、选项校验、取消和服务关闭；question/asked/answered 持久化。
- `tools.todo` 注册 todo_write，整表替换，校验唯一非空内容、pending/in_progress/completed 和活跃数量。
- 待办事件为 todo/write；最近写入生效，turn/start 清空，turn/end 保留；MINIDSH_TODO_ALLOW_PARALLEL 控制并行活跃任务。
- `tools.web-fetch`/`tools.web-search` 注册官方名称，使用上游外部不可信提示词段；默认关闭。
- Web fetch 支持有界读取、30 秒默认超时、200000 字符输出、重定向复核、私网拒绝、HTML 安全降级和取消。
- Web search 使用 MINIDSH_WEB_SEARCH_URL 的学习版 provider；支持查询去重、10 查询/10 来源、800 字符摘要和 Markdown 引用。
- `tools.ask-user-timed` 支持官方 timeout 参数、pending/callId/message、默认 120 秒等待和迟到回答；超时问题保留在待答队列。
- 迟到回答投影为 source.kind=answer_to_pending_question 的 user/message，附 callId 和原问题；Web 图形表单支持选项、多选、自定义和跳过。
- 当前没有完整官方 Client 卡片系统、Turndown/GFM、DeepSeek 搜索路由或 DNS 连接固定。

### 本地 Subagent 与协作控制（学习版纵向切片）

- `core.subagents` 默认关闭；启用后提供 spawn/fork 两种本地后端、持久子 Session、父子树、名称和深度限制。
- `subagent` 启动无父历史的全新子级；`subagent_fork` 对应上游可配置工具名，复制最后一个完整 turn/end 之前的历史，不包含当前开放回合。
- 工具立即返回 agent-N 或指定名称；子级完成通过 subagent-settled 用户消息进入父级后续步骤，不泄露子级中间上下文。
- `send_message` 可向运行中子级排队输入或唤醒空闲子级；`interrupt_agent` 校验祖先后代关系并传播取消；`list_agents` 列出根和后代；`wait_agent` 支持官方 10000–3600000ms 边界。
- 记录父级 subagent/catalog 和进程内 subagent/start/end，提供会话 subagents 查询、subagent-message 和 subagent-interrupt 学习 API。
- MINIDSH_SUBAGENT_MAX_DEPTH 默认 1，0 禁止委派；Application 关闭按深度回收子级。
- 当前没有外部 ACP/Codex/Claude Code/DSH SDK 后端、模型/preset 选择、结构化输出、跨重启在线目录恢复；子 cwd 已校验持久化，但文件工具仍使用根工作区。父级完成通知在学习版 Agent Loop 后续 step 消费，不是官方完整 inbox/独立唤醒调度器。

### Workflow 脚本编排、后台 Jobs 与恢复（学习版纵向切片）

- `core.workflow` 和 `tools.workflow` 默认关闭；复用本地 subagent 树，需要 Node.js 可执行文件，不增加 Python 运行时依赖。
- Node 子进程用 vm 执行普通 JavaScript async body，脚本无 require/process/fs/net/timer；meta/args 作为 JSON 数据注入，不对元数据求值。
- 支持 agent、pipeline、parallel、phase、log 和顶层 await；每个 agent 使用 delivery=caller，结果返回脚本，不额外唤醒父级。
- 支持对象根 JSON Schema 子集、普通子级失败/结构不匹配映射 null、fatal hook 错误中止整次运行、非有限数/循环/稀疏数组拒绝。
- MINIDSH_WORKFLOW_MAX_AGENTS 默认 1000，MINIDSH_WORKFLOW_MAX_CONCURRENT 默认按 CPU 且最多 32；单项组合器最多 4096 条。
- 提供 workflow/start/phase/log/agent-start/agent-end/end 和父 Session 的 tool-workflow 四类记录；取消停止进程并等待子级终态后配对成员结束。
- run_in_background 使用独立 workflow job id；managed-job 复用 job_output/job_list/job_kill、输出环、并发上限和会话归属。
- phase/log/member 生命周期进入增量输出，完成结果只投递一次；kill 同时取消 Node 和子级，job/registered/settled 持久化。
- Application 重启从 Session 与 jobs 输出文件恢复已完成结果；未结算任务标记 interrupted: host restarted，补记唯一 cause=recovery 审计，不恢复旧进程。
- HTTP jobs list/output/kill 支持可选 session_id 归属参数；原有无参数行为不变。
- 当前不支持任意模型路由，vm 是学习版能力隔离而非官方完整 PTC/OS 沙箱；没有跨进程重连正在运行的 Node。

### MCP 客户端、资源与管理器（学习版纵向切片）

- `core.mcp` 默认关闭；stdio 和 streamable-http 使用真实 JSON-RPC initialize/initialized，依次协商 2026-07-28 至 2024-11-05。
- 工具以 mcp__server__raw 注册，保留原始调用名；长/非法名称使用稳定 SHA-256 后缀。分页最多 16 页，远程重名和注册冲突拒绝整代，通知刷新串行。
- stdio 清洗 KEY/TOKEN/SECRET/PASSWORD/DSH_* 环境并合并显式 env；HTTP 支持 JSON/SSE 响应和 MCP-Session-Id。
- `${credential:REF}` 从宿主环境解析，失败诊断将解析值及 URL 编码替换为 [redacted]；成功结果保持原文。
- 工具调用支持超时、取消和 notifications/cancelled；MCP isError 失败关闭，文本/结构化/不支持内容块有明确投影。
- 共享 list_mcp_resources/list_mcp_resource_templates/read_mcp_resource 要求显式 server；支持游标、无 resources 能力空列表和 blob 长度说明。
- 服务器指令有 32KiB 默认上限；管理器支持 upsert/remove/enable/disable/restart/probe/list、状态事件和 HTTP API，最后一个服务器移除时撤回共享资源工具。
- 当前没有自动重连预算、OAuth、完整凭据提供方轮换、资源订阅、MCP prompts、官方 SDK 输出 schema 校验或图片附件持久投影；HTTP 取消等待传输超时，VM/进程隔离不适用于远端服务器。

### Skills 注册表、文件发现与调用（学习版纵向切片）

- `core.skills` 与 `tools.skill` 默认关闭；支持运行时注册、提供方注册和项目/custom/user/bundled 根按 rank 合并，同名高优先级获胜。
- 文件格式支持根下 `<name>/SKILL.md` 和 `<name>.md`，解析 name/description、disable-model-invocation、user-invocable 和严格布尔词；无效条目跳过。
- 每次发现重新扫描，frontmatter/成员变化在下一步更新；每次加载重读正文，正文修改不改变目录 digest。
- 持久 skill-catalog 用户消息按 entries+完整性 digest 去重，支持初始、替换、空目录、发现不完整及恢复；source 保留结构化 entries。
- `skill` 工具按精确名称加载，disable-model-invocation 从目录和模型加载排除；`/name` 只扫描直接用户 prompt，支持重复去重和 user-invocable=false 拒绝。
- 统一 `<skill_content>/<skill_resources>/<skill_instructions>` 渲染，提供 directory/url/opaque/provider 资源指引；目录描述规范化并默认最多 500 字符。
- 提供 CLI skills/skill 和 HTTP 列表/加载；无 skill 或无工具时不发布初始空目录。
- 当前 frontmatter 是学习版标量解析，不支持完整 YAML/Office 内置包/Chokidar/文件系统服务或官方工作目录作用域链。

### Schedule 宿主定时任务与投递（学习版纵向切片）

- `core.schedule` 和 `tools.schedule` 默认关闭；支持 after/at/every/daily/weekly/cron/rrule 七种时间记录、UTC 毫秒时刻和本地时区。
- Cron 支持五字段列表/范围/步长及 Vixie 日期 OR 语义；RRULE 支持 MINUTELY/HOURLY/DAILY/WEEKLY/MONTHLY/YEARLY、INTERVAL/COUNT/UNTIL、BYDAY/BYMONTHDAY/BYMONTH/BYHOUR/BYMINUTE/BYSECOND/BYSETPOS。
- 当前 RRULE 不支持 BYWEEKNO/BYYEARDAY，BYHOUR/BYMINUTE/BYSECOND 各只接受一个值，DST 使用 zoneinfo 跳过不存在墙钟并选较早 fold。
- `.minidsh/schedule.json` 使用 schedule v2 原子整单元持久化，打开校验版本、任务 id 和 scheduledAt；损坏输入不发布新文件。
- schedule_create/list/view/edit 支持目标、活动筛选、分页、暂停恢复、expectedUpdatedAt 冲突和删除；run 不移动原发生时点，history 支持游标，deleteInactive 只清理 ended。
- this_session 复用现有会话，new_once 预留并复用新会话，new 每次创建；委派调用和委派目标拒绝。
- 定时器只追赶最近一次，运行中目标在下一边界注入；schedule/inbox 在消息持久化时确认回执，不代表模型完成。历史最多 200 条/30 天，60 秒单任务投递间隔。
- HTTP 提供 create/list/view/history/edit/run/deleteInactive 和 schedule/changed；管理读取不激活目标 Session。
- 当前没有完整 RFC 5545 求值器、关联 Agent 变更通知、归档准入、批量 Session 提醒、执行槽或独立存储后端。

### 外部插件与产品组装（学习版等价分层）

- 使用 Python 标准 `minidsh.plugins` entry points 发现独立发行包；外部功能默认禁用，只有显式启用才导入插件代码。
- 外部插件复用 `Plugin`、服务依赖、工具注册表和功能开关，不能覆盖内置插件名称。
- Headless、Web、Desktop 是独立 Python 发行入口，复用同一 Application；核心和 Web 不依赖 GUI 包。
- Desktop 的 `pywebview` 通过可选依赖安装，仅在 Desktop 启动路径延迟导入。
- 这些是学习版设计，不对齐官方 Cordis profile/bundle、npm 插件加载器、Electron 壳、安装器或自动更新协议。

## 尚未对齐

- 官方完整工具集、profile/bundle 格式、运行时热重载和外部 npm 插件。
- 官方 Web Host 的认证、鉴权、页面、RPC 与多路复用协议；当前 HTTP/SSE 是学习 API，不是官方兼容实现。
- Linux Landlock/bwrap 和 Windows 隔离；当前仅 macOS Seatbelt，未启用时 `read-only` 保守拒绝 Shell。
- 官方 `sandbox_permissions` 升级协议和审批取消通知的完整 Client 集成；当前仅支持会话 API 与审批取消结果。
- 官方完整终端行缓冲、OSC 就绪协议、PTY 跨进程恢复和 Windows ConPTY；当前持久后端仅支持 Unix `/bin/bash`。
- 任务完成后的 Agent 自动唤醒、跨进程任务恢复和长期常驻宿主。
- 完整附件存储、图片变换/定价、持久上传索引和配额恢复、扩展请求头及遥测；完整模型目录元数据、后台 listing 刷新及官方通用 LLM 重试插件仍未移植。
- 官方完整事件类型、任意插件消息投影、格式目录、Zstandard 和 Windows 写锁；学习版仅自定义 v0/v1/v2 JSONL 代际和 surface 切片。
- 官方压缩区间选择、token 校准、模型切换压缩、重试和取消；pruner 使用学习版计量事件。
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
- 真实超长 Shell 工具结果从 9011 码点裁剪到 5159；原事件不变，模型投影含 PRUNE_MARKER 且保留头尾。
- surface replacement 遮蔽原节点并保留替换位置；resume/fork 继承裁剪视图，重复 prune 为空。
- transcript 保留原始工具全文；HTTP prune/surface 和 CLI surface/prune 均通过。
- 修改工具调用身份和非消息事件的 replacement 在写入前被拒绝；Unicode 与图片块顺序测试通过。
- 最终 wheel 包含 surface/pruner 和 Web 裁剪资源；离线安装后真实 Shell 长输出经 CLI prune 成功，surface 可读取 replacement。
- 提问回答器、HTTP 待答列表/回答、运行取消和队列清理通过；question/answered 持久化。
- todo_write 整表投影、turn/start 清空、单活跃拒绝、并行策略和 CLI todos 查询通过。
- 本地 Web 重定向、HTML 脚本/隐藏内容移除、外链提示、搜索摘要 800 字上限和提示词段通过。
- Web 深度 512 防护、输出上限、运行取消、私网/file scheme 拒绝和未配置 provider 错误均通过；未访问公网。
- 四个新工具默认 disabled，不改变默认系统提示词。
- 最终 wheel 包含 questions/todo/web_tools；离线安装后提问、待办、Web fetch/search 四条本地真实流程全部通过。
- 本地 DeepSeek SSE 验证 text/reasoning 分片、usage、工具参数和最终响应一致；500 后重试事件的 attempt=2。
- max-token 响应的工具调用和 replay 块被丢弃；真实 Agent 工具调用的 delta 带正确 run_id。
- Mock fallback 和 core.streaming 禁用通过；300 条事件写入 256 队列后慢 listener 被移除，dropped_streams=1。
- 浏览器实际运行后实时文本区显示 Mock 正文，原始事件区仍保留完整事件。
- 最终 wheel 离线安装后，本地 DeepSeek 的真实 text delta 与 run_id 正确，慢 listener 溢出断开通过。
- text/reasoning/tool/usage/finish 编解码往返保持顺序、字段和时间，非法 dt 被拒绝。
- 真实 HTTP 断开后 Last-Event-ID 重连收到后续 assistant/result/done，不创建重复会话；未来 id 和历史截断被拒绝。
- 压缩流随 assistant 事件保存，CLI/HTTP 展开读取通过；修复 Mock fallback 缺少 index 的问题。
- core.stream-recording/core.sse-replay 禁用行为及既有背压回归通过。
- 最终 wheel 离线安装后，本地 DeepSeek 分片可压缩保存、展开恢复文本，broker 历史可回放 assistant/message 和 turn/end。
- 真实 50ms 超时后 pending 保留，HTTP 迟到回答投影为 answer_to_pending_question 并可 resume；取消后队列清空。
- 浏览器在超时后通过图形表单提交自定义回答，待答面板正常关闭。
- 最终 wheel 离线安装后，50ms 超时返回 pending，迟到回答写回原会话并可 resume，图形表单资源已打包。
- 新会话产生不可变代际和 manifest；resume 后首代字节不变，fork 当前代为 2，repair/migrate 正常会话返回 unchanged。
- v0/v1 迁移原文件不变，migrated 审计进入第 1 代，后续事件进入下一代，原始备份字节一致。
- 外部修改当前代际后 SHA-256 校验失败；v0 截断尾部迁移、interrupted 恢复和 resume 通过。
- 最终 wheel 包含 generations 模块；离线安装后新会话、resume 和 v1→v2 迁移通过，首代字节不变。
- Seatbelt 实测 read-only 写入被内核拒绝，workspace-write 成功且 /var/tmp 越界写入被拒绝，/tmp 子路径规则通过。
- sandbox/mode 与 approval/policy 切换、runtime context 更新、审批 cancelled 和迟到回答丢弃通过。
- 持久 PTY 打开时 mode 切换被拒绝，kill 后新模式下写入被内核拒绝；默认非内核行为不变。
- 最终 wheel 包含 seatbelt 后端；离线安装后工作区写入成功，越界写入和 read-only 写入均被内核拒绝。
- 真实 Mock 委派启动子级读取 README，父级收到 subagent-settled；spawn 不含父指令，fork 包含已完成父历史。
- 子级后续消息产生第二个 turn/end，最大深度拒绝、等待唤醒与非法 timeout、根级中断 aborted、HTTP 查询/消息和应用关闭回收通过。
- 修复预留子 session id 丢失、继续会话误新建和根级中断权限遗漏；默认 subagent 功能全部 disabled，不改变原提示词。
- 最终 wheel 包含 subagents 模块；离线安装后在独立工作区真实 spawn、读取文件和完成通知通过，父子两个 Session 均持久化。
- 真实 JavaScript parallel 启动两个子 Agent 并返回文本数组；pipeline 两阶段无全局屏障，结构化对象及 index 正确。
- process/require 不可见，未知 hook 选项、循环返回值和超过两个 Agent 的上限均失败关闭；普通 schema 结果可回到脚本。
- 主动取消报告 cancelled，子级静止后 agent-end outcome=cancelled，Application 关闭同样回收运行；成功和失败均无悬挂控制进程。
- 模型 workflow 工具前台结果、六类 SSE 生命周期和四类持久记录通过；修复跨 realm 对象、Node pending Promise 退出和控制管道竞态。
- 最终 wheel 包含 Python 引擎和 workflow_guest.js；离线安装后真实运行两个子 Agent 并持久化三个独立 Session。
- 后台发布生成独立 run/job id；增量 phase/log、最终结果单次投递、跨 owner 拒绝和 job 审计通过。
- job_kill 映射 killed/cancelled 并等待子级静止；模型后台工具、job_output 和 HTTP session_id 列表/输出通过。
- 重启后已完成结果可重读，未结算 managed job 标为 interrupted: host restarted；恢复审计 cause=recovery 且重复重启不重复补记。
- 一次性 Bash 非零退出码和前台 workflow 回归通过；最终 wheel 离线安装后前台、后台及完成结果重启恢复通过。
- 独立 stdio fixture 验证 initialize、分页发现、真实 echo、资源列表/读取、服务器指令和独立 probe；二进制只显示长度。
- Streamable HTTP 验证 JSON/SSE 响应、MCP-Session-Id、凭据模板及结构化结果；未使用真实凭据。
- 通知新增工具、远程重名保留旧代、恢复清除错误、RunCancelled 与 notifications/cancelled、管理启停/probe 和资源清理通过。
- 最终 wheel 离线安装后真实 stdio MCP 工具与资源调用通过，关闭后子进程退出。
- 真实 skill 文件验证初始目录、模型加载、隐藏模型的用户手势、描述更新、正文重读和空目录替换。
- 项目/custom/runtime 优先级、严格布尔、发现不完整与恢复、user-invocable=false 拒绝、HTTP 和 CLI 加载通过。
- 最终 wheel 离线安装后 skill 工具、/name 注入和目录替换通过；默认配置不产生 skill 工具或目录。
- Cron 工作时刻、月末最后一个工作日 BYSETPOS=-1、DAILY COUNT=2 耗尽和 DTSTART 默认值通过；修复 aware/naive 比较与负索引。
- 创建/暂停/恢复/冲突、真实一秒定时回执、new_once 新会话、历史、pending 和 v2 存储通过。
- 重启只投递一次并推进未来时点，运行中目标队列注入、委派拒绝、HTTP 管理和默认不可见通过。
- 最终 wheel 离线安装后真实定时投递、回执和重启追赶通过。

后续修改如果改变上述已对齐行为，必须在 Git 变更中同时修改本文件并说明原因；升级上游 commit 时必须重新核对，不得静默替换提示词或协议。
