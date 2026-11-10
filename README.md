# MiniDSH：从 0 学 Agent Harness

这是一个用于拆解 DeepSeek Harness（DSH）核心思想的学习项目。它不是官方 DSH 的 Python 移植，也不追求第一版就功能齐全，而是用可运行的小切片逐层回答：

1. 功能怎样通过插件组合，而不是写死在主程序里？
2. 模型怎样发现并调用工具？
3. 工具结果怎样回到 Agent Loop？
4. 会话事件怎样变成可回放的 JSONL？
5. 每个功能怎样独立开关，并由依赖关系决定能否激活？

当前版本只使用 Python 标准库，支持 Python 3.9+，不需要 API Key。

## 立即运行

在本目录执行：

```bash
PYTHONPATH=src python3 -m minidsh plugins
PYTHONPATH=src python3 -m minidsh tools
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

- `core.tools`：工具注册表。
- `tools.list_dir`：目录读取工具。
- `tools.read_file`：文本读取工具。
- `session.jsonl`：JSONL 会话持久化。
- `llm.mock`：确定性模拟模型。
- `agent.core`：Agent Loop。

临时禁用工具：

```bash
PYTHONPATH=src python3 -m minidsh --disable tools.list_dir tools
PYTHONPATH=src python3 -m minidsh --disable tools.list_dir plugins
```

禁用会话后，Agent 仍能运行，只是不再落盘：

```bash
PYTHONPATH=src python3 -m minidsh --disable session.jsonl run "请列出当前目录"
```

禁用模型后，`agent.core` 会因为缺少 `llm` 服务而保持 `pending`，而不是带着残缺依赖启动：

```bash
PYTHONPATH=src python3 -m minidsh --disable llm.mock plugins
```

完整配置示例见 `config.example.json`：

```bash
PYTHONPATH=src python3 -m minidsh --config config.example.json config
```

配置文件先合并，命令行的 `--enable` 和 `--disable` 后生效。

## 当前架构

```text
CLI
  └─ Application：注册并组装插件，不包含具体工具逻辑
      └─ PluginManager：功能开关、依赖检查、激活与逆序卸载
          └─ ServiceContext：服务注册与解析，对应 Cordis Context 的最小模型
          └─ EventBus：工具与回合事件
          ├─ ToolRegistry：工具目录与执行管线
          │   ├─ list_dir
          │   └─ read_file
          ├─ JsonlSessionStore：追加式会话事件
          ├─ MockModel：可替换的模型适配器
          └─ Agent：turn / step / tool call 循环
```

与官方 DSH 的概念对应：

| MiniDSH | DSH 中对应的概念 |
|---|---|
| `PluginManager` | Cordis 插件生命周期与依赖收敛的简化版 |
| `ServiceContext` | Cordis 的共享 Context 和服务注入 |
| `features` | profile / bundle 的功能组合入口，但不等于完整 profile 格式 |
| `ToolRegistry` | `ctx.tools` 和工具执行管线 |
| `JsonlSessionStore` | 追加式 `SessionEvent` 日志 |
| `Model` | LLM adapter  seam |
| `Agent.run()` | turn / step / tool call 的最小 Agent Loop |

## 源码阅读顺序

1. `src/minidsh/config.py`：默认配置、JSON 配置和命令行覆盖。
2. `src/minidsh/plugins.py`：服务、插件、依赖与生命周期，是最核心的文件。
3. `src/minidsh/app.py`：唯一的组装入口；新增功能时看这里。
4. `src/minidsh/tools.py`：工具定义、注册、执行事件和工作区路径边界。
5. `src/minidsh/llm.py`：模型接口和 MockModel；真实模型适配器以后只替换这一层。
6. `src/minidsh/agent.py`：一条完整的 Agent Loop。
7. `src/minidsh/session.py`：事件追加与读取。
8. `src/minidsh/cli.py`：最外层用户入口。

## 观察会话

启用 `session.jsonl` 后，每次运行都会在工作区生成 `.minidsh/sessions/<session-id>.jsonl`。事件包括：

```text
session/start
user/message
assistant/message
tool/call
tool/result
assistant/message
turn/end
```

把文件名（不含 `.jsonl`）传给下面的命令即可查看：

```bash
PYTHONPATH=src python3 -m minidsh session <session-id>
```

## 推荐学习路线

### 第 0 层：当前内核

目标：能解释插件为什么激活、工具怎样执行、事件怎样落盘。

练习：

- 禁用 `core.tools`，观察所有依赖 `tools` 的插件。
- 禁用 `session.jsonl`，确认 Agent 功能不受影响。
- 给 `MockModel` 增加一个新的工具调用分支。

### 第 1 层：真实模型适配器

新增 `llm.deepseek`，用 HTTP/SSE 接入真实模型，保持 `Model.complete()` 不变。先支持流式文本，再支持工具调用，最后把 reasoning、usage 和错误事件写入 Session。MockModel 继续保留用于离线开发。

### 第 2 层：审批与安全边界

在 `tools/pre-execute` 和实际执行之间加入 ApprovalService。分别实现只读、工作区可写、完全访问；不要让路径检查代替 Shell 审批。

### 第 3 层：更多工具与事件管线

增加 `write_file`、`edit_file`、`bash`，为每个工具建立独立开关、输入 schema、输出上限、取消和超时。实现 `waterfall` 事件，让插件可以改写或拒绝工具调用。

### 第 4 层：会话恢复与压缩

从 JSONL 重建模型消息，支持 resume、fork、transcript；随后增加 token meter 和 compaction，区分“持久化事实”和“发给模型的投影”。

### 第 5 层：HTTP 与 Web UI

先把 `run` 暴露为 HTTP，再增加 SSE/WebSocket 流式事件，最后接前端。不要在 UI 中复制 Agent 逻辑。

### 第 6 层：外部插件与发行包

把内置插件迁移到独立 Python 包，通过 entry points 发现；再提供 headless、web、desktop 等 profile。Office、语音、浏览器等重依赖按需安装，避免重演全量依赖导致的安装包膨胀。

## 当前边界

- MockModel 不是真实 LLM。
- 当前工具只读，且限制在工作区内。
- 插件依赖在启动时收敛，尚不支持运行中热重载。
- 会话只有追加和读取，尚未实现 resume 和消息投影。
- 这是教学代码，不应直接暴露为公网服务。
