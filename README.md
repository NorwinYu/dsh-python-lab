# MiniDSH：从 0 学 Agent Harness

这是一个用于拆解 DeepSeek Harness（DSH）核心思想的 Python 学习项目。它不是官方 DSH 的完整移植，而是通过可运行的小切片逐层理解插件、工具、模型、审批、Shell、后台任务、会话恢复、压缩、HTTP/SSE、外部插件、产品发行和 Agent Loop。

核心、Headless 和 Web 只使用 Python 标准库，支持 Python 3.9+；Desktop 的 `pywebview` 是独立可选依赖。MockModel 不需要 API Key；真实 DeepSeek 模型插件可独立开关。

## Git 与上游基线

项目使用 Git 分阶段提交，对照的上游 commit 为：

```text
fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159
```

已对齐和未对齐的边界记录在 `docs/upstream-parity.md`。工具提示词和 runtime-context 文案使用上游英文原文。

```bash
git log --oneline
git status
```

## 立即运行

```bash
PYTHONPATH=src python3 -m minidsh plugins
PYTHONPATH=src python3 -m minidsh tools
PYTHONPATH=src python3 -m minidsh prompt
PYTHONPATH=src python3 -m minidsh run "请列出当前目录" --trace
PYTHONPATH=src python3 -m minidsh run "读取 README.md"
```

也可以安装成命令：

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
minidsh run "请列出当前目录"
```

## 功能开关

- `core.system-prompt`：身份段、工具段和 runtime context。
- `core.sandbox-policy`：文件策略与工作区边界。
- `core.approval`：写/Shell 操作审批与失败关闭。
- `core.fs-observation`：同会话文件读取版本及恢复。
- `core.jobs`：进程、输出、任务隔离和生命周期。
- `core.compaction`：自动/手动压缩检查点。
- `core.cancellation`：HTTP/Web 运行取消及模型、工具、前台 Shell 取消传播。
- `core.pty`：Unix PTY 服务与持久终端生命周期。
- `core.tool-result-pruner`：手动 surface 工具结果裁剪，默认关闭。
- `core.user-questions`：用户提问、待答和取消服务。
- `core.web`：Web 获取与搜索 provider 服务。
- `core.tools`：工具注册表。
- `tools.glob`：官方 `glob` 文件发现工具。
- `tools.read`：官方 `read` 文本读取工具。
- `tools.write`：官方 `write` 创建/覆盖工具。
- `tools.edit`：官方 `edit` 字面替换工具。
- `tools.bash`：官方一次性 `bash -c` 进程工具，默认启用。
- `tools.bash-persistent`：官方持久 `bash` 工具切片，默认禁用；启用后替代一次性 Bash。
- `tools.jobs`：官方 `job_output/job_list/job_kill` 工具组。
- `tools.ask-user`：官方 `ask_user_question` 工具切片，默认关闭。
- `tools.todo`：官方 `todo_write` 工具切片，默认关闭。
- `tools.web-fetch`：官方 `web_fetch` 工具切片，默认关闭。
- `tools.web-search`：官方 `web_search` 工具切片，默认关闭。
- `session.jsonl`：带版本头的 JSONL、v0 迁移、崩溃恢复和 POSIX 写锁。
- `llm.mock`：确定性模拟模型，默认启用。
- `llm.deepseek`：真实 DeepSeek Messages 模型，默认禁用。
- `llm.retry`：DeepSeek 有界瞬时错误重试，默认启用。
- `llm.model-listing`：模型列表查询与缓存，默认启用。
- `llm.files`：图片文件上传、进程内复用和 Files API 列表，默认启用。
- `agent.core`：Agent Loop。

```bash
PYTHONPATH=src python3 -m minidsh --disable tools.glob tools
PYTHONPATH=src python3 -m minidsh --disable tools.edit prompt
PYTHONPATH=src python3 -m minidsh --disable core.jobs plugins
PYTHONPATH=src python3 -m minidsh --disable core.compaction plugins
```

禁用全部模型后，`agent.core` 因缺少 `llm` 服务而保持 `pending`。禁用 `session.jsonl` 后 Agent 仍可运行，但不能 resume/fork/compact，也不会保存会话、审批或任务事件。

完整配置示例见 `config.example.json`。配置文件先合并，命令行的 `--enable` 和 `--disable` 后生效。

## 文件策略与写操作

默认文件策略是 `read-only`，写操作和 Shell 都会被拒绝。若要允许工作区写入：

1. 设置 `--sandbox-mode workspace-write`。
2. 在交互提示中允许，或显式传入 `--yes`。

```bash
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode workspace-write \
  --yes \
  run "写入 demo.txt：hello"
```

Mock 编辑会先 `read`，再在同一会话中 `edit`：

```bash
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode workspace-write \
  --yes \
  run "编辑 demo.txt：把hello替换为world"
```

`--yes` 会允许本次进程内的所有写和 Shell 操作，不应在内容不可信时使用。交互终端省略 `--yes` 会逐次询问；非交互环境没有可用审批者时返回 `unavailable`。

```bash
# 自动拒绝需要审批的操作
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode workspace-write \
  --approval-policy never \
  run "写入 denied.txt：hello"

# 不限制工作区路径，也不触发写/Shell 审批
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode danger-full-access \
  run "写入 full.txt：hello"
```

`write` 覆盖已有文件前必须读过它；`edit` 默认要求旧文本恰好出现一次。读取后如果文件被外部修改，也会拒绝继续覆盖。

## Shell 与后台任务

```bash
# 前台命令
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode workspace-write \
  --yes \
  run "执行 printf 'hello'"

# 后台命令，立即返回 job id
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode workspace-write \
  --yes \
  run "执行后台 sleep 10"
```

每次调用使用新的 `bash -c`；cwd、变量和函数不会跨调用保留。前台命令超时后转为后台任务，而不是直接杀死。后台任务支持独立 stdout/stderr 游标、输出上限、完整输出落盘、任务列表、进程组终止和会话事件。

当前 CLI 是短进程，退出时会回收后台任务；长期运行和跨进程任务恢复使用下面的常驻宿主。

## 持久 Bash 与 PTY

启用持久 profile 后，模型仍看到名为 `bash` 的工具，但 schema 只要求 `command`，描述使用上游持久模式原文：

```bash
PYTHONPATH=src python3 -m minidsh \
  --enable tools.bash-persistent \
  --sandbox-mode workspace-write --yes \
  run "执行 export DEMO=hello; cd /tmp"
```

`tools.bash-persistent` 会自动关闭 `tools.bash`，避免同名注册冲突；`core.pty` 提供独立生命周期服务。持久 Shell 使用 Unix PTY 和交互式 `/bin/bash`，同一 session 的命令串行执行，cwd、export 的环境变量、函数和 Shell 状态会保留。每次命令通过随机标记捕获输出，结果以 `[Command finished with exit code N]` 收尾。

命令超时、运行取消、`exit` 或 HTTP 主动 kill 都会重置 Shell；下一次调用从工作区和全新环境开始。默认命令超时 300000ms，模型返回最多保留尾部 16000 字符，可用 `MINIDSH_PERSISTENT_TIMEOUT_MS` 和 `MINIDSH_PERSISTENT_MAX_OUTPUT_CHARS` 调整。PTY 会话事件为 `pty/created` 和 `pty/reset`。

```text
GET  /v1/terminals
POST /v1/terminals/<pty-id>/kill
```

持久模式不提供 `run_in_background`，后台任务仍使用一次性 Bash 的 jobs 管线。状态只在同一常驻 Application 内有效；CLI 每次命令退出都会回收 PTY，resume 只能恢复事件记录，不能恢复已退出进程的实际内存状态。当前仅支持 Unix PTY，不支持 Windows ConPTY 或内核沙箱。

## 会话恢复与压缩

```bash
PYTHONPATH=src python3 -m minidsh sessions
PYTHONPATH=src python3 -m minidsh resume <session-id> "继续刚才的任务"

# 可读消息投影
PYTHONPATH=src python3 -m minidsh transcript <session-id>

# 创建独立分支；可选 --at-seq 指定保留位置
PYTHONPATH=src python3 -m minidsh fork <session-id>

# 手动创建压缩检查点
PYTHONPATH=src python3 -m minidsh compact <session-id>

# 缩小学习用上下文容量，观察自动压力压缩
PYTHONPATH=src python3 -m minidsh --context-window 100 run "测试自动压缩"
```

resume 会恢复消息、turn 编号和文件观察；runtime context 使用当前配置重新生成。fork 不会改写原会话。压缩只改变发给模型的投影，不删除原始 JSONL 事件。

## 会话版本、迁移与崩溃恢复

新会话使用 MiniDSH v1 版本头，事件 seq 仍从 0 开始，版本头不占事件序号。旧的无头 JSONL 作为 v0 只读兼容；普通 `session/transcript` 不改文件，首次追加、显式迁移或 resume 恢复时才写入 v1。

```bash
# 备份并修复截断末尾、未关闭 step/turn 和缺失工具结果
PYTHONPATH=src python3 -m minidsh repair <session-id>

# 迁移一个或全部可读取会话
PYTHONPATH=src python3 -m minidsh migrate <session-id>
PYTHONPATH=src python3 -m minidsh migrate
```

- 不完整末尾只有在 `repair` 或 resume 恢复时才截到最后一个完整换行；普通 `load` 失败关闭。
- 未开始的工具调用补 `TOOL_NOT_STARTED`；已开始但无持久结果的调用补 `TOOL_OUTCOME_UNKNOWN`，并明确警告不要对可能有副作用的操作盲目重试。
- 随后依次关闭开放的 step 和 turn，原因分别为 `interrupted`；fork 在开放回合截断时为分支补 `forked` 闭环，父会话不变。
- 每次迁移或恢复先把原始字节保存到 `.minidsh/sessions/backups/<id>.<sha256>.jsonl`，再通过临时文件、fsync 和 `os.replace` 原子发布；备份不会因重复操作被覆盖。
- 高于当前版本的格式、身份不一致、非连续 seq、无效 UTF-8/JSON 都会拒绝读取。
- resume 和手动 compact 持有整个回合的 POSIX `flock` 写锁，锁文件位于 `.minidsh/sessions/.locks`，进程退出后由内核释放；读取不获取写锁。
- HTTP 对同一 session 的并发回合直接拒绝；同一 Application 的 job watcher 可在存储串行锁下追加审计事件，不会因回合写锁卡住等待者。

对应 HTTP 操作为 `POST /v1/sessions/<id>/repair` 和 `/migrate`。这是学习版 v0/v1 协议，不兼容官方多代际/Zstandard 格式。

## Surface replacement 与工具结果裁剪

`surface` 是当前发给模型的事件视图，原始 JSONL 仍是事实来源。普通消息视为追加；replacement 使用 `surfaceOp={op: replace, startSeq, endSeq}` 替换当前视图中的区间，并通过 `sourceEventSeqs` 引用被遮蔽事件。事件本身的 seq 和内容不会被修改。

```bash
# 查看当前模型视图及替换历史
PYTHONPATH=src python3 -m minidsh surface <session-id>

# 手动裁剪当前视图里过长的工具结果
PYTHONPATH=src python3 -m minidsh \
  --enable core.tool-result-pruner prune <session-id>
```

`core.tool-result-pruner` 默认关闭，不会自动裁剪。启用后，CLI、HTTP `POST /v1/sessions/<id>/prune` 和 Web“裁剪工具结果”按钮复用同一服务。每个被裁剪节点先追加 `compaction/prune` 计量事件，再追加只修改 `content` 的 `tool/result` replacement。

默认文本超过 8192 个 Unicode 码点时保留头部 4096、尾部 1024，中间换成 `[... tool result middle pruned ...]`。可用 `MINIDSH_PRUNE_THRESHOLD_CHARS`、`MINIDSH_PRUNE_HEAD_CHARS`、`MINIDSH_PRUNE_TAIL_CHARS` 调整，头部+标记+尾部不能超过阈值。非文本块保持原顺序，Python 字符串切片不会拆散 Unicode 码点。

折叠会拒绝不存在的起止节点、逆序区间、非当前节点、重复/未来来源引用，以及修改工具调用身份等非 content 字段。首个系统提示词只能由单个 system replacement 改写。旧事件没有 surfaceOp 时按 append 兼容。

`derive_messages`、resume、fork 和 compact 使用折叠后的模型视图；`transcript` 使用原始追加事件，因此裁剪副本不会抹掉用户已经看到的工具原文。重复 prune 不会重复裁剪已小于阈值的当前节点。

对应只读 API 为 `GET /v1/sessions/<id>/surface`。这是官方 surface 规则的纵向切片，不包含任意插件消息投影或完整官方事件类型系统。

## 图片附件与 Files API

CLI 和 resume 可重复传入 `--image`；HTTP 使用字符串数组，Web 在高级操作中每行填写一个工作区图片路径：

```bash
PYTHONPATH=src python3 -m minidsh run "描述这张图片" --image images/example.png
```

```json
{
  "prompt": "描述这张图片",
  "images": ["images/example.png"]
}
```

支持 PNG、JPEG、GIF、WebP 文件头识别。单张最大 32MiB，每请求最多 600 张、原始累计 128MiB，内联 base64 累计最大 20MiB。`read-only` 和 `workspace-write` 只允许工作区内路径，`danger-full-access` 才允许外部路径。

JSONL 只保存路径、媒体类型、字节数和 SHA-256，不写 base64。DeepSeek 请求准备时重新读取并核对附件；文件变化、格式不符或路径越界会失败。Mock 只验证新输入的附件元数据，不读取或分析图片内容。

| `DEEPSEEK_IMAGE_MODE` | 行为 |
|---|---|
| `auto`（默认） | 优先 Files 上传；上传失败或文件引用被拒绝时降级为内联 |
| `inline` | 不上传，直接使用 base64 |
| `required` | 必须取得文件 id，上传失败直接报错 |

上传使用 `<Messages 根>/files`、multipart 和 `files-api-2025-04-14`，默认生命周期 7 天。同一路由、凭据作用域和图片哈希在进程内复用未临近过期的文件 id，并发请求共享上传锁。无效文件引用会清除缓存；Messages 重试不会重复上传。事件流提供 `llm/image-upload` 和 `llm/image-fallback`。

查看远端文件列表：

```bash
PYTHONPATH=src python3 -m minidsh \
  --disable llm.mock --enable llm.deepseek files
```

`--disable llm.files` 后 `auto` 自动使用内联，`required` 会拒绝图片请求，Files 列表不可用。当前没有持久上传索引、自动配额清理、图片变换或浏览器文件选择器。

## 模型列表、重试与取消

查看模型不需要运行 Agent。默认 Mock 返回离线条目；查询真实 DeepSeek 列表时显式切换模型插件：

```bash
PYTHONPATH=src python3 -m minidsh models
PYTHONPATH=src python3 -m minidsh \
  --disable llm.mock --enable llm.deepseek models
```

DeepSeek 列表请求使用 Messages 根地址同源 origin 的 `GET /models`，默认超时 5000ms，成功结果缓存 600000ms；设 `DEEPSEEK_MODELS_MAX_AGE_MS=0` 可每次刷新。列表请求不自动重试，重定向不会转发凭据。

Messages 默认对 `EMPTY_RESPONSE/RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT` 最多重试 5 次，首次退避 500ms，指数增长至 10000ms，带 10% 对称抖动，并接受有上限的 `Retry-After`。每次重试发出 `llm/retry` 事件；`--disable llm.retry` 可完全关闭。`DEEPSEEK_RETRY_MODE=always` 会按上游语义持续重试所有错误，只应在配合取消和受控测试时使用。

SSE 的 `turn/start` 返回本次 `run_id`，可据此取消：

```bash
curl -X POST http://127.0.0.1:3080/v1/runs/<run-id>/cancel -d '{}'
```

取消会产生 `run/cancelled`，会话记录 `turn/end` 的 `aborted` 原因。模型流、重试等待、工具边界和前台 Shell 使用同一取消令牌；前台 Shell 被终止，而取消 `job_output` 等待不会杀掉被观察的后台任务。Web 的“停止运行”按钮调用同一 API。`--disable core.cancellation` 禁用外部取消入口。

## 提问、待办与 Web 工具

四个工具分别由 `tools.ask-user`、`tools.todo`、`tools.web-fetch`、`tools.web-search` 控制，默认全部关闭。可以分别启用，也可一起用于本地学习：

```bash
PYTHONPATH=src python3 -m minidsh \
  --enable tools.ask-user --enable tools.todo \
  --enable tools.web-fetch --enable tools.web-search \
  run "待办 完成交互测试"
```

Mock 使用以下显式前缀触发工具，不做通用自然语言理解：

| 输入前缀 | 工具 |
|---|---|
| `提问 是否继续？` | `ask_user_question` |
| `待办 完成测试` | `todo_write` |
| `获取 http://…` | `web_fetch` |
| `搜索 关键词` | `web_search` |

### 用户提问

`ask_user_question` 支持稳定问题 id、选项、多选、自定义文本和跳过。CLI 交互可输入选项编号、自由文本或回车跳过；空 `selected` 且没有 `custom` 表示跳过，不代表授权。问题与答案写入 `question/asked`、`question/answered`。

没有交互回答器的常驻宿主使用待答队列：

```text
GET  /v1/questions
POST /v1/questions/<question-id>/answer
```

答案 body 为 `{"answers": [{"id": "question-1", "selected": [], "custom": "回答"}]}`。运行取消或服务关闭会唤醒等待者并清理队列。当前 Web 页面只在事件流显示问题，尚无图形答题表单；可通过上述 API 回答。

### 会话待办

`todo_write` 每次写入完整列表，追加 `todo/write`，校验非空唯一内容、状态和活跃任务数量。默认最多一个 `in_progress`；设置 `MINIDSH_TODO_ALLOW_PARALLEL=1` 后允许多个。

查询使用 `minidsh todos <session-id>` 或 `GET /v1/sessions/<id>/todos`。最近一次整表写入生效，新的 `turn/start` 清空当前清单，`turn/end` 保留完成后的列表。

### Web 获取与搜索

`web_fetch` 支持 HTTP(S)、最多 5 次重定向、30 秒默认操作超时、1MiB 原始读取上限和 200000 字符默认输出上限。HTML 会移除脚本、样式、隐藏内容，超过 512 层或转换失败时不把原始 active markup 返回模型。文本和页面都带“外部内容不可信”提示与 URL。

默认拒绝 loopback、私网、链路本地和其他非公网地址；每次重定向都重新检查。本地测试显式使用 `MINIDSH_WEB_ALLOW_PRIVATE=1`。`MINIDSH_WEB_TIMEOUT_MS` 和 `MINIDSH_WEB_FETCH_MAX_OUTPUT_CHARS` 可调整预算。当前 DNS 检查未做连接级地址固定，不是完整 SSRF 防线。

`web_search` 通过可替换 provider 端点工作，设置 `MINIDSH_WEB_SEARCH_URL` 后向其 POST `{"query": "...", "maxResults": 10}`。响应提供 `sources`、`truncated` 和可选 `content`。支持去重、最多 10 个查询、10 条来源、每条摘要 800 字符和 Markdown 引用。未配置 provider 时工具仍可见，执行返回明确错误。

Web 内容不是指令；系统提示词会随工具开关加入上游对应英文提醒。两个工具都支持运行取消。当前 HTML 转换不是完整 Turndown/GFM，搜索 provider 协议是学习版 JSON 接口，不是官方 DeepSeek 搜索凭据或路由实现。

## HTTP 宿主与 Web 页面

启动本地 HTTP/SSE 宿主：

```bash
PYTHONPATH=src python3 -m minidsh serve --port 3080
```

然后打开 `http://127.0.0.1:3080`。页面支持运行/resume、fork、compact、会话列表和事件流。

API 摘要：

```text
GET  /health
GET  /v1/plugins
GET  /v1/tools
GET  /v1/models
GET  /v1/files
GET  /v1/prompt
GET  /v1/questions
GET  /v1/sessions
GET  /v1/sessions/<id>
GET  /v1/sessions/<id>/transcript
GET  /v1/sessions/<id>/surface
GET  /v1/sessions/<id>/todos
GET  /v1/jobs
GET  /v1/jobs/<id>/output
GET  /v1/terminals
POST /v1/runs
POST /v1/runs/stream
POST /v1/runs/<run-id>/cancel
POST /v1/questions/<question-id>/answer
POST /v1/sessions/<id>/resume
POST /v1/sessions/<id>/fork
POST /v1/sessions/<id>/compact
POST /v1/sessions/<id>/repair
POST /v1/sessions/<id>/migrate
POST /v1/sessions/<id>/prune
POST /v1/jobs/<id>/kill
POST /v1/terminals/<pty-id>/kill
```

默认只绑定 `127.0.0.1`。绑定非回环地址时必须设置 `MINIDSH_TOKEN`，客户端同时携带：

```text
Authorization: Bearer <token>
```

SSE 包含 turn、assistant、重试、工具执行、run result/error/cancelled 和 job 完成通知；内部 run/done 只用于关闭对应当前流。并发运行通过唯一 `run_id` 过滤事件，相同 prompt 也不会串会话或相互提前结束。

## 外部 Python 插件

插件通过标准 `importlib.metadata` entry points 发现，group 固定为 `minidsh.plugins`。项目附带一个独立的时钟工具发行包，它不在核心源码的 `PYTHONPATH` 中：

```bash
python3 -m pip install -e . -e ./examples/minidsh-clock
minidsh plugins
minidsh --enable example.clock plugins
minidsh --enable example.clock tools
```

对应声明位于 `examples/minidsh-clock/pyproject.toml`：

```toml
[project.entry-points."minidsh.plugins"]
"example.clock" = "minidsh_clock:ClockPlugin"
```

entry point 可以导出 `Plugin` 实例、`Plugin` 子类或返回 `Plugin` 的工厂。entry point 名称必须和 `plugin.name` 相同，且不能覆盖内置功能。外部插件沿用同一服务依赖、功能开关、工具注册表和提示词段，不需要修改 `Application` 的核心组装代码。

所有外部插件**默认禁用**。禁用状态只读取发行包元数据，不导入或执行插件模块；只有配置文件或 `--enable` 显式启用后才加载。安装第三方插件即表示信任其代码，启用后插件拥有当前 Python 进程的权限。插件已安装但未启用时，`plugins` 会显示 `disabled`、发行包、版本和 entry point 来源。

也可以在配置文件中启用，但卸载插件后必须同时删除对应开关，否则配置会按未知功能拒绝启动：

```json
{
  "features": {
    "example.clock": true
  }
}
```

## Headless、Web 与 Desktop 发行包

产品入口和核心组装分离，三个入口最终都复用同一个 `Application`：

| 发行包 | 命令 | 额外运行时依赖 |
|---|---|---|
| `minidsh` / `minidsh-headless` | `minidsh` / `minidsh-headless` | 无，仅 Python 标准库 |
| `minidsh-web` | `minidsh-web`，默认执行 `serve` | 无，复用核心 HTTP/SSE |
| `minidsh-desktop` | `minidsh-desktop`，默认执行 `desktop` | `pywebview` |

本地安装示例：

```bash
# 最小 Headless；核心本身也可直接作为 Headless 使用
python3 -m pip install -e . -e ./packages/minidsh-headless
minidsh-headless plugins

# Web：不需要安装 Desktop/GUI 依赖
python3 -m pip install -e . -e ./packages/minidsh-web
minidsh-web --port 3080

# Desktop：只有这个 profile 安装 pywebview
python3 -m pip install -e '.[desktop]' -e ./packages/minidsh-desktop
minidsh-desktop
```

也可以只安装核心后按需添加 GUI 依赖：`python3 -m pip install -e '.[desktop]'`，然后运行 `minidsh desktop`。核心不会在导入 CLI、列出插件或运行 Headless/Web 时导入 `pywebview`；缺少该依赖时只有 Desktop 命令返回明确安装提示。Linux 的 GUI 后端可能还需要操作系统提供的 GTK/Qt 运行库。

这里借鉴的是上游 `apps/cli`、`apps/web` 和 `apps/desktop` 的产品分层思想；Python entry points、`pywebview` 和这些发行包名称都是学习版设计，不是官方插件格式或官方桌面包的兼容实现。

## 接入真实 DeepSeek

先禁用 Mock，再启用 DeepSeek，避免两个插件同时提供 `llm` 服务：

```bash
export DEEPSEEK_API_KEY='你的 API Key'
PYTHONPATH=src python3 -m minidsh \
  --disable llm.mock \
  --enable llm.deepseek \
  run "读取 README.md"
```

可选环境变量：

| 变量 | 默认值 |
|---|---|
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/anthropic` |
| `DEEPSEEK_MODEL` | `deepseek-flash` |
| `DEEPSEEK_REASONING_EFFORT` | `high`，可选 `off / low / high / max` |
| `DEEPSEEK_MAX_TOKENS` | `256000` |
| `DEEPSEEK_STREAM_IDLE_TIMEOUT_MS` | `300000` |
| `DEEPSEEK_MODELS_TIMEOUT_MS` | `5000` |
| `DEEPSEEK_MODELS_MAX_AGE_MS` | `600000`，`0` 表示每次刷新 |
| `DEEPSEEK_RETRY_MODE` | `normal`，可显式设为 `always` |
| `DEEPSEEK_RETRY_MAX` | `5` |
| `DEEPSEEK_RETRY_INITIAL_DELAY_MS` | `500` |
| `DEEPSEEK_RETRY_MAX_DELAY_MS` | `10000` |
| `DEEPSEEK_RETRY_JITTER_RATIO` | `0.1` |
| `DEEPSEEK_IMAGE_MODE` | `auto`，可选 `inline / required` |
| `DEEPSEEK_FILES_API_TIMEOUT_MS` | `60000` |
| `DEEPSEEK_FILE_EXPIRY_SECONDS` | `604800`，允许 `3600–2592000` |

真实调用会访问网络并产生费用。密钥只从环境变量读取，不写入配置、会话或 Shell 环境。当前在线验收尚未执行；本地 Messages/SSE 端到端验收已通过。

## 当前架构

```text
minidsh-headless / minidsh-web / minidsh-desktop
  └─ CLI / Web UI / HTTP API / pywebview
      └─ Application：唯一的插件组装入口
          └─ PluginManager：功能开关、依赖检查、激活与逆序卸载
              ├─ 内置插件：直接注册
              ├─ EntryPoint 发现：默认禁用，显式启用后加载
              ├─ ServiceContext：服务注册与解析
              ├─ EventBus：工具、回合和任务事件
              ├─ SystemPrompt：身份段、工具段、runtime context
          ├─ SandboxPolicy：read-only / workspace-write / danger-full-access
          ├─ ApprovalService：ask / never / allowed-once / unavailable
          ├─ ObservationStore：文件版本观察与恢复
          ├─ JobService：进程、输出环、任务隔离、终止与回收
          ├─ CompactionService：token 估算、摘要、压缩检查点
          ├─ ToolRegistry：策略、审批和工具执行管线
          │   ├─ read / glob / write / edit
          │   └─ bash / job_output / job_list / job_kill
          ├─ JsonlSessionStore：事件、历史投影、resume、fork、transcript
          ├─ MockModel：离线模型适配器
          ├─ DeepSeekModel：Messages/SSE 模型适配器
          └─ Agent：turn / step / tool call 循环
              └─ ApiServer / SseBroker：HTTP、SSE、run_id 关联
```

## 源码阅读顺序

1. `src/minidsh/config.py`：配置、策略、上下文容量和功能开关。
2. `src/minidsh/plugins.py`：服务、插件、依赖与生命周期。
3. `src/minidsh/discovery.py`：entry points 元数据、延迟导入和插件契约。
4. `src/minidsh/app.py`：唯一的组装入口。
5. `src/minidsh/system_prompt.py`：官方提示词和 runtime context。
6. `src/minidsh/sandbox.py`：文件策略和对应文案。
7. `src/minidsh/approval.py`：审批结果与审计事件。
8. `src/minidsh/observations.py`：文件观察及恢复。
9. `src/minidsh/tools.py`：工具 schema、执行链、原子写入和路径边界。
10. `src/minidsh/jobs.py`：进程、输出环、任务隔离、等待、kill 和回收。
11. `src/minidsh/shell.py`：Bash 与 jobs 工具及输出渲染。
12. `src/minidsh/pty_shell.py`：PTY、命令标记、持久状态和终端回收。
13. `src/minidsh/questions.py`：问题校验、CLI 回答器、HTTP 待答和取消。
14. `src/minidsh/todo.py`：整表待办写入与并行策略。
15. `src/minidsh/web_tools.py`：Web 获取、搜索 provider 和安全边界。
16. `src/minidsh/llm.py`：模型接口、模型列表和 Mock 工具触发器。
17. `src/minidsh/retry.py`：重试模式、瞬时错误和指数退避。
18. `src/minidsh/cancellation.py`：协作式取消令牌与资源关闭回调。
19. `src/minidsh/images.py`：图片路径、格式、哈希和请求预算。
20. `src/minidsh/files.py`：multipart 上传、文件对象校验和 Files CRUD。
21. `src/minidsh/deepseek.py`：Messages 图片、SSE、重试、列表缓存和原生 replay。
22. `src/minidsh/session.py`：版本头、原子迁移、POSIX 写锁、待办和消息投影。
23. `src/minidsh/session_recovery.py`：工具结果补偿与开放 step/turn 闭环。
24. `src/minidsh/surface.py`：surface 折叠、来源引用和 replacement 校验。
25. `src/minidsh/pruner.py`：Unicode 工具结果裁剪与 replacement 写入。
26. `src/minidsh/compaction.py`：token 估算、压缩 prompt 和检查点。
27. `src/minidsh/agent.py`：Agent Loop、恢复入口、图片输入与取消终态。
28. `src/minidsh/server.py`：HTTP API、SSE、待答队列、会话互斥和终端生命周期。
29. `src/minidsh/web/index.html`：Web 工作台、图片路径与裁剪操作。
30. `src/minidsh/desktop.py`：延迟 GUI 导入和本地窗口宿主。
31. `examples/minidsh-clock`：可独立安装的外部工具插件。
32. `packages`：Headless、Web、Desktop 三个产品发行入口。
33. `src/minidsh/cli.py`：命令行、交互提问、迁移恢复与常驻宿主入口。

## 观察会话

启用 `session.jsonl` 后，每次运行都会生成 `.minidsh/sessions/<session-id>.jsonl`。事件包括：

```text
system/message
session/start / session/resume / session/fork
user/message / assistant/message
step/start / step/end
approval/asked / approval/decided
fs/observed
job/registered / job/settled
tool/call / tool/result
compaction/start / compaction/end / compaction/prune
session/migrated / session/repaired
pty/created / pty/reset
question/asked / question/answered
todo/write
turn/end
```

```bash
PYTHONPATH=src python3 -m minidsh session <session-id>
```

## 推荐学习路线

### 第 0 层：插件内核

理解插件激活、工具执行和事件落盘。

### 第 1 层：真实模型适配器

理解 Messages API、SSE、工具调用和原生思考回放。

### 第 2 层：审批与写操作

理解文件策略、失败关闭、审批审计、文件观察、原子写入和路径安全。

### 第 3 层：Shell 与后台任务

理解进程组、前后台任务、超时转后台、输出上限、取消和资源回收。

### 第 4 层：会话恢复与压缩

理解历史投影、resume、fork、文件观察恢复、token meter，以及“压缩视图但不删除事实”。

### 第 5 层：HTTP 与常驻宿主

理解 HTTP API、SSE、任务通知、run_id 关联、并发会话和最小 Web 页面。

### 第 6 层：外部插件与发行包

通过 entry points 发现独立 Python 插件；提供 Headless、Web、Desktop 发行入口，重依赖按需安装。

### 第 7 层：模型列表、重试与取消

理解同源模型发现、成功缓存、瞬时错误分类、指数退避、取消令牌和资源回收。

### 第 8 层：图片附件与 Files API

理解内容寻址附件、内联与文件引用、multipart 上传、缓存复用、失败降级和无效引用失效。

### 第 9 层：持久 Shell 与 PTY

理解控制终端、owner 隔离、命令串行化、随机完成标记、退出码、超时/取消重置和资源回收。

### 第 10 层：会话迁移与崩溃恢复

理解版本头、连续事件校验、备份与原子发布、工具结果补偿、开放回合闭环和 POSIX 写锁。

### 第 11 层：Surface replacement 与裁剪

理解追加事实与模型视图的分离、区间替换、来源引用、工具结果裁剪和人类 transcript。

### 第 12 层：提问、待办与 Web 工具（当前）

理解用户能力服务、待答队列、会话整表投影、外部不可信内容和 Web 安全边界。对照 `docs/upstream-parity.md` 阅读。

## 当前边界

- 有文件、Bash、提问、待办和 Web 工具切片，仍不是完整官方工具集；尚无定时 pending 提问和图形答题表单。
- Web 搜索使用学习版 JSON provider，HTML 不是完整 Turndown/GFM；DNS 检查未做连接级地址固定。
- HTTP/SSE 是学习 API，不兼容官方 Web Host 的认证、RPC 和多路复用协议。
- 插件依赖在启动时收敛，尚不支持运行中热重载。
- surface 只支持学习版消息事件和工具结果裁剪，尚无官方完整事件类型、插件消息投影、多代际不可变文件、Zstandard、Windows 写锁和格式目录。
- token meter 是启发式估算，尚无精确 tokenizer、图片 token 定价和模型切换压缩。
- 尚无内核沙箱和审批升级；PTY 仅支持 Unix `/bin/bash`，不支持 Windows ConPTY。
- PTY 是进程内学习实现，尚无官方终端行缓冲/OSC 就绪协议、跨进程恢复和文件策略切换围栏。
- 图片上传只有进程内缓存和锁，尚无持久索引、独立等待者取消、配额回收、图片变换和浏览器文件选择器。
- 模型列表尚未实现官方 badge、客户端版本要求、后台刷新和完整能力目录；列表请求不支持外部取消。
- 网络连接建立前只能检查取消状态，已建立的响应流可以主动关闭；`always` 重试不会自行设置次数上限。
- 当前 Web 页面不支持自定义 Authorization 请求头；token 主要用于 API 客户端。
- 尚未进行真实 DeepSeek 在线验收；这是教学代码，不应直接暴露为公网服务。
