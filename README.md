# MiniDSH：从 0 学 Agent Harness

这是一个用于拆解 DeepSeek Harness（DSH）核心思想的 Python 学习项目。它不是官方 DSH 的完整移植，而是通过可运行的小切片逐层理解插件、工具、模型、审批、Shell、后台任务、会话恢复、压缩和 Agent Loop。

当前只使用 Python 标准库，支持 Python 3.9+。MockModel 不需要 API Key；真实 DeepSeek 模型插件可独立开关。

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
- `core.tools`：工具注册表。
- `tools.glob`：官方 `glob` 文件发现工具。
- `tools.read`：官方 `read` 文本读取工具。
- `tools.write`：官方 `write` 创建/覆盖工具。
- `tools.edit`：官方 `edit` 字面替换工具。
- `tools.bash`：官方 `bash` 进程工具。
- `tools.jobs`：官方 `job_output/job_list/job_kill` 工具组。
- `session.jsonl`：JSONL 会话持久化。
- `llm.mock`：确定性模拟模型，默认启用。
- `llm.deepseek`：真实 DeepSeek Messages 模型，默认禁用。
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

当前 CLI 是短进程，退出时会回收后台任务；长期运行、任务通知和跨进程任务恢复需要后续常驻宿主阶段。

## 会话恢复与压缩

查看会话并恢复：

```bash
PYTHONPATH=src python3 -m minidsh sessions
PYTHONPATH=src python3 -m minidsh resume <session-id> "继续刚才的任务"
```

其他操作：

```bash
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

真实调用会访问网络并产生费用。密钥只从环境变量读取，不写入配置、会话或 Shell 环境。当前在线验收尚未执行；本地 Messages/SSE 端到端验收已通过。

## 当前架构

```text
CLI
  └─ Application：唯一的插件组装入口
      └─ PluginManager：功能开关、依赖检查、激活与逆序卸载
          └─ ServiceContext：服务注册与解析
          └─ EventBus：工具与回合事件
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
```

## 源码阅读顺序

1. `src/minidsh/config.py`：配置、策略、上下文容量和功能开关。
2. `src/minidsh/plugins.py`：服务、插件、依赖与生命周期。
3. `src/minidsh/app.py`：唯一的组装入口。
4. `src/minidsh/system_prompt.py`：官方提示词和 runtime context。
5. `src/minidsh/sandbox.py`：文件策略和对应文案。
6. `src/minidsh/approval.py`：审批结果与审计事件。
7. `src/minidsh/observations.py`：文件观察及恢复。
8. `src/minidsh/tools.py`：工具 schema、执行链、原子写入和路径边界。
9. `src/minidsh/jobs.py`：进程、输出环、任务隔离、等待、kill 和回收。
10. `src/minidsh/shell.py`：Bash 与 jobs 工具及输出渲染。
11. `src/minidsh/llm.py`：模型接口、内容块和 MockModel。
12. `src/minidsh/deepseek.py`：Messages 请求、SSE 和原生 replay。
13. `src/minidsh/session.py`：事件、消息投影、resume、fork、transcript。
14. `src/minidsh/compaction.py`：token 估算、压缩 prompt 和检查点。
15. `src/minidsh/agent.py`：完整的最小 Agent Loop。
16. `src/minidsh/cli.py`：命令行与交互审批。

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
compaction/start / compaction/end
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

### 第 4 层：会话恢复与压缩（当前）

理解历史投影、resume、fork、文件观察恢复、token meter，以及“压缩视图但不删除事实”。对照 `docs/upstream-parity.md` 阅读。

### 第 5 层：HTTP 与常驻宿主/Web UI

增加 HTTP、SSE/WebSocket、任务通知和 Agent 唤醒；不要在 UI 中复制 Agent 逻辑。

### 第 6 层：外部插件与发行包

通过 entry points 发现独立 Python 插件；提供 headless、web、desktop 等 profile，重依赖按需安装。

## 当前边界

- 有 `read/glob/write/edit/bash` 和三个 job 工具，仍不是完整官方工具集。
- 插件依赖在启动时收敛，尚不支持运行中热重载。
- 已支持 resume/fork/压缩，但尚无官方 surface replacement、格式迁移、跨进程锁和崩溃修复。
- token meter 是启发式估算，尚无精确 tokenizer、tool-result pruner 和模型切换压缩。
- 尚无内核沙箱、PTY、持久 Shell、任务通知、审批升级、图片、重试和模型 listing。
- 当前 Shell 固定使用 `/bin/bash`，尚未支持 Windows 原生进程后端。
- 尚未进行真实 DeepSeek 在线验收；这是教学代码，不应直接暴露为公网服务。
