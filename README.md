# MiniDSH：从 0 学 Agent Harness

这是一个用于拆解 DeepSeek Harness（DSH）核心思想的 Python 学习项目。它不是官方 DSH 的完整移植，而是通过可运行的小切片逐层理解插件、工具、模型、会话和 Agent Loop。

当前只使用 Python 标准库，支持 Python 3.9+。MockModel 不需要 API Key；真实 DeepSeek 模型插件可独立开关。

## Git 与上游基线

项目使用 Git 分阶段提交。当前对照的上游 commit 为：

```text
fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159
```

已对齐和未对齐的边界记录在 `docs/upstream-parity.md`。不要根据“MiniDSH”这个名字推断系统提示词：当前已实现的提示词使用上游英文原文。

查看阶段历史：

```bash
git log --oneline
git status
```

## 立即运行

在本目录执行：

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

当前每个功能都是插件，都有独立开关：

- `core.system-prompt`：官方身份段、工具段和 runtime context 组装。
- `core.tools`：工具注册表。
- `tools.glob`：官方 `glob` 文件发现工具。
- `tools.read`：官方 `read` 文本读取工具。
- `session.jsonl`：JSONL 会话持久化。
- `llm.mock`：确定性模拟模型，默认启用。
- `llm.deepseek`：真实 DeepSeek Messages 模型，默认禁用。
- `agent.core`：Agent Loop。

临时禁用工具：

```bash
PYTHONPATH=src python3 -m minidsh --disable tools.glob tools
PYTHONPATH=src python3 -m minidsh --disable tools.read plugins
```

禁用会话后，Agent 仍能运行，只是不再落盘：

```bash
PYTHONPATH=src python3 -m minidsh --disable session.jsonl run "请列出当前目录"
```

禁用全部模型后，`agent.core` 因缺少 `llm` 服务而保持 `pending`：

```bash
PYTHONPATH=src python3 -m minidsh --disable llm.mock plugins
```

完整配置示例见 `config.example.json`：

```bash
PYTHONPATH=src python3 -m minidsh --config config.example.json config
```

配置文件先合并，命令行的 `--enable` 和 `--disable` 后生效。

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
          ├─ ToolRegistry：工具目录与执行管线
          │   ├─ glob
          │   └─ read
          ├─ JsonlSessionStore：追加式会话事件
          ├─ MockModel：离线模型适配器
          ├─ DeepSeekModel：Messages/SSE 模型适配器
          └─ Agent：turn / step / tool call 循环
```

与官方 DSH 的概念对应：

| MiniDSH | DSH 中对应的概念 |
|---|---|
| `PluginManager` | Cordis 插件生命周期与依赖收敛的简化版 |
| `ServiceContext` | Cordis 的共享 Context 和服务注入 |
| `features` | profile / bundle 的功能组合入口，不等于完整 profile 格式 |
| `SystemPrompt` | `ctx.systemPrompt` 的有序段落和 runtime context 子集 |
| `ToolRegistry` | `ctx.tools` 和工具执行管线 |
| `JsonlSessionStore` | 追加式 `SessionEvent` 日志 |
| `Model` | LLM adapter seam |
| `Agent.run()` | turn / step / tool call 的最小 Agent Loop |

## 源码阅读顺序

1. `src/minidsh/config.py`：默认配置、JSON 配置和命令行覆盖。
2. `src/minidsh/plugins.py`：服务、插件、依赖与生命周期。
3. `src/minidsh/app.py`：唯一的组装入口。
4. `src/minidsh/system_prompt.py`：官方提示词和 runtime context。
5. `src/minidsh/tools.py`：`glob/read` 的 schema、提示词与执行。
6. `src/minidsh/llm.py`：模型接口、内容块和 MockModel。
7. `src/minidsh/deepseek.py`：Messages 请求序列化、SSE、原生 replay。
8. `src/minidsh/agent.py`：完整的最小 Agent Loop。
9. `src/minidsh/session.py`：事件追加与读取。
10. `src/minidsh/cli.py`：最外层用户入口。

## 观察会话

启用 `session.jsonl` 后，每次运行都会生成 `.minidsh/sessions/<session-id>.jsonl`。事件包括：

```text
system/message
session/start
user/message
step/start
assistant/message
tool/call
tool/result
step/end
turn/end
```

把文件名（不含 `.jsonl`）传给下面的命令即可查看：

```bash
PYTHONPATH=src python3 -m minidsh session <session-id>
```

## 推荐学习路线

### 第 0 层：插件内核

目标：能解释插件为什么激活、工具怎样执行、事件怎样落盘。

练习：禁用 `core.tools`，观察所有依赖 `tools` 的插件；给 MockModel 增加一个工具调用分支。

### 第 1 层：真实模型适配器（当前）

目标：理解 Messages API、SSE、工具调用和原生思考回放。

当前已支持文本、reasoning、工具调用、usage 和 replay metadata。对照 `docs/upstream-parity.md` 阅读，不要把本地协议子集误认为完整官方适配器。

### 第 2 层：审批与安全边界

在 `tools/pre-execute` 和实际执行之间加入 ApprovalService。分别实现只读、工作区可写、完全访问；不要让路径检查代替 Shell 审批。

### 第 3 层：更多工具与事件管线

增加官方 `write`、`edit`、`bash`，为每个工具建立独立开关、输入 schema、输出上限、取消和超时。实现 `waterfall` 事件，让插件可以改写或拒绝工具调用。

### 第 4 层：会话恢复与压缩

从 JSONL 重建模型消息，支持 resume、fork、transcript；随后增加 token meter 和 compaction，区分“持久化事实”和“发给模型的投影”。

### 第 5 层：HTTP 与 Web UI

先把 `run` 暴露为 HTTP，再增加 SSE/WebSocket 流式事件，最后接前端。不要在 UI 中复制 Agent 逻辑。

### 第 6 层：外部插件与发行包

把内置插件迁移到独立 Python 包，通过 entry points 发现；再提供 headless、web、desktop 等 profile。Office、语音、浏览器等重依赖按需安装。

## 当前边界

- 只实现 `glob/read` 两个只读工具，不是官方完整工具集。
- 插件依赖在启动时收敛，尚不支持运行中热重载。
- 会话只有追加和读取，尚未实现 resume 和消息投影。
- 尚未实现审批、写操作、Shell、图片、重试、模型 listing 和真实在线验收。
- 这是教学代码，不应直接暴露为公网服务。
