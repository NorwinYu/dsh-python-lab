# MiniDSH

**一个用 Python 编写的分层、可检查的智能体框架——为被阅读、被运行、被拆解而构建。**

[English](README.md) | 简体中文

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![Dependencies: none](https://img.shields.io/badge/core%20dependencies-none-2ea44f)](pyproject.toml)
[![Docs site](https://img.shields.io/badge/docs-github%20pages-4c53d6)](https://norwinyu.github.io/dsh-python-lab/)
[![Upstream parity](https://img.shields.io/badge/upstream-dsh%20parity%20report-6b74f0)](docs/upstream-parity.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)

MiniDSH 用约 12,000 行零依赖的 Python 重新实现了现代编码智能体框架的*形状*。它既不是聊天机器人
demo，也不是对某个 SDK 的封装：里面有一个插件内核、一个智能体循环、一张工具注册表、一套权限栈、
一个事件溯源式会话存储、子代理、一个工作流引擎、一个 MCP 客户端、技能、一个调度器、持久化目标、
程序化工具调用，以及三个产品外壳（CLI、HTTP + Web、Desktop）。

每一层都可以独立开启或关闭、独立观察、独立验证。如果你曾好奇*工具调用到底发生在哪里*、*为什么恢复
后的会话会忘记它写过的某个文件*，或者*是什么挡住了智能体删除你的主目录*，这个仓库会用可运行的代码
而不是示意图来回答这些问题。

> **它不是 DeepSeek Harness 的官方移植。** 究竟实现了哪些切片、还剩下哪些边界，都记录在
> [docs/upstream-parity.md](docs/upstream-parity.md) 里（335 条，刻意不留情面）。我们从不拿
> “基本对齐”去掩盖一项缺失的能力。

---

## 目录

- [为什么要做这个项目](#为什么要做这个项目)
- [十分钟能做什么](#十分钟能做什么)
- [快速开始](#快速开始)
- [仓库结构](#仓库结构)
- [架构](#架构)
- [一次运行的完整生命周期](#一次运行的完整生命周期)
- [功能全景](#功能全景)
- [功能深入剖析](#功能深入剖析)
  - [1. 插件内核与组合](#1-插件内核与组合)
  - [2. 智能体循环](#2-智能体循环)
  - [3. 系统提示词与运行时上下文](#3-系统提示词与运行时上下文)
  - [4. 文件工具](#4-文件工具)
  - [5. 沙箱策略、审批与观察](#5-沙箱策略审批与观察)
  - [6. 内核沙箱（macOS Seatbelt）](#6-内核沙箱macos-seatbelt)
  - [7. Shell、后台任务与 PTY](#7-shell后台任务与-pty)
  - [8. 会话：世代、恢复、分叉与修复](#8-会话世代恢复分叉与修复)
  - [9. 表面替换与工具结果裁剪](#9-表面替换与工具结果裁剪)
  - [10. 压缩](#10-压缩)
  - [11. 流式输出、流记录与 SSE 重放](#11-流式输出流记录与-sse-重放)
  - [12. 提问与待办](#12-提问与待办)
  - [13. Web 抓取与搜索](#13-web-抓取与搜索)
  - [14. 子代理](#14-子代理)
  - [15. 工作流引擎](#15-工作流引擎)
  - [16. MCP 客户端](#16-mcp-客户端)
  - [17. 技能](#17-技能)
  - [18. 定时任务](#18-定时任务)
  - [19. 目标与自动轮次](#19-目标与自动轮次)
  - [20. 程序化工具调用（PTC）](#20-程序化工具调用ptc)
  - [21. 智能体预设](#21-智能体预设)
  - [22. HTTP 宿主与 Web 工作台](#22-http-宿主与-web-工作台)
  - [23. 外部插件](#23-外部插件)
  - [24. 产品发行版](#24-产品发行版)
  - [25. 真实 DeepSeek 适配器](#25-真实-deepseek-适配器)
- [配置参考](#配置参考)
- [事件参考](#事件参考)
- [与 2026 年智能体生态的对应关系](#与-2026-年智能体生态的对应关系)
- [27 层学习路径](#27-层学习路径)
- [调试手册](#调试手册)
- [边界与非目标](#边界与非目标)
- [常见问题](#常见问题)
- [文档索引](#文档索引)

---

## 为什么要做这个项目

大多数智能体框架都在为*尽快把一个应用发出去*而优化。它们把有意思的部分藏了起来：工具 schema 是
怎么注册的、审批在什么时候被检查、取消的时候会发生什么、历史是怎么被压缩的、崩溃之后还有什么能
留下来。对产品而言这没问题，对学习而言则很糟。

MiniDSH 优化的方向恰恰相反：**每个机制都可见，而且每个机制都能被关掉，好让你看到它坏在哪。**

三条设计规则让这件事成立：

1. **是服务，不是全局变量。** 一切都是插件，插件提供具名服务、也依赖具名服务。关掉一个功能，它的
   服务、它的工具 schema、它的提示词段落会一起消失。
2. **事实与投影分离。** 会话日志是仅追加、不可变的。模型看到的是一个派生视图，可以压缩、裁剪或
   替换，而无需改写历史。
3. **失败即关闭。** 缺少审批者、未知的功能开关、损坏的会话、缺失的预设、超大的负载——一律以显式
   错误终止操作，而不是悄悄降级。

结果是：一个在小尺度上就能像真实框架一样行事、而且你能把它整个装进脑子里的框架。

**适合谁读：** 想搞懂框架内部机制的工程师，在评估自研还是采购的团队，以及准备智能体平台面试的人
——那类面试的问题常常就是“你会怎么实现 X”。

---

## 十分钟能做什么

```bash
# 1. Run an offline agent with no API key at all
minidsh run "read README.md" --trace

# 2. Watch a tool call disappear because you disabled the tool
minidsh --disable tools.read prompt      # the read section vanishes from the prompt

# 3. See a permission decision and its audit trail
minidsh --sandbox-mode workspace-write run "write demo.txt: hello"
minidsh session <session-id>             # approval/asked + approval/decided events

# 4. Resume a session and watch the file observation come back
minidsh resume <session-id> "edit demo.txt: replace hello with world"

# 5. Open the workbench
minidsh serve --port 3080                # http://127.0.0.1:3080 (EN / 中文 toggle)
```

上面所有内容都能离线运行，用的是确定性的 `MockModel`。导出 `DEEPSEEK_API_KEY` 并启用
`llm.deepseek` 之后，同样的命令就可以对接真实模型。

---

## 快速开始

**环境要求：** macOS 或 Linux 上的 Python 3.9+。只有 `workflow` 和 `run_code` 需要 Node.js。只有
Desktop 外壳需要 `pywebview`。

```bash
git clone https://github.com/NorwinYu/dsh-python-lab.git
cd dsh-python-lab

python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
```

然后：

```bash
minidsh plugins      # which layers are active, pending, disabled, or missing a dependency
minidsh tools        # the tool schemas the model currently sees
minidsh prompt       # the exact system prompt + runtime context
minidsh run "read README.md" --trace
```

如果 `minidsh` 报 `ModuleNotFoundError: No module named 'minidsh'`，说明当前的解释器跳过了可编辑
安装生成的 `.pth` 文件（部分内置或打过补丁的 CPython 版本会这样）。可以用兼容模式重装，或直接从
源码运行 —— 两者入口完全一致：

```bash
python3 -m pip install -e . --config-settings editable_mode=compat
# 或者：
PYTHONPATH=src python3 -m minidsh plugins
```

`--trace` 会打印回合、每次工具调用以及每个事件的负载，因此你不用打开会话文件就能跟着循环走。

### 第一次被放行的写入

默认沙箱策略是 `read-only`，所以写入和 shell 都会被拒绝。要在工作区内允许写入，你必须做**两**
件事：放宽策略，并且批准这次操作。

```bash
# Non-interactive: approve every write/shell in this process (learning only)
minidsh --sandbox-mode workspace-write --yes run "write demo.txt: hello"

# Interactive: approve each operation at the prompt
minidsh --sandbox-mode workspace-write run "write demo.txt: hello"
```

> `--yes` 会批准当前进程中的**一切**操作。绝不要在模型会读取不可信内容的环境里使用它。

### 模拟模型速查表

`MockModel` 不具备任何自然语言理解能力。它把显式前缀映射为意图，这正是轨迹可复现的原因。默认
语法是英文；每个意图同时接受一个中文别名，因为这个实验项目起步于中文，而真实会话中常常中英混用。

| 意图 | 英文 | 中文别名 | 工具 |
|---|---|---|---|
| List directory | `list` | `目录` | `glob` |
| Read a file | `read README.md` | `读取 README.md` | `read` |
| Write a file | `write demo.txt: hello` | `写入 demo.txt：hello` | `write` |
| Edit a file | `edit demo.txt: replace hello with world` | `编辑 demo.txt：把hello替换为world` | `read` → `edit` |
| Run a command | `run printf hello` | `执行 printf hello` | `bash` |
| Background command | `run in background sleep 10` | `执行后台 sleep 10` | `bash` |
| List jobs | `jobs` | `任务列表` | `job_list` |
| Read job output | `job output bash-1` | `读取任务 bash-1` | `job_output` |
| Kill a job | `kill job bash-1` | `终止任务 bash-1` | `job_kill` |
| Ask the user | `ask Continue?` | `提问 是否继续？` | `ask_user_question` |
| Write todos | `todo Finish the lab` | `待办 完成实验` | `todo_write` |
| Fetch a URL | `fetch https://example.com` | `获取 https://example.com` | `web_fetch` |
| Search the web | `search agent harness` | `搜索 agent harness` | `web_search` |
| Load a skill | `skill review` | `使用技能 review` | `skill` |
| Delegate | `delegate analysis \| read README.md` | `委派 分析 \| 读取 README.md` | `subagent` |
| Fork a subagent | `fork agent summarize` | `派生 总结` | `subagent_fork` |
| List agents | `agents` | `子代理列表` | `list_agents` |
| Message an agent | `message agent-1: keep going` | `发送消息 agent-1：继续` | `send_message` |
| Interrupt an agent | `interrupt agent-1` | `中断子代理 agent-1` | `interrupt_agent` |
| Wait for input | `wait agent 5000` | `等待子代理输入 5000` | `wait_agent` |
| Run a workflow | `workflow hello\|world` | `工作流 你好\|你好` | `workflow` |
| Run code | `run code {"code": "return 1"}` | `运行代码 {...}` | `run_code` |
| Create a goal | `goal create finish migration\|4` | `创建目标 完成迁移\|4` | `create_goal` |
| Read the goal | `goal get` | `读取目标` | `get_goal` |
| Update the goal | `goal update {...}` | `更新目标 {...}` | `update_goal` |
| Create a schedule | `schedule create {...}` | `定时任务 {...}` | `schedule_create` |
| List schedules | `schedule list` | `定时列表` | `schedule_list` |
| View a schedule | `schedule view s1` | `定时查看 s1` | `schedule_view` |
| Edit a schedule | `schedule edit {...}` | `定时编辑 {...}` | `schedule_edit` |

未知前缀会返回一条帮助信息，而不是猜一个工具调用——这是一个很小但很重要的性质：**模拟模型从不
臆造意图。** 完整的语法说明见 [docs/mock-model.md](docs/mock-model.md)。

### 打开 Web 工作台

```bash
minidsh serve --port 3080
# then open http://127.0.0.1:3080
```

工作台是一个零依赖的单文件 HTML，由框架自身提供服务。它带有中/英切换、明/暗主题、结构化执行
轨迹、实时流式文本、待答问题表单、会话浏览，以及压缩/裁剪/分叉控制。

---

## 仓库结构

```text
dsh-python-lab/
├── README.md                 English guide (this file)
├── README.zh-CN.md           Chinese guide
├── AGENTS.md                 Instructions for coding agents working in this repo
├── LICENSE                   MIT
├── THIRD_PARTY_NOTICES.md    Upstream MIT notice and what prose is derived
├── config.example.json       Every feature flag in one place
├── pyproject.toml            Zero-dependency core; optional [desktop] extra
├── docs/
│   ├── index.html            Textbook site shell (EN / 中文)
│   ├── chapters.js           31 bilingual chapters: concept → cost
│   ├── assets/               Workbench screenshots used by the site
│   ├── modules.md            Module atlas: role, contracts, and traps per file
│   ├── exercises.md          Runnable labs mapped to 2026 practice
│   ├── mock-model.md         The deterministic prompt grammar
│   ├── upstream-parity.md    What matches upstream, what does not (English)
│   └── upstream-parity.zh-CN.md
├── src/minidsh/              The harness: 36 modules, ~12k lines
│   └── web/index.html        The local workbench UI
├── packages/                 headless / web / desktop distribution entry points
└── examples/minidsh-clock/   A standalone external plugin (installable)
```

---

## 架构

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Products       minidsh CLI      minidsh-web (HTTP)     minidsh-desktop   │
│                                 + web/index.html        (pywebview)      │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │  same Application, one object graph
┌───────────────────────────▼─────────────────────────────────────────────┐
│ Composition    Application (app.py)                                     │
│                  └─ PluginManager: flags → dependency order → activate   │
│                       └─ ServiceContext: named services, one provider    │
└───────────────────────────┬─────────────────────────────────────────────┘
        ┌───────────────────┼───────────────────┬────────────────────┐
        ▼                   ▼                   ▼                    ▼
  ┌───────────┐      ┌────────────┐      ┌────────────┐      ┌──────────────┐
  │ Agent     │      │ Session    │      │ Permissions│      │ Execution    │
  │ loop      │◄────►│ store      │      │ sandbox    │      │ jobs / PTY   │
  │ turn/step │      │ generations│      │ approval   │      │ subagents    │
  │ tools     │      │ surface    │      │ observe    │      │ workflow/PTC │
  └─────┬─────┘      │ compact    │      │ seatbelt   │      └──────────────┘
        │            └────────────┘      └────────────┘
        ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │ Model adapters: MockModel (deterministic) · DeepSeek Messages (SSE)   │
  └──────────────────────────────────────────────────────────────────────┘
```

`app.py` 之上的一切都是产品外壳，之下的一切都是服务。没有哪个工具会导入 Agent，没有哪个产品会
导入另一个产品，依赖箭头只指向内部。

逐文件的详细拆解在 [docs/modules.md](docs/modules.md) 里。

---

## 一次运行的完整生命周期

这就是你可以在轨迹面板里、或者用 `--trace` 看到的顺序：

```text
POST /v1/runs/stream  (or: minidsh run "...")
  │
  ├─ 1. config merge        file config → CLI overrides → frozen AppConfig
  ├─ 2. plugin activation    retry until no pending plugin can start
  ├─ 3. turn/start          emits session_id + run_id (the cancellation handle)
  ├─ 4. context assembly    system prompt sections + runtime context + skill catalog
  │                         + schedule inbox + surface-derived history
  ├─ 5. pressure check      estimate tokens; compact if > 80% of the window
  ├─ 6. model call          SSE deltas → assistant/delta; final → assistant/message
  ├─ 7. tool loop           for each tool call (bounded by max_steps):
  │      ├─ policy check     path inside workspace? sandbox mode allows it?
  │      ├─ approval         ask / never / unavailable → approval/decided
  │      ├─ observation      write/edit require a prior read of this exact version
  │      ├─ execution        process spawn, HTTP, JSON-RPC, or in-process call
  │      └─ tool/result      envelope + error classification
  ├─ 8. persistence         one new immutable generation + index.json manifest
  └─ 9. turn/end            reason: stop | max_steps | aborted | error
```

每一个编号步骤，都是真实框架必须做出一项设计决策的位置。MiniDSH 把每一步都做得显式，并且可以单独
测试。

---

## 功能全景

| 层 | 你学到什么 | 默认 |
|---|---|---|
| **内核** | 插件开关、依赖激活、服务图、组合根 | 开 |
| **智能体核心** | 回合/步骤循环、提示词组装、工具分派、取消 | 开 |
| **工具** | Schema 设计、信封、路径安全、原子写入 | 开 |
| **权限** | 沙箱模式、审批、审计事件、文件观察 | 开 |
| **进程** | 任务隔离、输出游标、进程组、PTY 持久化 | 开 |
| **记忆** | JSONL 世代、恢复、分叉、修复、表面、压缩 | 开 |
| **传输** | HTTP API、SSE、运行关联、背压、重放 | 开 |
| **协作** | 子代理、消息传递、中断、工作流编排 | 关 |
| **生态** | MCP 服务与资源、技能、智能体预设 | 关 |
| **长周期** | 定时投递、持久化目标、自动轮次 | 关 |
| **程序化** | TypeScript `run_code`、Node REPL、SDK RPC 回调工具注册表 | 关 |
| **产品** | CLI、Web 工作台、Desktop 外壳、外部插件发现 | 开 |

所有标记为*关*的功能都是有意关闭的：每一项都会扩大攻击面，或者削弱“小到可以读完”这一性质。
请一次只打开一个。

---

## 功能深入剖析

每一节都遵循同样的结构：**它做什么**、**怎么试**、**观察什么**、**设计说明**、**边界**。

### 1. 插件内核与组合

**它做什么。** `PluginManager` 把一份功能开关字典变成一张活的服务图。插件声明 `provides`、
`requires` 和 `activate`。激活会一直循环，直到没有任何待处理的插件还能继续推进，因此声明顺序
永远不重要——重要的只有依赖关系。

**怎么试。**

```bash
minidsh plugins                          # active / disabled / pending with missing services
minidsh --disable core.jobs plugins      # jobs dependents go 'missing=jobs'
minidsh --disable core.compaction plugins
minidsh --disable llm.mock --disable llm.deepseek plugins   # agent.core stays pending
```

**观察什么。** 关掉一个*服务*和关掉一个*工具*是两回事：工具从 schema 里消失，服务从图里消失，
而依赖它们的插件保持 `pending`，而不是崩溃。

**设计说明。**

- 一个服务名只允许一个提供者；第二次注册会抛错。
- 停用按注册的逆序执行，因此资源能正确地反向释放。
- 关闭的外部插件以 `DisabledExternalPlugin` 占位符注册——它会出现在 `plugins` 输出里，但永远
  不会被导入。
- 没有热重载。图在启动时一次性收敛，之后保持稳定。

**边界。** 不支持运行时替换插件，除预设之外没有作用域化的子图，也没有 YAML 加载器。

---

### 2. 智能体循环

**它做什么。** `Agent.run()` 就是一个回合。在一个回合内，循环交替执行模型调用和工具调用，直到
`max_steps`（默认 4）。它负责顺序、预算、持久化和失败处理——但不负责智能。

**怎么试。**

```bash
minidsh run "read README.md" --trace
minidsh run "delegate analysis | read README.md" \
  --enable core.subagents --enable tools.subagent --enable tools.subagent-control
```

**观察什么。** `turn/start` → `assistant/message` → `tools/pre-execute` →
`tools/post-execute` → `assistant/message` → `turn/end`。一个回合可能以命中 `max_steps` 结束，
那是一个预算结果，不是错误。

**设计说明。**

- 取消会在模型、工具和 shell 三类边界上被检查。`RunCancelled` 会向上传播。
- 子代理的完成以一条 `subagent-settled` 用户消息到达，由后续某个步骤消费，而不是由单独的调度器
  处理。
- 流式增量携带 `session_id`、`run_id`、`turn`、`step` 和 `attempt`，因此消费方可以丢弃失败尝试
  已经写出的草稿。

**边界。** `max_steps` 按 Agent 实例固定，不是自适应的。没有规划器，没有反思步骤；除了模型自己
的决定之外，失败的工具调用不会被自动重试。

---

### 3. 系统提示词与运行时上下文

**它做什么。** 用若干有序段落拼出提示词：一个固定的身份块、逐工具的指引，以及一份运行时上下文
快照。

**怎么试。**

```bash
minidsh prompt
minidsh --disable tools.edit prompt        # the "prefer edit" clause disappears with the tool
minidsh --sandbox-mode workspace-write --yes prompt
```

**观察什么。** 运行时上下文是一条**独立的用户消息**，它不属于静态系统提示词，并且会在恢复
时重新生成。这就是为什么恢复后的会话看到的是*当前*策略，而不是它启动时的策略。

**设计说明。**

- 身份是固定的：`You are an AI agent powered by DeepSeek Harness.`
- 工具指引由工具插件注册，并带显式顺序（bash 1000、read 1100、write 1200、edit 1300、glob
  1400、jobs 1600）。
- 只有在 `edit` 启用时，`write` 的指引才会多出 “and prefer edit for targeted changes” 这一句。
- 关掉一个工具，会在同一步里同时移除它的 schema *和*它的说明。让模型去用一个它根本调用不了的
  工具，是框架的 bug，不是模型的 bug。

**边界。** 段落顺序在代码里固定；除人设前缀/后缀之外，没有按预设编写提示词的能力。

---

### 4. 文件工具

**它做什么。** 实现 `read`、`glob`、`write` 和 `edit`，其名称、schema、信封和限制都与上游兼容。

**怎么试。**

```bash
minidsh run "read README.md"
minidsh run "list"
minidsh --sandbox-mode workspace-write --yes run "write demo.txt: hello"
minidsh --sandbox-mode workspace-write --yes run "edit demo.txt: replace hello with world"
```

**观察什么。**

| 工具 | 限制与行为 |
|---|---|
| `read` | 2000 行、每行 2000 字符、总计 50 KiB，带行号，`<path>/<type>/<content>` 信封 |
| `glob` | 最多 100 条结果，包含隐藏文件，排除 VCS 目录，按修改时间排序 |
| `write` | 创建或覆盖，返回 `Created file` / `Updated file`，原子替换 |
| `edit` | 字面匹配，默认要求唯一，可选项 `replace_all`，替换内容中的 `$` 不做特殊处理 |

**设计说明。**

- 覆盖或编辑要求**在同一会话中先读取过同一版本**。如果期间磁盘上的文件变了，观察即失效，操作
  会被拒绝。
- 这条规则，就是“模型编辑了一个文件”和“模型覆盖掉了别人正在编辑的文件”之间的差别。
- 路径相对工作区解析；在 `workspace-write` 下，`..` 和符号链接逃逸都会被拒绝。

**边界。** 没有 patch/diff 工具，没有二进制编辑，没有多文件事务。

---

### 5. 沙箱策略、审批与观察

**它做什么。** 三个相互独立、可以组合的控制手段：

| 控制手段 | 取值 | 它回答的问题 |
|---|---|---|
| 沙箱策略 | `read-only`, `workspace-write`, `danger-full-access` | 这个操作*可以碰哪里*？ |
| 审批策略 | `ask`, `never` (+ `--yes`) | *谁*授权了这个操作？ |
| 观察 | 按会话记录的文件版本 | 这次写入是基于当前内容吗？ |

**怎么试。**

```bash
# Default: refused by policy
minidsh run "write denied.txt: hello"

# Widen the policy but refuse everything at the approver
minidsh --sandbox-mode workspace-write --approval-policy never run "write denied.txt: hello"

# No workspace restriction, no approval prompts
minidsh --sandbox-mode danger-full-access run "write full.txt: hello"
```

**观察什么。** `minidsh session <id>` 会显示成对的 `approval/asked` 和 `approval/decided`
事件，包含请求 id、工具名、调用 id 和结果（`allowed-once`、`rejected`、`unavailable`、
`cancelled`）。

**设计说明。**

- 非交互运行且没有应答者时返回 `unavailable`——它不会悄悄放行。
- 取消审批会立即记录 `cancelled`，并丢弃迟到的应答结果。
- 观察随会话一起持久化，并在恢复时还原，因此“先读后写”能跨重启存活。
- HTTP 可以按会话放宽或收紧：`POST /v1/sessions/<id>/sandbox` 和 `/approval-policy`。运行时
  上下文和进程边界读取的是同一个覆盖值。

**边界。** 审批是一种人在环路中的控制手段，本身不是安全边界。运行不可信内容时，请把它和下面的
内核沙箱搭配使用。

---

### 6. 内核沙箱（macOS Seatbelt）

**它做什么。** 为 bash、后台任务和持久 PTY 启用真正的操作系统级强制执行。拒绝来自子进程，而不是
来自框架。

**怎么试。**

```bash
minidsh --enable core.kernel-sandbox run "run printf denied > blocked.txt"
```

**观察什么。** 一个真正的内核错误，例如：

```text
/bin/bash: blocked.txt: Operation not permitted
[exit code: 1]
```

**设计说明。**

- `read-only`：允许执行和读取，拒绝写入。
- `workspace-write`：只允许工作区、托管的任务目录和平台临时目录。
- `danger-full-access`：隔离关闭。
- 持久终端打开期间不能改变沙箱模式；请先把它杀掉。

**边界。** 仅限 macOS。没有 Linux 的 Landlock/bubblewrap，也没有 Windows 的对应实现。这是操作
系统隔离的教学切片，不是经过加固的多租户运行时。

---

### 7. Shell、后台任务与 PTY

**它做什么。** 两种 shell 模型：

- **`bash`（默认）：** 每次调用一个全新的 `bash -c`。cwd、变量和函数都不会保留。
- **`bash --persistent`：** 每个会话一个运行在 Unix PTY 下的交互式 `/bin/bash`，状态在多次调用
  之间保留。

再加上任务系统：带独立 stdout/stderr 游标的后台进程。

**怎么试。**

```bash
# One-shot
minidsh --sandbox-mode workspace-write --yes run "run printf 'hello'"

# Background job
minidsh --sandbox-mode workspace-write --yes run "run in background sleep 10"

# Persistent shell (replaces tools.bash automatically)
minidsh --enable tools.bash-persistent --sandbox-mode workspace-write --yes \
  run "run export DEMO=hello; cd /tmp"
```

**观察什么。**

- 前台超时不会杀掉进程；它会把**同一个任务移到后台**，并返回
  `[still running after ...; moved to background job bash-1]`。
- `job_output` 通过游标消费新输出；它不会重复打印你已经读过的内容。
- 每个会话最多同时有 10 个运行中/停止中的任务。
- 内存环形缓冲保留 256 KiB，首次终端读取之后缩到 16 KiB；完整输出写入磁盘，并报告路径。
- 持久 shell 用随机的起始/结束标记包住每条命令，并以 `[Command finished with exit code N]`
  结尾。

**设计说明。**

- 环境变量清洗会移除所有匹配 `*KEY*`、`*TOKEN*`、`*SECRET*`、`*PASSWORD*` 和 `DSH_*` 的变量，
  然后注入托管的 `DSH_HOME` 和 `DSH_SESSION_ID`。凭据不会意外泄漏到子进程里。
- 杀掉任务会杀掉它的**进程组**，因此 shell 脚本无法留下孤儿进程。
- PTY 会在超时、取消、`exit`、HTTP kill 或关闭时重置。

**边界。** 仅限 Unix PTY（没有 Windows ConPTY）。CLI 是短生命周期进程，因此后台任务和 PTY 会在
退出时被回收——需要长时间运行时请用 `serve`。

---

### 8. 会话：世代、恢复、分叉与修复

**它做什么。** 一个事件溯源、世代不可变的会话存储。

**布局：**

```text
.minidsh/sessions/<session-id>/
├── index.json                       manifest: current generation, path, SHA-256
└── generations/
    ├── 00000001.jsonl               full snapshot, never overwritten
    └── 00000002.jsonl               next snapshot after an append
```

**怎么试。**

```bash
minidsh sessions
minidsh transcript <session-id>       # human-readable projection (append-only view)
minidsh resume <session-id> "read README.md"
minidsh fork <session-id>             # independent branch, parent untouched
minidsh fork <session-id> --at-seq 42 # branch from an earlier point
minidsh compact <session-id>          # manual compaction checkpoint
minidsh repair <session-id>           # fix truncated tail, open steps/turns, missing results
minidsh migrate <session-id>          # upgrade legacy formats
```

**观察什么。**

- `resume` 会还原消息、回合编号和文件观察。运行时上下文则依据**当前**配置重新生成。
- `fork` 复制到某个边界为止的历史，并且从不修改父会话。
- `compact` 改变的是投影，不是事件日志。
- `repair` 会先写一份备份，然后如实收尾。

**设计说明。**

- 每个事件在读取时都会校验：会话身份、连续的 `seq`、事件类型、负载形状、UTF-8。
- 迁移和修复在发布新世代之前，会把原始字节保存到
  `.minidsh/sessions/backups/<id>.<sha256>.jsonl`。
- 清单更新使用临时文件 + `fsync` + `os.replace`；读取时会重新校验哈希，因此一旦发生外部篡改，就会按
  失败即关闭处理。
- `resume` 和 `compact` 在**整个回合**期间持有 POSIX `flock` 写租约。读取不加锁。锁文件的
  inode 稳定，进程死亡时由内核释放。
- 崩溃修复会区分*从未开始*（`TOOL_NOT_STARTED`）和*已开始但结果未知*
  （`TOOL_OUTCOME_UNKNOWN`），并警告不要盲目重试有副作用的操作。

**边界。** 这是用于学习的 v0/v1/v2 格式，不是上游的多世代 Zstandard 格式。没有 Windows 写锁。

---

### 9. 表面替换与工具结果裁剪

**它做什么。** 把*发生了什么*（仅追加的事件）和*模型当前看到什么*（一个折叠后的视图，称为表面）
分开。

**怎么试。**

```bash
minidsh surface <session-id>
minidsh --enable core.tool-result-pruner prune <session-id>
```

**观察什么。** 一个 `tool/result` 替换事件，带 `surfaceOp={op: replace, startSeq, endSeq}` 和
列出被遮蔽节点的 `sourceEventSeqs`。原始事件仍在日志里；`transcript` 依然能看到它。

**设计说明。**

- 替换会校验区间是否存在、是否有序、是否只包含当前节点，以及来源既不能重复也不能来自未来。
- 工具结果替换**只能**改变 `content`——绝不能改工具名或调用 id。
- 第一条系统提示词只能通过一次系统替换来改写。
- 当内容超过 8192 的阈值时，裁剪器保留头部 4096 + 标记 + 尾部 1024 个 Unicode 码点，可通过
  `MINIDSH_PRUNE_*` 配置。
- 裁剪是手动的，默认关闭，因此不会有任何东西在你背后变化。

**边界。** 只支持消息级和工具结果级的替换。不支持任意的插件投影，也没有上游完整的事件类型体系。

---

### 10. 压缩

**它做什么。** 当估算上下文超过窗口的 80% 时，用一份摘要替换对话的中间部分，同时保留尾部。

**怎么试。**

```bash
minidsh --context-window 100 run "test automatic compaction"
minidsh compact <session-id>
```

**观察什么。** `compaction/start` 和 `compaction/end` 事件；摘要包在 `<compacted-summary>` 里；
原始事件不受影响。

**设计说明。**

- 触发条件：`context_window` 的 80%（默认 1,048,576）。
- 保留比例：大约 16% 的消息，从尾部开始计数。
- 压缩提示词是上游风格的结构化摘要（主要请求、概念、文件、错误、挂起的任务、当前工作、
  下一步、关键上下文）。
- Token 计数是启发式估算，不是真正的分词器。它有意偏保守。

**边界。** 没有精确分词器，没有图像 token 计价，切换模型时也不会重新压缩。

---

### 11. 流式输出、流记录与 SSE 重放

**它做什么。** 三件相互独立、但很容易混淆的事：

| 特性 | 开关 | 用途 |
|---|---|---|
| 实时增量 | `core.streaming` | 为实时 UI 文本产生 `assistant/delta` 事件 |
| 流记录 | `core.stream-recording` | 把原始分片压缩后存进 `assistant/message` 负载 |
| SSE 重放 | `core.sse-replay` | 进程内环形缓冲 + `Last-Event-ID` 续传，应对连接中断 |

**怎么试。**

```bash
minidsh stream <session-id>            # expand recorded chunks with seq/turn/step
curl -H 'Last-Event-ID: 42' 'http://127.0.0.1:3080/v1/runs/stream?session_id=<id>'
```

**观察什么。**

- 增量携带 `attempt`；重试时，消费方应当清掉失败尝试的草稿。
- 记录下来的分片是 `text-chunks`、`reasoning-chunks`、`tool-call-chunks`，带 `time0` 和
  `dt`；usage/block-start/finish 保持原始形态。
- SSE broker 保留 2048 个事件；慢速监听者会被丢弃，而不会拖住智能体。

**设计说明。**

- 每个 SSE 监听者都有一个上限 256 的事件队列。溢出会关闭该监听者并递增 `dropped_streams`；
  运行继续，并且仍然会持久化结果。
- 重放只选择同一会话中 id 大于断点的事件，然后挂到实时流上。它绝不会启动一个重复的智能体。
- 记录的分片一旦损坏，会话读取就会失败——要么完整保真，要么什么都不要。

**边界。** 重放是进程内的；宿主重启会清空环形缓冲。记录的分片不是全局 SSE 序号。

---

### 12. 提问与待办

**它做什么。** `ask_user_question` 挂起当前回合等待人类输入；`todo_write` 维护一份会话级任务
列表。

**怎么试。**

```bash
minidsh --enable tools.ask-user --enable tools.todo run "ask Continue?"
minidsh --enable tools.todo run "todo Finish the lab"
minidsh todos <session-id>
```

**观察什么。**

- `selected` 为空数组且没有 `custom`，意味着**跳过**，而不是批准。
- 启用 `tools.ask-user-timed` 时，默认等待 120 秒；超时返回 `pending=true`，并把问题留在
  队列中。
- 迟到的回答会以用户消息注入，来源类型为 `answer_to_pending_question`，并携带原始调用 id。
- 待办是整表替换，文本必须唯一且非空；除非 `MINIDSH_TODO_ALLOW_PARALLEL=1`，否则只能有一个
  `in_progress`。

**设计说明。** 人类输入是异步的、不可信的。它和其它所有外部信号一样，会经历校验、拥有生命周期，并
留下一条审计痕迹。

---

### 13. Web 抓取与搜索

**它做什么。** 带提示词注入意识的有界 Web 访问。

**怎么试。**

```bash
minidsh --enable tools.web-fetch run "fetch https://example.com"
MINIDSH_WEB_SEARCH_URL=http://127.0.0.1:9000/search \
  minidsh --enable tools.web-search run "search agent harness"
```

**观察什么。**

- 抓取到的内容会被包上一层明确的“外部内容不可信”声明，外加 URL。
- HTML 的 script、style 和隐藏节点会被剥离；嵌套过深或无法转换的页面不会返回原始标记。
- 私有地址、回环地址和链路本地地址会被拒绝，并且每次重定向都会重新检查。
- 搜索最多支持 10 条查询、10 个来源、800 字符的摘录，以及 Markdown 引用。

**设计说明。** 默认超时和字节上限是工具契约的一部分，而不是埋在配置里的某个设置。预算应当紧挨着
能力的定义。

**边界。** 这不是完整的 SSRF 防御（DNS 不会在连接时固定），不是完整的 Turndown/GFM，搜索提供方
是一个很小的 JSON 协议，而不是厂商路由。

---

### 14. 子代理

**它做什么。** 创建拥有自己的会话、名字和生命周期的子智能体。

**怎么试。**

```bash
minidsh --enable core.subagents --enable tools.subagent --enable tools.subagent-control \
  run "delegate analysis | read README.md"
```

**观察什么。**

- `subagent` 立即返回 `agent-1`；子代理在自己的会话中运行。
- 完成信息以一条 `subagent-settled` 用户消息到达父会话。
- `subagent_fork` 还会额外继承父会话直到最后一个完整 `turn/end` 的历史。
- `send_message` 唤醒空闲的子代理，或为运行中的子代理排队输入；`interrupt_agent` 取消整棵
  子树；`list_agents` 和 `wait_agent` 用于发现和协调。

**设计说明。**

- 深度上限 `MINIDSH_SUBAGENT_MAX_DEPTH` 默认为 1；设为 0 则完全禁用委派。
- 名字在同一个父代理下唯一；根代理叫 `lead`。
- 子代理继承父代理的预设，因此能力范围限定可以组合生效。

**边界。** 仅限进程内。没有 ACP、Codex、Claude Code 或远程 SDK 后端；没有按子代理选择模型；
子代理的 `cwd` 会被记录，但文件工具仍然使用根工作区。

---

### 15. 工作流引擎

**它做什么。** 在隔离的 Node 进程里运行 JavaScript 编排脚本，其中每次 `agent()` 调用都通过
JSON-RPC 回到 Python，变成真正的子代理。

**怎么试。**

```bash
minidsh --enable core.subagents --enable core.workflow --enable tools.workflow \
  run "workflow hello|world"

minidsh --enable core.subagents --enable core.workflow --enable tools.workflow \
  run "workflow background hello|world"
```

```javascript
phase('audit')
const results = await pipeline(args.items,
  async item => await agent(item, { schema: args.schema }),
  async (answer, item, index) => ({ item, index, answer }))
return results
```

**观察什么。** `workflow/start`、`workflow/phase`、`workflow/log`、`workflow/agent-start`、
`workflow/agent-end`、`workflow/end` 事件；后台运行会返回一个可配合 `job_output` 使用的任务
id。

**设计说明。**

- 沙箱里没有 `require`、`process`、`fs`、`net` 或定时器。`meta` 和 `args` 以 JSON 数据的形式
  注入，永远不会被求值。
- 以对象为根的 JSON Schema 子集：
  `type/properties/required/additionalProperties/items/enum/const/oneOf`。子代理失败或 schema
  不匹配会变成 `null`；钩子误用则会中止整次运行。
- `MINIDSH_WORKFLOW_MAX_AGENTS` 为 1000，并发默认 `min(32, cpu_count)`，每次组合子调用最多
  4096 个条目。
- 宿主重启后，已完成的工作流可以从磁盘恢复；未完成的会被标记为
  `failed: interrupted: host restarted`，并带一条 `cause=recovery` 审计事件。

**关于形态的设计说明。** “工作流”是确定性编排——控制图在执行前就已知。“智能体”是模型驱动的
控制流。判断一项任务需要哪一种，是核心的架构能力。

**边界。** `vm` 隔离是学习用的边界，不是加固过的 PTC/操作系统沙箱。没有按智能体的模型路由。

---

### 16. MCP 客户端

**它做什么。** 通过 MCP 与 stdio 和 Streamable HTTP 服务通信，把它们的工具注册到
`mcp__<server>__<tool>` 之下，并暴露资源。

**怎么试。** 加到一份 JSON 配置里：

```json
{
  "features": { "core.mcp": true, "tools.mcp-resources": true },
  "mcp_servers": {
    "local": {
      "transport": "stdio",
      "command": "python3",
      "args": ["-u", "server.py"],
      "env": { "SERVER_VALUE": "${credential:SERVER_VALUE}" }
    },
    "remote": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:3000/mcp",
      "headers": { "Authorization": "Bearer ${credential:MCP_TOKEN}" }
    }
  }
}
```

```bash
minidsh --config mcp.json tools
curl http://127.0.0.1:3080/v1/mcp
```

**观察什么。**

- 真实的 JSON-RPC `initialize` / `notifications/initialized`，以及分页的工具发现。
- `${credential:NAME}` 从宿主环境解析；失败时会脱敏已经解析出的值。
- 名称冲突会拒绝整个新的工具世代，并保留旧工具不受影响。
- 服务的 `isError` 会变成显式的工具失败，而不是静默的空结果。
- 资源二进制块只报告其 base64 长度——二进制负载永远不会进入模型历史。

**设计说明。** MCP 边界正是信任、命名、凭据和取消交汇的地方。请对照规范来读这个模块——它存在的
意义就在这里。

**边界。** 本实现遵循经典握手。2026-07-28 的 MCP 修订把协议变成了无状态的（不再有
`initialize`，不再有 `Mcp-Session-Id`，改为 `server/discover`、MRTR）；见
[与 2026 年智能体生态的对应关系](#与-2026-年智能体生态的对应关系)和对齐报告。目前没有 OAuth，
没有资源订阅，也还没有自动重连预算。

---

### 17. 技能

**它做什么。** 指令的渐进式披露。一个技能是一个含有 `SKILL.md` 的目录，或者一个带 frontmatter
的扁平 Markdown 文件。只对外公布名字和一行描述；正文按需加载。

**怎么试。**

```text
skills/
└── review/
    └── SKILL.md
```

```markdown
---
name: review
description: Review code carefully
---
Read the relevant files before suggesting changes.
```

```bash
minidsh --enable core.skills --enable tools.skill skills
minidsh --enable core.skills --enable tools.skill run "skill review"
```

**观察什么。** 一条常驻的 `skill-catalog` 用户消息列出可用的技能；变更会追加一份替换目录；
删除所有技能会显式退役旧名字。`skill` 工具每次调用都会重新读取完整正文，并包在
`<skill_content>` 里。

**设计说明。**

- 根目录按顺序为：项目 `.dsh/skills`、`.agents/skills`、配置的目录、`~/.dsh/skills`、
  `~/.agents/skills`、可选的随包根目录。
- `disable-model-invocation: true` 会隐藏目录条目和工具，但仍允许用户显式使用 `/name` 手势；
  `user-invocable: false` 会阻止该手势。
- 未知路径和工具结果都无法伪造用户手势。

**边界。** Frontmatter 是标量解析器，不是完整 YAML。没有文件监听，没有随包内置的 Office 技能，
也没有上游的工作目录作用域链。

---

### 18. 定时任务

**它做什么。** 宿主级定时器，按计划把一条提示词投递到某个会话。

**怎么试。**

```bash
minidsh --enable core.schedule --enable tools.schedule \
  run 'schedule create {"title":"Deploy check","prompt":"Check deployment status","rrule":"DTSTART:20990101T090000Z\nRRULE:FREQ=DAILY;COUNT=1","target":"this_session"}'
```

**观察什么。**

- 任务属于宿主，而不属于某个会话，因此可以跨会话列出。
- 目标：`this_session`（复用）、`new_once`（只创建一次）、`new`（每次投递都新建会话）。
- 时间形式：`after`、`at`、`every`、`daily`、`weekly`、`cron`（五个字段）、`rrule`（子集）。
- `schedule/inbox` 确认的是消息**已持久化**，而不是模型已经完成。
- 停机后错过的触发，只会投递最近的一次。
- 历史保留 200 次投递 / 30 天；最小间隔 60 秒。

**设计说明。** 调度是一个持久性问题。幂等性、补投策略，以及“已投递”与“已完成”的区分，就是设计
的全部。

**边界。** 不支持 RRULE 的 `BYWEEKNO`/`BYYEARDAY`，`BYHOUR`/`BYMINUTE`/`BYSECOND` 只接受
单值，只有一种存储后端。

---

### 19. 目标与自动轮次

**它做什么。** 附加在会话上的持久化目标，可选地自动续跑。

**怎么试。**

```bash
minidsh --enable core.goals --enable tools.goal run "goal create finish migration|4"
minidsh --enable core.goals --enable tools.goal --enable core.goal-rounds run "goal create finish migration|4"
minidsh --enable core.goals goal <session-id>
minidsh --enable core.goals goal <session-id> resume
```

**观察什么。**

- 每个会话最多一个当前目标；`goal/change` 保存目标、状态、修订号、轮次计数和阻塞原因。
- 每次更新都要求精确匹配 `goal_id` + `revision`——这是乐观并发，不是最后写入者获胜。
- 恢复、分叉或重启之后，目标总是处于**解除武装**状态；必须由人来恢复它。
- 自动进入 `blocked` 需要连续三轮，并给出具体原因。

**设计说明。** 这是仓库里最微妙的一个模块。长周期自主性需要显式状态、预算，以及一个刻意的重新
武装步骤；缺少这些，“一直做到完成为止”就是一个没有退出条件的循环。

**边界。** 没有完成度评估器，没有子树空闲聚合，也没有独立的调度器。

---

### 20. 程序化工具调用（PTC）

**它做什么。** 在 PTC 模式下，模型只看到 `run_code`；其它所有工具都变成 TypeScript 程序里的
`await tools.name(args)`。

**怎么试。**

```bash
minidsh --enable core.ptc --enable tools.run-code \
  run 'run code {"code":"const text: string = await tools.read({file_path: \"README.md\"}); return text.length","description":"Measure README"}'
```

**观察什么。**

- 调用仍然经过同一个 `ToolRegistry`，因此审批、取消和事件照常生效。
- `per-call` 在一个新进程里执行异步函数体；`persistent` 保留一个 Node REPL，按会话保存顶层
  变量。
- 只读子调用并行执行（默认 10 路）；有副作用的调用独占执行。
- 默认值：120 秒超时、600 秒上限、64 MiB 输出、3000 ms 停止宽限、128 个挂起、每分钟 1024 次
  子调用。
- 错误类别：`timeout`、`abort`、`exception`、`protocol`、`worker-exit`、`output-limit`、
  `invalid-output`。

**设计说明。** 代码执行把多轮工具往返压缩成一个模型回合——这是很大的延迟收益——代价是配额、
隔离和更难的调试。这个权衡的两面都在这里实现了。

**边界。** 没有 Acorn 源码转换，没有完整的协作式撤销/进程树清理，也没有 PTC 图像投影。

---

### 21. 智能体预设

**它做什么。** 把会话绑定到一个能力画像：有哪些工具存在，以及用什么人设前缀/后缀修饰提示词。

**怎么试。**

```json
{
  "features": { "core.agent-presets": true },
  "default_preset": "standard",
  "presets": {
    "standard": { "plugins": ["tools.read", "tools.glob"] },
    "minimal": {
      "plugins": [],
      "persona": { "prefix": "Minimal agent", "complete": true, "includeRuntimeContext": false }
    },
    "coder": { "plugins": ["tools.run-code", "tools.read"], "persona": { "prefix": "Coder", "suffix": "End" } }
  }
}
```

```bash
minidsh --enable core.agent-presets presets
minidsh --enable core.agent-presets preset <session-id> minimal
```

**观察什么。** `preset/selected` 持久化这次选择。只有在第一个模型回合之前才允许切换；之后会被
拒绝。分叉和子代理继承父代理的预设。如果预设不在历史里，会按失败即关闭处理，而不是回退到默认值。

**设计说明。** 能力范围限定是产品特性，不是优化。一个评审智能体不该带着它永远不会用到的 shell；
而 `run_code` 会在预设移除某个工具的瞬间就失去对该工具的访问权——因为 PTC 使用的是同一个限定
作用域的服务上下文。

**边界。** 一个“名字到插件”的映射，不是带热重载的上游 YAML 加载器。

---

### 22. HTTP 宿主与 Web 工作台

**它做什么。** 一个本地 HTTP + SSE 宿主，以及一个单文件的工作台 UI。

**怎么试。**

```bash
minidsh serve --port 3080
open http://127.0.0.1:3080
```

**观察什么。**

- `POST /v1/runs/stream` 启动一次运行；`turn/start` 事件携带用于取消的 `run_id`。
- 并发运行按 `run_id` 隔离；两条一模一样的提示词仍然会得到不同的会话。
- 一个会话在同一时刻只接受一个经由 HTTP 的写回合。
- UI 渲染结构化轨迹（模型/工具/任务/错误分类），实时流式显示助手文本，并把待答问题显示为模态
  表单。

**设计说明。**

- 默认绑定 `127.0.0.1`。在没有 `MINIDSH_TOKEN` 的情况下绑定非回环地址会拒绝启动；客户端随后
  需发送 `Authorization: Bearer <token>`。
- 工作台没有构建步骤。如果 UI 需要一个私有捷径，那说明 API 缺了点什么——这是一个有用的压力
  测试。
- 中/英切换和明/暗主题持久化在 `localStorage` 中。

**边界。** 这是学习用的 API，不是多租户网关。浏览器 UI 不会发送自定义的 Authorization 头。

---

### 23. 外部插件

**它做什么。** 通过标准的 `minidsh.plugins` 入口点组发现插件。

**怎么试。**

```bash
python3 -m pip install -e . -e ./examples/minidsh-clock
minidsh plugins                        # example.clock shown as disabled
minidsh --enable example.clock tools   # now it provides a real tool
```

**观察什么。** 禁用的插件只暴露发行版元数据——模块永远不会被导入。启用则是另一道显式的信任决定。

**设计说明。** 入口点可以导出一个 `Plugin` 实例、一个子类，或者一个工厂函数。入口点名称必须
等于 `plugin.name`，并且不得遮蔽内置功能。卸载了包却没有删除它的配置条目，会在启动时按失败即关闭
处理。

**边界。** 没有热重载，没有带版本的插件 API，插件代码也没有沙箱——启用一个插件，就等于把宿主
进程的权限交给它。

---

### 24. 产品发行版

**它做什么。** 把产品外壳和核心拆开。

| 包 | 命令 | 额外运行时 |
|---|---|---|
| `minidsh` / `minidsh-headless` | `minidsh`, `minidsh-headless` | 无（仅标准库） |
| `minidsh-web` | `minidsh-web` (defaults to `serve`) | 无 |
| `minidsh-desktop` | `minidsh-desktop` (defaults to `desktop`) | `pywebview` |

```bash
python3 -m pip install -e . -e ./packages/minidsh-web
minidsh-web --port 3080

python3 -m pip install -e '.[desktop]' -e ./packages/minidsh-desktop
minidsh-desktop
```

**观察什么。** 导入 CLI、列出插件，或者运行无头/Web 模式时，永远不会导入 `pywebview`。只有
Desktop 命令会报告缺少 GUI 依赖，而且只在真正用到它的时候。

**设计说明。** 重量级的可选依赖应该留在产品边缘。一个为了跑一条 Bash 命令就必须依赖浏览器工具包的
框架，是打包失败。

---

### 25. 真实 DeepSeek 适配器

**它做什么。** 实现 DeepSeek Messages 协议：SSE 流式输出、带重放签名的思考块、工具调用、
图像、重试，以及模型列表。

**怎么试。**

```bash
export DEEPSEEK_API_KEY='...'
minidsh --disable llm.mock --enable llm.deepseek run "read README.md"

minidsh --disable llm.mock --enable llm.deepseek models
minidsh --disable llm.mock --enable llm.deepseek files
```

**观察什么。**

- 请求路径是 `/v1/messages`（当 base URL 已经以 `/v1` 结尾时是 `/messages`）。
- 请求头：`x-api-key`、`anthropic-version: 2023-06-01`。
- `deepseek-messages` 重放信封和推理签名会被保留；切换提供方会丢弃外来的签名。
- 重试会发出 `llm/retry`，并以带抖动的指数退避进行，同时遵守有界的 `Retry-After`。
- 图像在 `auto` 模式下优先使用 Files API，失败则回退到内联 base64；JSONL 只存路径、媒体
  类型、大小和 SHA-256。

**设计说明。** 大多数“第一回合能跑、恢复后就崩”的 bug 都出在适配器的协议记账里：内容块顺序、
签名重放、用量统计和错误分类。把这些逻辑放在一个模块里，才让它可审计。

**边界。** 本仓库尚未进行真实在线验收；本地 Messages/SSE 端到端测试做过。这个适配器是学习
切片，不构成兼容性承诺。绝不要把本宿主暴露到公网。

---

## 配置参考

### 文件配置

`config.example.json` 包含所有开关。文件配置先合并；命令行上的 `--enable` / `--disable`
随后应用，因此 CLI 永远优先。

```json
{
  "session_dir": ".minidsh/sessions",
  "sandbox_mode": "read-only",
  "approval_policy": "ask",
  "approve_all": false,
  "context_window": 1048576,
  "mcp_servers": {},
  "skill_dirs": [],
  "skill_include_default_roots": true,
  "presets": {},
  "default_preset": "standard",
  "features": { "core.mcp": false, "tools.web-fetch": false }
}
```

### 功能开关

**核心服务**

| 开关 | 默认 | 作用 |
|---|---|---|
| `core.system-prompt` | 开 | 身份块、工具指引段落、运行时上下文 |
| `core.tools` | 开 | 工具注册表与执行流水线 |
| `core.sandbox-policy` | 开 | 工作区边界与沙箱模式 |
| `core.approval` | 开 | 审批服务与审计事件 |
| `core.fs-observation` | 开 | 按会话跟踪文件版本 |
| `core.jobs` | 开 | 进程生命周期、输出游标、隔离 |
| `core.compaction` | 开 | Token 估算、摘要、检查点 |
| `core.cancellation` | 开 | 运行取消的传播 |
| `core.streaming` | 开 | 增量模型输出 |
| `core.stream-recording` | 开 | 压缩后的原始分片持久化 |
| `core.sse-replay` | 开 | 进程内 SSE 历史环形缓冲 |
| `core.pty` | 开 | 供持久 shell 使用的 Unix PTY 服务 |
| `core.kernel-sandbox` | **关** | macOS Seatbelt 隔离 |
| `core.tool-result-pruner` | **关** | 手动裁剪工具结果 |
| `core.user-questions` | 开 | 提问服务与待答队列 |
| `core.web` | 开 | Web 抓取/搜索服务 |
| `core.subagents` | **关** | 本地子代理 |
| `core.workflow` | **关** | Node 工作流引擎 |
| `core.mcp` | **关** | MCP 客户端与管理器 |
| `core.skills` | **关** | 技能注册表与发现 |
| `core.schedule` | **关** | 宿主调度器 |
| `core.goals` | **关** | 持久化目标 |
| `core.goal-rounds` | **关** | 目标自动轮次 |
| `core.ptc` | **关** | 程序化工具调用 |
| `core.agent-presets` | **关** | 按会话的能力画像 |
| `agent.core` | 开 | 智能体循环本身 |

**工具**

| 开关 | 默认 | 注册的工具 |
|---|---|---|
| `tools.read` | 开 | `read` |
| `tools.write` | 开 | `write` |
| `tools.edit` | 开 | `edit` |
| `tools.glob` | 开 | `glob` |
| `tools.bash` | 开 | 一次性的 `bash` |
| `tools.bash-persistent` | **关** | 持久的 `bash`（会自动禁用 `tools.bash`） |
| `tools.jobs` | 开 | `job_output`, `job_list`, `job_kill` |
| `tools.ask-user` | **关** | `ask_user_question` |
| `tools.ask-user-timed` | **关** | 带待答回复的限时提问 |
| `tools.todo` | **关** | `todo_write` |
| `tools.web-fetch` | **关** | `web_fetch` |
| `tools.web-search` | **关** | `web_search` |
| `tools.subagent` | **关** | `subagent` |
| `tools.subagent-fork` | **关** | `subagent_fork` |
| `tools.subagent-control` | **关** | `send_message`, `interrupt_agent`, `list_agents`, `wait_agent` |
| `tools.workflow` | **关** | `workflow` |
| `tools.mcp-resources` | **关** | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` |
| `tools.skill` | **关** | `skill` |
| `tools.schedule` | **关** | `schedule_create/list/view/edit` |
| `tools.goal` | **关** | `get_goal`, `create_goal`, `update_goal` |
| `tools.run-code` | **关** | `run_code` |

**存储与模型**

| 开关 | 默认 | 作用 |
|---|---|---|
| `session.jsonl` | 开 | JSONL 世代、迁移、恢复、加锁 |
| `llm.mock` | 开 | 确定性的离线模型 |
| `llm.deepseek` | **关** | 真实的 Messages 适配器 |
| `llm.retry` | 开 | 带退避的有界重试 |
| `llm.model-listing` | 开 | 模型列表查询与缓存 |
| `llm.files` | 开 | Files API 的上传、复用、列表 |

把模型全部禁用后，`agent.core` 会保持 `pending`，因为它依赖的 `llm` 缺失了——这正是依赖模型
按设计工作的样子。

### 环境变量

**模型与传输**

| 变量 | 默认值 | 含义 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | 凭据；只从环境读取，绝不写入磁盘 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/anthropic` | Messages 根地址 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 模型 id |
| `DEEPSEEK_REASONING_EFFORT` | `high` | `off` / `low` / `high` / `max` |
| `DEEPSEEK_MAX_TOKENS` | `256000` | 响应上限 |
| `DEEPSEEK_STREAM_IDLE_TIMEOUT_MS` | `300000` | SSE 空闲超时 |
| `DEEPSEEK_MODELS_TIMEOUT_MS` | `5000` | 模型列表超时 |
| `DEEPSEEK_MODELS_MAX_AGE_MS` | `600000` | 列表缓存 TTL（`0` = 每次都刷新） |
| `DEEPSEEK_RETRY_MODE` | `normal` | `normal` 或 `always` |
| `DEEPSEEK_RETRY_MAX` | `5` | 最大重试次数 |
| `DEEPSEEK_RETRY_INITIAL_DELAY_MS` | `500` | 首次退避时长 |
| `DEEPSEEK_RETRY_MAX_DELAY_MS` | `10000` | 退避上限 |
| `DEEPSEEK_RETRY_JITTER_RATIO` | `0.1` | 对称抖动 |
| `DEEPSEEK_IMAGE_MODE` | `auto` | `auto` / `inline` / `required` |
| `DEEPSEEK_FILES_API_TIMEOUT_MS` | `60000` | Files API 超时 |
| `DEEPSEEK_FILE_EXPIRY_SECONDS` | `604800` | 上传有效期（3600–2592000） |

**框架行为**

| 变量 | 默认值 | 含义 |
|---|---|---|
| `MINIDSH_TOKEN` | — | 绑定非回环地址时必填 |
| `MINIDSH_SUBAGENT_MAX_DEPTH` | `1` | 委派深度（`0` 表示禁用） |
| `MINIDSH_GOAL_MAX_ROUNDS` | `256` | 目标轮次的默认上限 |
| `MINIDSH_ASK_TIMEOUT_SECONDS` | `120` | 限时提问的等待时长 |
| `MINIDSH_TODO_ALLOW_PARALLEL` | `0` | 允许多个 `in_progress` 待办 |
| `MINIDSH_SCHEDULE_NEW_INTERVAL_SECONDS` | `60` | 定时任务的最小间隔 |
| `MINIDSH_WEB_TIMEOUT_MS` | `30000` | 抓取超时 |
| `MINIDSH_WEB_FETCH_MAX_OUTPUT_CHARS` | `200000` | 抓取输出上限 |
| `MINIDSH_WEB_SEARCH_URL` | — | 搜索提供方端点 |
| `MINIDSH_WEB_ALLOW_PRIVATE` | `0` | 允许私有地址（本地测试用） |
| `MINIDSH_PERSISTENT_TIMEOUT_MS` | `300000` | PTY 命令超时 |
| `MINIDSH_PERSISTENT_MAX_OUTPUT_CHARS` | `16000` | PTY 输出尾部长度 |
| `MINIDSH_PRUNE_THRESHOLD_CHARS` | `8192` | 裁剪触发阈值 |
| `MINIDSH_PRUNE_HEAD_CHARS` | `4096` | 保留的头部长度 |
| `MINIDSH_PRUNE_TAIL_CHARS` | `1024` | 保留的尾部长度 |
| `MINIDSH_SKILL_DESCRIPTION_MAX` | `500` | 技能描述长度上限 |
| `MINIDSH_WORKFLOW_MAX_AGENTS` | `1000` | 工作流的智能体预算 |
| `MINIDSH_WORKFLOW_MAX_CONCURRENT` | `min(32, cpu)` | 工作流并发度 |
| `MINIDSH_PTC_TIMEOUT_MS` | `120000` | `run_code` 超时 |
| `MINIDSH_PTC_MAX_TIMEOUT_MS` | `600000` | `run_code` 超时上限 |
| `MINIDSH_PTC_MAX_OUTPUT_BYTES` | `67108864` | `run_code` 输出上限 |
| `MINIDSH_PTC_MAX_PARALLEL` | `10` | 并行的只读子调用数 |
| `MINIDSH_PTC_MAX_PENDING` | `128` | 挂起子调用的上限 |
| `MINIDSH_PTC_MAX_SUBCALLS_PER_MINUTE` | `1024` | 速率预算 |
| `MINIDSH_PTC_STOP_GRACE_MS` | `3000` | 强制杀死前的宽限时间 |
| `MINIDSH_PTC_PERSISTENT` | `0` | 默认使用持久 REPL |

---

## 事件参考

会话就是事件日志。下面是本框架写出的事件类型：

| 分组 | 事件 |
|---|---|
| 回合与步骤 | `turn/start`, `turn/end`, `step/start`, `step/end` |
| 消息 | `system/message`, `user/message`, `assistant/message`, `assistant/delta`, `assistant/usage` |
| 工具 | `tool/call`, `tool/result`, `tools/pre-execute`, `tools/post-execute` |
| 审批 | `approval/asked`, `approval/decided`, `approval/policy` |
| 文件 | `fs/observed` |
| 会话 | `session/start`, `session/resume`, `session/fork`, `session/migrated`, `session/repaired` |
| 进程 | `job/registered`, `job/settled`, `pty/created`, `pty/reset` |
| 记忆 | `compaction/start`, `compaction/end`, `compaction/prune` |
| 沙箱 | `sandbox/mode` |
| 交互 | `question/asked`, `question/answered`, `todo/write` |
| 委派 | `subagent/start`, `subagent/end`, `subagent/catalog` |
| 工作流 | `workflow/start`, `workflow/phase`, `workflow/log`, `workflow/agent-start`, `workflow/agent-end`, `workflow/end` |
| 目标 | `goal/change`, `goal/changed`, `goal/activation-changed` |
| 定时任务 | `schedule/changed`, `schedule/inbox` |
| 模型传输 | `llm/retry`, `llm/image-upload`, `llm/image-fallback` |
| 预设 | `preset/selected` |
| 运行 | `run/result`, `run/error`, `run/cancelled`, `run/done` |

```bash
minidsh session <session-id>     # raw events
minidsh transcript <session-id>  # human-readable projection
minidsh surface <session-id>     # what the model currently sees
```

---

## 与 2026 年智能体生态的对应关系

MiniDSH 刻意做得很小，但它的接缝，和生产级智能体平台最终收敛到的方向是对齐的。每一行都是一个
可以把教学实现和当下业界答案放在一起比较的地方。

| 2026 年的实践 | 在这里的位置 | 这个实验教什么 |
|---|---|---|
| 作为一门独立学科的 **harness 工程**（[harness 章节](https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md)） | 整个仓库 | 负责顺序、限制、持久化和失败处理的是框架，而不是模型 |
| 把 **AGENTS.md** 当作跨厂商的智能体指令文件（[agents.md](https://agents.md)） | [`AGENTS.md`](AGENTS.md) | 给编码智能体的指令放在可预期的位置，与面向人类的 README 分开 |
| 带渐进式披露的 **Agent Skills**（[规范](https://agentskills.io/specification)） | `skills.py` | 只公布名字 + 描述；只有任务需要时才加载正文 |
| 作为工具互操作层的 **MCP**（[规范](https://modelcontextprotocol.io/specification/2026-07-28/changelog)） | `mcp.py` | 命名空间、凭据脱敏、取消、失败即关闭的发现流程 |
| **MCP 2026-07-28 的无状态化** | 对齐文档、边界 | 实现的是经典握手；新的无状态模型作为缺口被记录在案 |
| **上下文工程**（压缩、观察遮蔽、裁剪） | `compaction.py`, `surface.py`, `pruner.py` | 压缩视图，永不压缩日志 |
| **面向智能体的事件溯源** | `session.py`, `generations.py` | 用一份仅追加日志完成重放、审计、分叉和崩溃修复 |
| **沙箱**（Seatbelt / Landlock / bwrap）（[讨论](https://github.com/zeroclaw-labs/zeroclaw/blob/a56c345d51dd8ab562e9351e0d4ab83f6a741db9/docs/book/src/security/sandboxing.md)） | `seatbelt.py` | 策略在提示词层、审批在循环里、强制执行在内核中 |
| **内核级隔离** vs. 提示词级规则 | `sandbox.py` + `seatbelt.py` | “请不要写”不是边界；`Operation not permitted` 才是 |
| **把子代理当作上下文隔离** | `subagents.py` | 父代理收到的是摘要，不是子代理的完整对话记录 |
| **确定性编排** vs. 智能体式控制流 | `workflow.py` | 图已知时用脚本，图未知时用智能体 |
| **程序化工具调用** | `ptc.py` | 一个代码回合代替十个工具回合，并附带配额与隔离 |
| **OpenTelemetry GenAI 语义约定**（[规范](https://opentelemetry.io/docs/specs/semconv/gen-ai/)） | `events.py` + `server.py` | 会话/运行/回合/步骤/尝试的关联已经是 span 形状；导出是缺失的那条边 |
| **带异步回答的人在环路** | `questions.py` | 迟到的回答、挂起状态，以及“跳过不等于批准” |
| **带预算的长周期目标** | `goals.py` | 修订号校验的状态、恢复即解除武装、显式的阻塞条件 |
| **持久化调度** | `schedule.py` | 已投递 ≠ 已完成；补投策略很重要 |
| **评测**（Terminal-Bench、τ²-bench 风格）（[排行榜](https://benchlm.ai/benchmarks/terminal-bench-2)） | `docs/exercises.md` | 确定性的模拟轨迹让回归可度量 |
| **智能体间互操作**（A2A v1.0）（[公告](https://agent2agent.info/blog/a2a-v1-announcement/)） | 边界 | 目前是进程内子代理；A2A 是已记录的下一条边界 |
| **提示词注入防御** | `web_tools.py` | 外部内容只是数据，永远不是指令；预算在工具处强制执行 |
| **能力范围限定** | `presets.py` | 评审智能体不该带着 shell |

两点实情：

1. **MCP 规范在 2026-07-28 变了。** MiniDSH 实现的是大多数现存服务仍在使用的握手式协议；对齐
   文档把无状态修订记录为一个已知缺口，而不是假装它不存在。
2. **遥测是 span 形状的，但没有导出。** 事件已经带有 OTel GenAI span 所需的身份字段；接一个
   导出器是一项有意的练习，而不是一个没做完的功能。

---

## 27 层学习路径

第一次请按顺序读，之后可以随意跳。每一层都给出模块名，以及它要回答的问题。

| # | 层 | 模块 | 它回答的问题 |
|---|---|---|---|
| 0 | 插件内核 | `config.py`, `plugins.py` | 功能如何变成服务？被禁用的功能为什么会干净地消失？ |
| 1 | 模型适配器 | `llm.py`, `deepseek.py` | 一个流式协议如何变成内容块和工具调用？ |
| 2 | 审批与写入 | `sandbox.py`, `approval.py`, `observations.py` | 为什么“先读后写”是一条正确性规则？ |
| 3 | Shell 与后台任务 | `shell.py`, `jobs.py` | 前台超时会把正在运行的进程怎么样？ |
| 4 | 恢复与压缩 | `session.py`, `compaction.py` | 为什么投影变了，历史却没变？ |
| 5 | HTTP 与托管 | `server.py` | 并发运行如何保持隔离？ |
| 6 | 外部插件与产品 | `discovery.py`, `packages/` | 插件如何在不碰核心的情况下发布？ |
| 7 | 重试与取消 | `retry.py`, `cancellation.py` | 重试之后必须丢弃哪些部分输出？ |
| 8 | 图像与 Files API | `images.py`, `files.py` | 为什么内容寻址是上传的正确键？ |
| 9 | 持久 shell 与 PTY | `pty_shell.py` | 如何在不永久保留进程的前提下保留 shell 状态？ |
| 10 | 迁移与崩溃恢复 | `generations.py`, `session_recovery.py` | 如何在不伪造成功的前提下完成恢复？ |
| 11 | 表面与裁剪 | `surface.py`, `pruner.py` | 模型视图如何缩水，而审计日志不缩水？ |
| 12 | 提问、待办、Web 工具 | `questions.py`, `todo.py`, `web_tools.py` | 人的回答迟到之后，为什么仍然有效？ |
| 13 | 流式与背压 | `stream_codec.py`, `server.py` | 一个慢速的 SSE 客户端会怎样？ |
| 14 | 流记录与重放 | `stream_codec.py` | 如何在不重放整次运行的情况下续传一条流？ |
| 15 | 限时提问 | `questions.py` | 挂起、跳过和批准之间有什么区别？ |
| 16 | 不可变世代 | `generations.py` | 带哈希的清单为什么优于一个可变的文件？ |
| 17 | 内核沙箱 | `seatbelt.py` | 真正的权限拒绝来自哪里？ |
| 18 | 子代理 | `subagents.py` | 为什么委派的重点是上下文隔离？ |
| 19 | 工作流 | `workflow.py`, `workflow_guest.js` | 什么时候编排应该写成代码，而不是模型回合？ |
| 20 | 工作流任务与恢复 | `jobs.py`, `workflow.py` | 后台运行如何在宿主重启后存活下来？ |
| 21 | MCP | `mcp.py` | 如何集成第三方工具，又不盲目信任它们？ |
| 22 | 技能 | `skills.py` | 一开始应该加载多少上下文？ |
| 23 | 定时任务 | `schedule.py`, `schedule_time.py` | 已投递和已完成有什么区别？ |
| 24 | 目标 | `goals.py` | 是什么让自主循环不至于永远跑下去？ |
| 25 | PTC | `ptc.py`, `ptc_runner.js` | 让模型写代码，你得到什么、失去什么？ |
| 26 | 预设与人设 | `presets.py` | 如何按会话限定能力范围？ |

每一层在 [docs/exercises.md](docs/exercises.md) 里都有配套练习。

---

## 调试手册

| 症状 | 先跑什么命令 | 看什么 |
|---|---|---|
| 模型从不调用某个工具 | `minidsh tools` | 这个工具在 schema 里吗？它的开关打开了吗？ |
| 提示词提到了一个并不存在的工具 | `minidsh prompt` | 指引由工具插件注册——检查有没有过期的段落 |
| 一次写入被拒绝了 | `minidsh session <id>` | `approval/decided` 的结果：`rejected`、`unavailable` 还是 `cancelled` |
| 审批通过了，写入还是失败 | `minidsh transcript <id>` | 缺失或已失效的 `fs/observed` 观察 |
| 恢复后的会话行为不一样 | `minidsh surface <id>` | 某次替换或压缩改变了模型视图 |
| 上下文立刻爆掉 | `minidsh --context-window 100 run ...` | 观察 `compaction/start` 和摘要的形状 |
| 后台任务看起来卡住了 | `minidsh session <id>` | 有 `job/registered` 却没有 `job/settled` |
| 会话加载不了 | `minidsh repair <id>` | 被截断的尾部、未闭合的回合，或哈希不匹配 |
| 智能体一直在循环 | `minidsh goal <id>` | 目标武装了吗？轮次预算呢？`blocked_reason`？ |
| 流式文本重复了 | `minidsh stream <id>` | 重试尝试；消费方必须按 `attempt` 重置 |

---

## 边界与非目标

如实列出的限制，再重述一遍，免得有人非得自己踩坑才发现：

- **格式。** 会话 v2 是用于学习的世代格式，不是上游的 Zstandard 多世代格式。没有 Windows 写锁。
- **沙箱。** 只有 macOS Seatbelt。没有 Linux Landlock/bwrap，也没有 Windows 隔离。
- **PTY。** 只有 Unix `/bin/bash`。没有 ConPTY，没有跨进程的终端恢复。
- **MCP。** 握手式协议；2026-07-28 的无状态修订尚未实现。没有 OAuth，没有资源订阅，也没有
  重连预算。
- **子代理。** 仅限进程内。不支持 ACP/Codex/Claude Code/远程 SDK 后端。
- **遥测。** 事件是 span 形状的，但发布包里不带 OTel 导出器。
- **Token。** 启发式估算，不是分词器；没有图像 token 计价。
- **审批。** 是人的控制手段，不是安全边界；请与内核沙箱搭配使用。
- **在线验收。** 真实的 DeepSeek 调用尚未在 CI 中验证。密钥只从环境读取。
- **范围。** 这是教学代码。不要把这个 HTTP 宿主暴露到公网。

完整、逐条列出的边界清单在 [docs/upstream-parity.md](docs/upstream-parity.md)。

---

## 常见问题

**我应该基于这个框架做产品吗？**
不应该。它是一个用于阅读和实验的仪器。可以抄模式，不要抄进程。

**为什么要有确定性的模拟模型？**
因为智能体的 bug 是轨迹 bug。用模拟模型，你可以对事件的精确顺序做断言；用真实模型，你只能对
结局做断言。

**为什么默认一切都是关的？**
每一个高级层都会扩大攻击面。一次打开一个功能，你才会知道每一个功能真实的代价。

**我需要 API key 吗？**
不需要。所有默认路径都能离线工作。DeepSeek 适配器是需要显式开启的。

**为什么模拟语法里会有中文别名？**
项目起步于中文，而真实会话中常常中英混用。别名解析到*同一组*意图，因此下游什么都不变。

**怎么添加一个工具？**
见 [`examples/minidsh-clock`](examples/minidsh-clock) 和 [docs/modules.md](docs/modules.md)。
简版：注册一个 `provides=("tools",)` 的 `Plugin`，声明一个 schema，然后调用
`ctx.require("tools").register(...)`。审批、策略和事件都是免费得到的。

**我只有一个小时，该从哪开始？**
第 0 → 1 → 2 → 3 层，然后跑一遍 Web 工作台，端到端看一个会话走完。

---

## 文档索引

| 文档 | 语言 | 用途 |
|---|---|---|
| [README.md](README.md) | English | 本指南 |
| [README.zh-CN.md](README.zh-CN.md) | 中文 | 中文指南 |
| [docs/index.html](docs/index.html) | EN / 中文 | 教科书式站点：31 章，每章含概念、动机、实现、验证、代价与业界对照 |
| [docs/modules.md](docs/modules.md) | English | 逐模块的职责、契约与陷阱 |
| [docs/exercises.md](docs/exercises.md) | English | 与当下实践对应的可运行实验 |
| [docs/mock-model.md](docs/mock-model.md) | English | 确定性的提示词语法 |
| [docs/upstream-parity.md](docs/upstream-parity.md) | English | 哪些与上游一致、哪些不一致 |
| [docs/upstream-parity.zh-CN.md](docs/upstream-parity.zh-CN.md) | 中文 | 同一份报告的中文版 |
| [AGENTS.md](AGENTS.md) | English | 给编码智能体的指令 |
| [config.example.json](config.example.json) | — | 每个开关及其默认值 |
| [LICENSE](LICENSE) | — | MIT 许可 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | English | 上游署名与复述范围 |

## 发布文档站点

`docs/index.html` 是一个自包含的中英双语站点（无需构建步骤，除 `docs/assets/` 下两张截图与同目录的 `chapters.js` 外没有外部资源）。
`.github/workflows/pages.yml` 会在每次改动 `docs/` 的推送后把该目录发布到 GitHub Pages。站点已上线：
<https://norwinyu.github.io/dsh-python-lab/>。

**一次性设置（fork 后需要重新做）：**

1. 打开 **Settings → Pages**。
2. 在 *Build and deployment* 下把 **Source** 设为 **GitHub Actions**。
3. 重新运行 `Pages` 工作流（或再推送一次 `docs/` 下的改动）。

在该开关打开之前，`Configure Pages` 步骤会以 404 失败 —— 工作流本身没有问题，只是仓库尚未启用 Pages。

## 许可证

MIT，见 [LICENSE](LICENSE)。

本项目是独立重实现，不是 fork。它确实复述了少量上游**文案**（身份句、工具描述、工具使用指引、
压缩摘要小标题），目的是让行为可以逐行对照。DeepSeek Harness 同样采用 MIT 许可，Copyright (c) 2026
DeepSeek，因此这种复用只要保留署名即被允许。具体复述了什么、以及上游许可原文，都记录在
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中。

如果你基于 MiniDSH 继续构建，请同时保留两份声明：本仓库的 `LICENSE` 与第三方声明。
