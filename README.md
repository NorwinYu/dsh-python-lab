# MiniDSH：从 0 学 Agent Harness

这是一个用于拆解 DeepSeek Harness（DSH）核心思想的 Python 学习项目。它不是官方 DSH 的完整移植，而是通过可运行的小切片逐层理解插件、工具、模型、审批、会话和 Agent Loop。

当前只使用 Python 标准库，支持 Python 3.9+。MockModel 不需要 API Key；真实 DeepSeek 模型插件可独立开关。

## Git 与上游基线

项目使用 Git 分阶段提交。当前对照的上游 commit 为：

```text
fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159
```

已对齐和未对齐的边界记录在 `docs/upstream-parity.md`。当前已实现的工具提示词和 runtime-context 文案使用上游英文原文。

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
- `core.approval`：写操作审批与失败关闭。
- `core.fs-observation`：同会话文件读取版本。
- `core.tools`：工具注册表。
- `tools.glob`：官方 `glob` 文件发现工具。
- `tools.read`：官方 `read` 文本读取工具。
- `tools.write`：官方 `write` 创建/覆盖工具。
- `tools.edit`：官方 `edit` 字面替换工具。
- `session.jsonl`：JSONL 会话持久化。
- `llm.mock`：确定性模拟模型，默认启用。
- `llm.deepseek`：真实 DeepSeek Messages 模型，默认禁用。
- `agent.core`：Agent Loop。

示例：

```bash
PYTHONPATH=src python3 -m minidsh --disable tools.glob tools
PYTHONPATH=src python3 -m minidsh --disable tools.edit prompt
PYTHONPATH=src python3 -m minidsh --disable llm.mock plugins
```

禁用全部模型后，`agent.core` 因缺少 `llm` 服务而保持 `pending`。禁用 `session.jsonl` 后 Agent 仍可运行，但不会保存会话或审批审计。

完整配置示例见 `config.example.json`。配置文件先合并，命令行的 `--enable` 和 `--disable` 后生效。

## 文件策略与写操作

默认文件策略是 `read-only`，因此写操作会被拒绝。若要允许工作区写入，需要同时：

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

`--yes` 会允许本次进程内的所有写操作，不应在内容不可信时使用。交互终端中省略 `--yes` 会逐次询问；非交互环境没有可用审批者时，写操作返回 `unavailable`。

其他策略：

```bash
# 所有需要审批的写操作都自动拒绝
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode workspace-write \
  --approval-policy never \
  run "写入 denied.txt：hello"

# 完全访问：不限制工作区路径，也不触发写前审批
PYTHONPATH=src python3 -m minidsh \
  --sandbox-mode danger-full-access \
  run "写入 full.txt：hello"
```

`write` 覆盖已有文件前必须读过它；`edit` 默认要求旧文本恰好出现一次。读取后如果文件被外部修改，也会拒绝继续覆盖。

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

真实调用会访问网络并产生费用。密钥只从环境变量读取，不写入配置或会话。当前在线验收尚未执行；本地 Messages/SSE 端到端验收已通过。

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
          ├─ ObservationStore：同会话文件版本
          ├─ ToolRegistry：策略、审批和工具执行管线
          │   ├─ read / glob
          │   └─ write / edit
          ├─ JsonlSessionStore：追加式会话与审批事件
          ├─ MockModel：离线模型适配器
          ├─ DeepSeekModel：Messages/SSE 模型适配器
          └─ Agent：turn / step / tool call 循环
```

## 源码阅读顺序

1. `src/minidsh/config.py`：配置、文件策略、审批策略和功能开关。
2. `src/minidsh/plugins.py`：服务、插件、依赖与生命周期。
3. `src/minidsh/app.py`：唯一的组装入口。
4. `src/minidsh/system_prompt.py`：官方提示词和 runtime context。
5. `src/minidsh/sandbox.py`：文件策略和对应文案。
6. `src/minidsh/approval.py`：审批结果与审计事件。
7. `src/minidsh/observations.py`：同会话文件版本观察。
8. `src/minidsh/tools.py`：工具 schema、执行链、原子写入和路径边界。
9. `src/minidsh/llm.py`：模型接口、内容块和 MockModel。
10. `src/minidsh/deepseek.py`：Messages 请求、SSE 和原生 replay。
11. `src/minidsh/agent.py`：完整的最小 Agent Loop。
12. `src/minidsh/session.py`：事件追加与读取。
13. `src/minidsh/cli.py`：命令行与交互审批。

## 观察会话

启用 `session.jsonl` 后，每次运行都会生成 `.minidsh/sessions/<session-id>.jsonl`。事件包括：

```text
system/message
session/start
user/message
step/start
assistant/message
approval/asked
approval/decided
tool/call
tool/result
step/end
turn/end
```

查看会话：

```bash
PYTHONPATH=src python3 -m minidsh session <session-id>
```

## 推荐学习路线

### 第 0 层：插件内核

理解插件为什么激活、工具怎样执行、事件怎样落盘。

### 第 1 层：真实模型适配器

理解 Messages API、SSE、工具调用和原生思考回放。

### 第 2 层：审批与写操作（当前）

理解文件策略、失败关闭、审批审计、同会话观察、原子写入和路径安全。对照 `docs/upstream-parity.md` 阅读。

### 第 3 层：Shell 与后台任务

增加官方 `bash`、`job_output`、`job_list`、`job_kill`，实现进程组、超时、后台任务、输出上限和取消。不要把 Shell 命令字符串检查误当成完整沙箱。

### 第 4 层：会话恢复与压缩

从 JSONL 重建模型消息，支持 resume、fork、transcript；随后增加 token meter 和 compaction。

### 第 5 层：HTTP 与 Web UI

先暴露 HTTP，再增加 SSE/WebSocket 流式事件，最后接前端。不要在 UI 中复制 Agent 逻辑。

### 第 6 层：外部插件与发行包

把内置插件迁移到独立 Python 包，通过 entry points 发现；提供 headless、web、desktop 等 profile，重依赖按需安装。

## 当前边界

- 只有 `read/glob/write/edit`，尚无 Shell、PTY、子代理和完整官方工具集。
- 插件依赖在启动时收敛，尚不支持运行中热重载。
- 会话只有追加和读取，尚未实现 resume 和消息投影。
- 尚未实现内核沙箱、审批升级、图片、重试、模型 listing 和真实在线验收。
- 这是教学代码，不应直接暴露为公网服务。
