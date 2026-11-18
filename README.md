# MiniDSH

**A layered, inspectable agent harness written in Python — built to be read, run, and taken apart.**

English | [简体中文](README.zh-CN.md)

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![Dependencies: none](https://img.shields.io/badge/core%20dependencies-none-2ea44f)](pyproject.toml)
[![Docs site](https://img.shields.io/badge/docs-github%20pages-4c53d6)](https://norwinyu.github.io/dsh-python-lab/)
[![Upstream parity](https://img.shields.io/badge/upstream-dsh%20parity%20report-6b74f0)](docs/upstream-parity.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)

MiniDSH re-implements the *shape* of a modern coding-agent harness in about 12,000 lines of
dependency-free Python. It is not a chatbot demo and not a wrapper around an SDK: it contains a
plugin kernel, an agent loop, a tool registry, a permission stack, an event-sourced session store,
subagents, a workflow engine, an MCP client, skills, a scheduler, persistent goals, programmatic
tool calling, and three product shells (CLI, HTTP + Web, Desktop).

Every layer can be switched on or off, observed, and verified independently. If you have ever
wondered *where the tool call actually happens*, *why a resumed session forgets a file it wrote*,
or *what stops an agent from deleting your home directory*, this repository answers those
questions with running code instead of diagrams.

> **It is not an official port of DeepSeek Harness.** Exactly which slices are implemented, and
> which boundaries remain, is recorded in [docs/upstream-parity.md](docs/upstream-parity.md)
> (335 bullets, deliberately blunt). We never use "mostly aligned" to hide a missing capability.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [What you can do in ten minutes](#what-you-can-do-in-ten-minutes)
- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Architecture](#architecture)
- [The lifecycle of one run](#the-lifecycle-of-one-run)
- [Feature map](#feature-map)
- [Feature deep dives](#feature-deep-dives)
  - [1. Plugin kernel and composition](#1-plugin-kernel-and-composition)
  - [2. Agent loop](#2-agent-loop)
  - [3. System prompt and runtime context](#3-system-prompt-and-runtime-context)
  - [4. File tools](#4-file-tools)
  - [5. Sandbox policy, approval, observations](#5-sandbox-policy-approval-observations)
  - [6. Kernel sandbox (macOS Seatbelt)](#6-kernel-sandbox-macos-seatbelt)
  - [7. Shell, jobs, and PTY](#7-shell-jobs-and-pty)
  - [8. Sessions: generations, resume, fork, repair](#8-sessions-generations-resume-fork-repair)
  - [9. Surface replacement and tool-result pruning](#9-surface-replacement-and-tool-result-pruning)
  - [10. Compaction](#10-compaction)
  - [11. Streaming, stream recording, SSE replay](#11-streaming-stream-recording-sse-replay)
  - [12. Questions and todos](#12-questions-and-todos)
  - [13. Web fetch and search](#13-web-fetch-and-search)
  - [14. Subagents](#14-subagents)
  - [15. Workflow engine](#15-workflow-engine)
  - [16. MCP client](#16-mcp-client)
  - [17. Skills](#17-skills)
  - [18. Schedule](#18-schedule)
  - [19. Goals and automatic rounds](#19-goals-and-automatic-rounds)
  - [20. Programmatic tool calling (PTC)](#20-programmatic-tool-calling-ptc)
  - [21. Agent presets](#21-agent-presets)
  - [22. HTTP host and Web workbench](#22-http-host-and-web-workbench)
  - [23. External plugins](#23-external-plugins)
  - [24. Product distributions](#24-product-distributions)
  - [25. Real DeepSeek adapter](#25-real-deepseek-adapter)
- [Configuration reference](#configuration-reference)
- [Event reference](#event-reference)
- [How this maps to the 2026 agent ecosystem](#how-this-maps-to-the-2026-agent-ecosystem)
- [The 27-layer learning path](#the-27-layer-learning-path)
- [Debugging cookbook](#debugging-cookbook)
- [Boundaries and non-goals](#boundaries-and-non-goals)
- [FAQ](#faq)
- [Documentation index](#documentation-index)

---

## Why this exists

Most agent frameworks are optimized for *shipping an app quickly*. They hide the interesting
parts: how tool schemas are registered, when approval is checked, what happens on cancellation,
how history is compacted, what is durable after a crash. That is fine for products and terrible
for learning.

MiniDSH optimizes for the opposite: **every mechanism is visible, and every mechanism can be
disabled to see what breaks.**

Three design rules make this work:

1. **Services, not globals.** Everything is a plugin that provides and requires named services.
   Turning a feature off removes its service, its tool schema, and its prompt section together.
2. **Facts and projections are separate.** The session log is append-only and immutable. What the
   model sees is a derived view that can be compacted, pruned, or replaced without rewriting
   history.
3. **Fail closed.** Missing approver, unknown feature flag, corrupted session, missing preset,
   oversized payload — all of them stop the operation with an explicit error instead of degrading
   silently.

The result is a harness that behaves like a real one at small scale, and that you can hold in your
head.

**Who it is for:** engineers who want to understand harness internals, teams evaluating build vs.
buy, and anyone preparing for agent-platform interviews where "how would you implement X" is the
question.

---

## What you can do in ten minutes

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

Everything above works offline with the deterministic `MockModel`. The same commands work with a
real model after exporting `DEEPSEEK_API_KEY` and enabling `llm.deepseek`.

---

## Quick start

**Requirements:** Python 3.9+ on macOS or Linux. Node.js is needed only for `workflow` and
`run_code`. `pywebview` is needed only for the Desktop shell.

```bash
git clone https://github.com/NorwinYu/dsh-python-lab.git
cd dsh-python-lab

python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
```

Then:

```bash
minidsh plugins      # which layers are active, pending, disabled, or missing a dependency
minidsh tools        # the tool schemas the model currently sees
minidsh prompt       # the exact system prompt + runtime context
minidsh run "read README.md" --trace
```

If `minidsh` reports `ModuleNotFoundError: No module named 'minidsh'`, your interpreter skipped the
editable-install `.pth` file (some bundled or patched CPython builds do). Either install in compat
mode or run from source — the module entry point is identical:

```bash
python3 -m pip install -e . --config-settings editable_mode=compat
# or:
PYTHONPATH=src python3 -m minidsh plugins
```

`--trace` prints the turn, every tool call, and every event payload, so you can follow the loop
without opening the session file.

### Your first allowed write

The default sandbox policy is `read-only`, so writes and shells are refused. To allow writing
inside the workspace you must do **two** things: widen the policy and approve the operation.

```bash
# Non-interactive: approve every write/shell in this process (learning only)
minidsh --sandbox-mode workspace-write --yes run "write demo.txt: hello"

# Interactive: approve each operation at the prompt
minidsh --sandbox-mode workspace-write run "write demo.txt: hello"
```

> `--yes` approves **everything** in the current process. Never use it in an environment where
> the model reads untrusted content.

### Mock model cheat sheet

`MockModel` has no natural-language understanding. It maps explicit prefixes to intents, which is
exactly what makes trajectories reproducible. English is the default grammar; every intent also
accepts a Chinese alias, because the lab started in Chinese and real sessions mix languages.

| Intent | English | Chinese alias | Tool |
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

An unknown prefix returns a help message rather than a guessed tool call — a small but important
property: **the mock never invents intent.** Full grammar details live in
[docs/mock-model.md](docs/mock-model.md).

### Open the Web workbench

```bash
minidsh serve --port 3080
# then open http://127.0.0.1:3080
```

The workbench is a single dependency-free HTML file served by the harness itself. It has an
English/中文 switch, light/dark themes, a structured execution trace, live streaming text,
pending-question forms, session browsing, and compact/prune/fork controls.

---

## Repository layout

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

## Architecture

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

Everything above `app.py` is a product shell. Everything below it is a service. No tool imports
the Agent; no product imports another product; the dependency arrow only points inward.

A detailed per-file breakdown lives in [docs/modules.md](docs/modules.md).

---

## The lifecycle of one run

This is the sequence you can watch in the trace panel or with `--trace`:

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

Each numbered step is a place where a real harness makes a design decision. MiniDSH makes each
one explicit and separately testable.

---

## Feature map

| Layer | What you learn | Default |
|---|---|---|
| **Kernel** | Plugin flags, dependency activation, service graph, composition root | on |
| **Agent core** | Turn/step loop, prompt assembly, tool dispatch, cancellation | on |
| **Tools** | Schema design, envelopes, path safety, atomic writes | on |
| **Permissions** | Sandbox modes, approval, audit events, file observations | on |
| **Processes** | Job isolation, output cursors, process groups, PTY persistence | on |
| **Memory** | JSONL generations, resume, fork, repair, surface, compaction | on |
| **Transport** | HTTP API, SSE, run correlation, backpressure, replay | on |
| **Collaboration** | Subagents, message passing, interruption, workflow orchestration | off |
| **Ecosystem** | MCP servers/resources, skills, agent presets | off |
| **Long horizon** | Scheduled delivery, persistent goals, automatic rounds | off |
| **Programmatic** | TypeScript `run_code`, Node REPL, SDK RPC back into the tool registry | off |
| **Products** | CLI, Web workbench, Desktop shell, external plugin discovery | on |

Everything marked *off* is off for a reason: each one widens the attack surface or weakens the
"small enough to read" property. Turn them on one at a time.

---

## Feature deep dives

Each section follows the same shape: **what it does**, **how to try it**, **what to observe**,
**design notes**, and **boundaries**.

### 1. Plugin kernel and composition

**What it does.** `PluginManager` turns a dictionary of feature flags into a live service graph.
A plugin declares `provides`, `requires`, and `activate`. Activation loops until no pending
plugin can make progress, so declaration order never matters — only dependencies do.

**Try it.**

```bash
minidsh plugins                          # active / disabled / pending with missing services
minidsh --disable core.jobs plugins      # jobs dependents go 'missing=jobs'
minidsh --disable core.compaction plugins
minidsh --disable llm.mock --disable llm.deepseek plugins   # agent.core stays pending
```

**What to observe.** Disabling a *service* differs from disabling a *tool*: tools disappear from
the schema, services disappear from the graph, and dependents stay `pending` instead of crashing.

**Design notes.**

- One provider per service name; a second registration raises.
- Deactivation runs in reverse registration order, so resources unwind correctly.
- External plugins are registered as `DisabledExternalPlugin` placeholders when off — visible in
  `plugins` output, never imported.
- There is no hot reload. The graph converges once, at startup, and is then stable.

**Boundary.** No runtime plugin swap, no scoped child graphs beyond presets, no YAML loader.

---

### 2. Agent loop

**What it does.** `Agent.run()` is one turn. Inside a turn, the loop alternates model calls and
tool calls, up to `max_steps` (default 4). It owns ordering, budgets, persistence, and failure
handling — but not intelligence.

**Try it.**

```bash
minidsh run "read README.md" --trace
minidsh run "delegate analysis | read README.md" \
  --enable core.subagents --enable tools.subagent --enable tools.subagent-control
```

**What to observe.** `turn/start` → `assistant/message` → `tools/pre-execute` →
`tools/post-execute` → `assistant/message` → `turn/end`. A turn can end with `max_steps` reached,
which is a budget outcome, not an error.

**Design notes.**

- Cancellation is checked at model, tool, and shell boundaries. `RunCancelled` propagates.
- Subagent completion arrives as a `subagent-settled` user message and is consumed by a later
  step, not by a separate scheduler.
- Streaming deltas carry `session_id`, `run_id`, `turn`, `step`, and `attempt` so a consumer can
  discard a failed attempt's draft.

**Boundary.** `max_steps` is fixed per Agent instance, not adaptive. There is no planner, no
reflection step, no automatic retry of failed tool calls beyond the model's own decision.

---

### 3. System prompt and runtime context

**What it does.** Builds the prompt from ordered sections: a fixed identity block, per-tool
guidance, and a runtime context snapshot.

**Try it.**

```bash
minidsh prompt
minidsh --disable tools.edit prompt        # the "prefer edit" clause disappears with the tool
minidsh --sandbox-mode workspace-write --yes prompt
```

**What to observe.** The runtime context is a **separate user message**, not part of the static
system prompt, and it is regenerated on resume. That is why a resumed session sees the *current*
policy, not the policy it started with.

**Design notes.**

- Identity is fixed: `You are an AI agent powered by DeepSeek Harness.`
- Tool guidance is registered by the tool plugin with an explicit order (bash 1000, read 1100,
  write 1200, edit 1300, glob 1400, jobs 1600).
- `write` guidance gains "and prefer edit for targeted changes" only when `edit` is enabled.
- Turning a tool off removes its schema *and* its instructions in the same step. A model told to
  use a tool it cannot call is a harness bug, not a model bug.

**Boundary.** Section ordering is fixed in code; there is no per-preset prompt authoring beyond
persona prefix/suffix.

---

### 4. File tools

**What it does.** Implements `read`, `glob`, `write`, and `edit` with upstream-compatible names,
schemas, envelopes, and limits.

**Try it.**

```bash
minidsh run "read README.md"
minidsh run "list"
minidsh --sandbox-mode workspace-write --yes run "write demo.txt: hello"
minidsh --sandbox-mode workspace-write --yes run "edit demo.txt: replace hello with world"
```

**What to observe.**

| Tool | Limits and behaviour |
|---|---|
| `read` | 2000 lines, 2000 chars per line, 50 KiB total, line numbers, `<path>/<type>/<content>` envelope |
| `glob` | max 100 results, hidden files included, VCS dirs excluded, sorted by modification time |
| `write` | creates or overwrites, returns `Created file` / `Updated file`, atomic replace |
| `edit` | literal match, unique by default, `replace_all` option, `$`-free replacement |

**Design notes.**

- Overwriting or editing requires a **prior read of the same version in the same session**. If the
  file changed on disk in between, the observation is invalidated and the operation is refused.
- That rule is the difference between "the model edited a file" and "the model clobbered a file
  someone else was editing".
- Paths are resolved against the workspace; `..` and symlink escapes are rejected under
  `workspace-write`.

**Boundary.** No patch/diff tool, no binary editing, no multi-file transaction.

---

### 5. Sandbox policy, approval, observations

**What it does.** Three independent controls that compose:

| Control | Values | Question it answers |
|---|---|---|
| Sandbox policy | `read-only`, `workspace-write`, `danger-full-access` | *Where* may this operation touch? |
| Approval policy | `ask`, `never` (+ `--yes`) | *Who* authorized this operation? |
| Observations | per-session file versions | Is this write based on the current content? |

**Try it.**

```bash
# Default: refused by policy
minidsh run "write denied.txt: hello"

# Widen the policy but refuse everything at the approver
minidsh --sandbox-mode workspace-write --approval-policy never run "write denied.txt: hello"

# No workspace restriction, no approval prompts
minidsh --sandbox-mode danger-full-access run "write full.txt: hello"
```

**What to observe.** `minidsh session <id>` shows paired `approval/asked` and `approval/decided`
events with request id, tool name, call id, and outcome (`allowed-once`, `rejected`,
`unavailable`, `cancelled`).

**Design notes.**

- A non-interactive run with no answerer returns `unavailable` — it does not silently allow.
- Cancelling an approval records `cancelled` immediately and discards a late answerer result.
- Observations are persisted with the session and restored on resume, so "read before write"
  survives restarts.
- HTTP can escalate or tighten per session: `POST /v1/sessions/<id>/sandbox` and
  `/approval-policy`. The runtime context and the process boundary read the same override.

**Boundary.** Approval is a human-in-the-loop control, not a security boundary by itself. Pair it
with the kernel sandbox below when running untrusted content.

---

### 6. Kernel sandbox (macOS Seatbelt)

**What it does.** Enables real OS-level enforcement for bash, background jobs, and the persistent
PTY. Refusals come from the child process, not from the harness.

**Try it.**

```bash
minidsh --enable core.kernel-sandbox run "run printf denied > blocked.txt"
```

**What to observe.** A real kernel error, for example:

```text
/bin/bash: blocked.txt: Operation not permitted
[exit code: 1]
```

**Design notes.**

- `read-only`: execution and reads allowed, writes denied.
- `workspace-write`: only the workspace, the managed jobs directory, and platform temp dirs.
- `danger-full-access`: isolation disabled.
- Sandbox mode cannot change while a persistent terminal is open; kill it first.

**Boundary.** macOS only. No Linux Landlock/bubblewrap, no Windows equivalent. This is a teaching
slice of OS isolation, not a hardened multi-tenant runtime.

---

### 7. Shell, jobs, and PTY

**What it does.** Two shell models:

- **`bash` (default):** a fresh `bash -c` per call. No cwd, variables, or functions persist.
- **`bash --persistent`:** one interactive `/bin/bash` under a Unix PTY per session, with state
  preserved across calls.

Plus the jobs system: background processes with independent stdout/stderr cursors.

**Try it.**

```bash
# One-shot
minidsh --sandbox-mode workspace-write --yes run "run printf 'hello'"

# Background job
minidsh --sandbox-mode workspace-write --yes run "run in background sleep 10"

# Persistent shell (replaces tools.bash automatically)
minidsh --enable tools.bash-persistent --sandbox-mode workspace-write --yes \
  run "run export DEMO=hello; cd /tmp"
```

**What to observe.**

- Foreground timeout does not kill the process; it **moves the same job to the background** and
  returns `[still running after ...; moved to background job bash-1]`.
- `job_output` consumes new output via a cursor; it does not re-print what you already read.
- Each session may have at most 10 running/stopping jobs.
- The memory ring holds 256 KiB, shrinks to 16 KiB after the first terminal read; full output is
  written to disk and the path is reported.
- The persistent shell wraps each command in random start/end markers and ends with
  `[Command finished with exit code N]`.

**Design notes.**

- Environment scrubbing removes anything matching `*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`,
  and `DSH_*`, then injects managed `DSH_HOME` and `DSH_SESSION_ID`. Credentials do not leak into
  child processes by accident.
- Killing a job kills its **process group**, so a shell script cannot leave orphans behind.
- PTY resets on timeout, cancellation, `exit`, HTTP kill, or shutdown.

**Boundary.** Unix PTY only (no Windows ConPTY). CLI is a short-lived process, so background jobs
and PTYs are reclaimed on exit — use `serve` for long-running work.

---

### 8. Sessions: generations, resume, fork, repair

**What it does.** An event-sourced session store with immutable generations.

**Layout:**

```text
.minidsh/sessions/<session-id>/
├── index.json                       manifest: current generation, path, SHA-256
└── generations/
    ├── 00000001.jsonl               full snapshot, never overwritten
    └── 00000002.jsonl               next snapshot after an append
```

**Try it.**

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

**What to observe.**

- `resume` restores messages, turn numbering, and file observations. The runtime context is
  regenerated from **current** config.
- `fork` copies history up to a boundary and never mutates the parent.
- `compact` changes the projection, not the event log.
- `repair` writes a backup first, then closes the loop honestly.

**Design notes.**

- Every event is validated on read: session identity, contiguous `seq`, event type, payload shape,
  UTF-8.
- Migration and repair save the original bytes to
  `.minidsh/sessions/backups/<id>.<sha256>.jsonl` before publishing a new generation.
- Manifest updates use temp file + `fsync` + `os.replace`; the hash is re-verified on read, so
  external tampering fails closed.
- `resume` and `compact` hold a POSIX `flock` write lease for the **whole turn**. Reads take no
  lock. The lock file has a stable inode and is released by the kernel if the process dies.
- Crash repair distinguishes *never started* (`TOOL_NOT_STARTED`) from *started, outcome unknown*
  (`TOOL_OUTCOME_UNKNOWN`), and warns against blindly retrying side effects.

**Boundary.** This is a learning v0/v1/v2 format, not the upstream multi-generation Zstandard
format. No Windows write lock.

---

### 9. Surface replacement and tool-result pruning

**What it does.** Separates *what happened* (append-only events) from *what the model currently
sees* (a folded view called the surface).

**Try it.**

```bash
minidsh surface <session-id>
minidsh --enable core.tool-result-pruner prune <session-id>
```

**What to observe.** A `tool/result` replacement event with
`surfaceOp={op: replace, startSeq, endSeq}` and `sourceEventSeqs` listing the shadowed nodes. The
original event is still in the log; `transcript` still shows it.

**Design notes.**

- Replacements validate that the range exists, is ordered, contains only current nodes, and that
  sources are neither duplicated nor from the future.
- A tool-result replacement may change `content` **only** — never the tool name or call id.
- The first system prompt can only be rewritten by a single system replacement.
- The pruner keeps head 4096 + marker + tail 1024 Unicode code points above an 8192 threshold,
  configurable via `MINIDSH_PRUNE_*`.
- Pruning is manual and off by default, so nothing changes behind your back.

**Boundary.** Only message-level and tool-result replacements. No arbitrary plugin projections, no
full upstream event type system.

---

### 10. Compaction

**What it does.** When estimated context exceeds 80% of the window, replace the middle of the
conversation with a summary while keeping the tail.

**Try it.**

```bash
minidsh --context-window 100 run "test automatic compaction"
minidsh compact <session-id>
```

**What to observe.** `compaction/start` and `compaction/end` events; the summary is wrapped in
`<compacted-summary>`; the original events are untouched.

**Design notes.**

- Trigger: 80% of `context_window` (default 1,048,576).
- Retention: roughly 16% of messages, counted from the tail.
- The compaction prompt is the upstream-style structured summary (primary request, concepts,
  files, errors, pending jobs, current work, next step, critical context).
- Token accounting is a heuristic estimate, not a real tokenizer. It is conservative on purpose.

**Boundary.** No exact tokenizer, no image token pricing, no model-switch re-compaction.

---

### 11. Streaming, stream recording, SSE replay

**What it does.** Three separate concerns that are easy to confuse:

| Feature | Flag | Purpose |
|---|---|---|
| Live deltas | `core.streaming` | `assistant/delta` events for real-time UI text |
| Stream recording | `core.stream-recording` | Compress raw chunks into the `assistant/message` payload |
| SSE replay | `core.sse-replay` | In-process ring + `Last-Event-ID` resume for dropped connections |

**Try it.**

```bash
minidsh stream <session-id>            # expand recorded chunks with seq/turn/step
curl -H 'Last-Event-ID: 42' 'http://127.0.0.1:3080/v1/runs/stream?session_id=<id>'
```

**What to observe.**

- Deltas carry `attempt`; on retry the consumer should clear the failed attempt's draft.
- Recorded chunks are `text-chunks`, `reasoning-chunks`, `tool-call-chunks` with `time0` and `dt`;
  usage/block-start/finish stay raw.
- The SSE broker keeps 2048 events; a slow listener is dropped, not allowed to stall the agent.

**Design notes.**

- Every SSE listener has a bounded 256-event queue. Overflow closes that listener and increments
  `dropped_streams`; the run continues and still persists its result.
- Replay selects only events for the same session with a greater id than the breakpoint, then
  attaches to live. It never starts a duplicate agent.
- Corrupted recorded chunks make the session read fail — full fidelity or nothing.

**Boundary.** Replay is per-process; restarting the host empties the ring. Recorded chunks are not
global SSE sequence numbers.

---

### 12. Questions and todos

**What it does.** `ask_user_question` suspends the turn for human input; `todo_write` maintains a
session-scoped task list.

**Try it.**

```bash
minidsh --enable tools.ask-user --enable tools.todo run "ask Continue?"
minidsh --enable tools.todo run "todo Finish the lab"
minidsh todos <session-id>
```

**What to observe.**

- An empty `selected` array with no `custom` means **skipped**, not approved.
- With `tools.ask-user-timed`, the default wait is 120 s; timeout returns `pending=true` and keeps
  the question in the queue.
- A late answer is injected as a user message with source kind `answer_to_pending_question`,
  carrying the original call id.
- Todos are whole-list replacements with unique non-empty text; only one `in_progress` unless
  `MINIDSH_TODO_ALLOW_PARALLEL=1`.

**Design notes.** Human input is asynchronous and untrusted. It gets validation, a lifecycle, and
an audit trail like every other external signal.

---

### 13. Web fetch and search

**What it does.** Bounded web access with prompt-injection awareness.

**Try it.**

```bash
minidsh --enable tools.web-fetch run "fetch https://example.com"
MINIDSH_WEB_SEARCH_URL=http://127.0.0.1:9000/search \
  minidsh --enable tools.web-search run "search agent harness"
```

**What to observe.**

- Fetched content is wrapped with an explicit "external content is untrusted" note plus the URL.
- HTML scripts, styles, and hidden nodes are stripped; deeply nested or unconvertible pages do not
  return raw markup.
- Private, loopback, and link-local addresses are refused, re-checked on every redirect.
- Search supports up to 10 queries, 10 sources, 800-character snippets, and Markdown citations.

**Design notes.** The default timeouts and byte caps are part of the tool contract, not a setting
buried in config. Budgets belong next to the capability.

**Boundary.** Not a complete SSRF defense (DNS is not pinned at connection time), not full
Turndown/GFM, and the search provider is a small JSON protocol rather than a vendor route.

---

### 14. Subagents

**What it does.** Creates child agents with their own sessions, names, and lifetimes.

**Try it.**

```bash
minidsh --enable core.subagents --enable tools.subagent --enable tools.subagent-control \
  run "delegate analysis | read README.md"
```

**What to observe.**

- `subagent` returns `agent-1` immediately; the child runs in its own session.
- Completion arrives in the parent as a `subagent-settled` user message.
- `subagent_fork` additionally inherits the parent's history up to the last complete `turn/end`.
- `send_message` wakes an idle child or queues input for a running one; `interrupt_agent` cancels
  the subtree; `list_agents` and `wait_agent` are for discovery and coordination.

**Design notes.**

- Depth limit `MINIDSH_SUBAGENT_MAX_DEPTH` defaults to 1; 0 disables delegation entirely.
- Names are unique per parent; the root is `lead`.
- Children inherit the parent's preset, so capability scoping composes.

**Boundary.** In-process only. No ACP, Codex, Claude Code, or remote SDK backend; no per-child model
selection; child `cwd` is recorded but file tools still use the root workspace.

---

### 15. Workflow engine

**What it does.** Runs a JavaScript orchestration script in an isolated Node process, where each
`agent()` call becomes a real subagent through JSON-RPC back into Python.

**Try it.**

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

**What to observe.** `workflow/start`, `workflow/phase`, `workflow/log`, `workflow/agent-start`,
`workflow/agent-end`, `workflow/end` events; a background run returns a job id usable with
`job_output`.

**Design notes.**

- No `require`, `process`, `fs`, `net`, or timers in the guest. `meta` and `args` are injected as
  JSON data, never evaluated.
- Object-root JSON Schema subset: `type/properties/required/additionalProperties/items/enum/const/oneOf`.
  A child failure or schema mismatch becomes `null`; a hook misuse aborts the whole run.
- `MINIDSH_WORKFLOW_MAX_AGENTS` 1000, concurrency defaults to `min(32, cpu_count)`, 4096 items per
  combinator call.
- After a host restart, finished workflows can be recovered from disk; unfinished ones are marked
  `failed: interrupted: host restarted` with a single `cause=recovery` audit event.

**Design notes on shape.** "Workflow" is deterministic orchestration — the control graph is known
before execution. "Agent" is model-driven control flow. Knowing which one a task needs is a core
architectural skill.

**Boundary.** `vm` isolation is a learning boundary, not a hardened PTC/OS sandbox. No per-agent
model routing.

---

### 16. MCP client

**What it does.** Speaks MCP to stdio and Streamable HTTP servers, registers their tools under
`mcp__<server>__<tool>`, and exposes resources.

**Try it.** Add to a JSON config:

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

**What to observe.**

- Real JSON-RPC `initialize` / `notifications/initialized`, paginated tool discovery.
- `${credential:NAME}` resolves from the host environment; failures redact the resolved value.
- A name conflict rejects the entire new tool generation and leaves the old tools intact.
- Server `isError` becomes an explicit tool failure, not a silent empty result.
- Resource blobs report only their base64 length — binary payloads never enter model history.

**Design notes.** The MCP boundary is where trust, naming, credentials, and cancellation meet. Read
this module with the specification open — that is the point of it existing.

**Boundary.** The implementation follows the classic handshake. The 2026-07-28 MCP revision makes
the protocol stateless (no `initialize`, no `Mcp-Session-Id`, `server/discover`, MRTR); see
[how this maps to the 2026 ecosystem](#how-this-maps-to-the-2026-agent-ecosystem) and the parity
doc. No OAuth, no resource subscriptions, no auto-reconnect budget yet.

---

### 17. Skills

**What it does.** Progressive disclosure for instructions. A skill is a directory with `SKILL.md`
or a flat Markdown file with frontmatter. Only names and one-line descriptions are advertised; the
body is loaded on demand.

**Try it.**

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

**What to observe.** A persistent `skill-catalog` user message lists available skills; changes
append a replacement catalog; removing all skills explicitly retires old names. The `skill` tool
re-reads the full body every call and wraps it in `<skill_content>`.

**Design notes.**

- Roots, in order: project `.dsh/skills`, `.agents/skills`, configured dirs, `~/.dsh/skills`,
  `~/.agents/skills`, optional bundled root.
- `disable-model-invocation: true` hides the catalog entry and tool but still allows an explicit
  user `/name` gesture; `user-invocable: false` blocks that gesture.
- Unknown paths and tool results cannot forge a user gesture.

**Boundary.** Frontmatter is a scalar parser, not full YAML. No file watching, no Office skills
bundled, no upstream working-directory scope chain.

---

### 18. Schedule

**What it does.** Host-level timers that deliver a prompt to a session on a schedule.

**Try it.**

```bash
minidsh --enable core.schedule --enable tools.schedule \
  run 'schedule create {"title":"Deploy check","prompt":"Check deployment status","rrule":"DTSTART:20990101T090000Z\nRRULE:FREQ=DAILY;COUNT=1","target":"this_session"}'
```

**What to observe.**

- Tasks belong to the host, not to a session, and can be listed across sessions.
- Targets: `this_session` (reuse), `new_once` (create once), `new` (fresh session per delivery).
- Timing forms: `after`, `at`, `every`, `daily`, `weekly`, `cron` (five fields), `rrule` (subset).
- `schedule/inbox` confirms the message was **persisted**, not that the model finished it.
- Missed occurrences after downtime deliver only the most recent one.
- History retains 200 deliveries / 30 days; minimum interval 60 s.

**Design notes.** Scheduling is a durability problem. Idempotency, catch-up policy, and the
delivered/finished distinction are the whole design.

**Boundary.** No RRULE `BYWEEKNO`/`BYYEARDAY`, single-value `BYHOUR`/`BYMINUTE`/`BYSECOND`, one
storage backend.

---

### 19. Goals and automatic rounds

**What it does.** A persistent objective attached to a session, with optional automatic
continuation.

**Try it.**

```bash
minidsh --enable core.goals --enable tools.goal run "goal create finish migration|4"
minidsh --enable core.goals --enable tools.goal --enable core.goal-rounds run "goal create finish migration|4"
minidsh --enable core.goals goal <session-id>
minidsh --enable core.goals goal <session-id> resume
```

**What to observe.**

- At most one current goal per session; `goal/change` stores objective, status, revision, round
  count, and blocker reason.
- Every update requires exact `goal_id` + `revision` — optimistic concurrency, not last-write-wins.
- Restoring, forking, or restarting always leaves the goal **disarmed**; a human must resume it.
- Autonomous `blocked` requires three consecutive rounds and a concrete reason.

**Design notes.** This is the most subtle module in the repo. Long-horizon autonomy needs explicit
state, a budget, and a deliberate re-arm step; without them, "keep going until done" is a loop with
no exit condition.

**Boundary.** No completion evaluator, no subtree idle aggregation, no independent scheduler.

---

### 20. Programmatic tool calling (PTC)

**What it does.** In PTC mode the model sees only `run_code`; every other tool becomes
`await tools.name(args)` inside a TypeScript program.

**Try it.**

```bash
minidsh --enable core.ptc --enable tools.run-code \
  run 'run code {"code":"const text: string = await tools.read({file_path: \"README.md\"}); return text.length","description":"Measure README"}'
```

**What to observe.**

- Calls still go through the same `ToolRegistry`, so approval, cancellation, and events apply.
- `per-call` executes an async function body in a fresh process; `persistent` keeps a Node REPL
  with per-session top-level variables.
- Read-only subcalls run in parallel (default 10); mutating calls are exclusive.
- Defaults: 120 s timeout, 600 s cap, 64 MiB output, 3000 ms stop grace, 128 pending, 1024
  subcalls/minute.
- Error classes: `timeout`, `abort`, `exception`, `protocol`, `worker-exit`, `output-limit`,
  `invalid-output`.

**Design notes.** Code execution collapses multi-step tool churn into one model turn — a large
latency win — at the cost of quotas, isolation, and harder debugging. Both sides of that tradeoff
are implemented here.

**Boundary.** No Acorn source transform, no full cooperative revoke/process-tree cleanup, no PTC
image projection.

---

### 21. Agent presets

**What it does.** Binds a session to a capability profile: which tools exist, and what persona
prefixes/suffixes the prompt.

**Try it.**

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

**What to observe.** `preset/selected` persists the choice. Switching is allowed only before the
first model turn; after that it is refused. Forks and subagents inherit the parent preset. A
preset missing from history fails closed rather than falling back.

**Design notes.** Capability scoping is a product feature, not an optimization. A review agent
should not carry a shell it will never use, and `run_code` loses access to a tool the moment the
preset removes it — because PTC uses the same scoped service context.

**Boundary.** A name-to-plugin mapping, not an upstream YAML loader with hot reload.

---

### 22. HTTP host and Web workbench

**What it does.** A local HTTP + SSE host, and a single-file workbench UI.

**Try it.**

```bash
minidsh serve --port 3080
open http://127.0.0.1:3080
```

**What to observe.**

- `POST /v1/runs/stream` starts a run; the `turn/start` event carries the `run_id` used for
  cancellation.
- Concurrent runs are isolated by `run_id`; two identical prompts still get distinct sessions.
- A session accepts one write turn at a time over HTTP.
- The UI renders a structured trace (model/tool/job/error categories), streams assistant text live,
  and shows pending questions as a modal form.

**Design notes.**

- Default bind is `127.0.0.1`. Binding a non-loopback address without `MINIDSH_TOKEN` refuses to
  start; clients then send `Authorization: Bearer <token>`.
- The workbench has no build step. If the UI needs a private shortcut, the API is missing
  something — a useful pressure test.
- English/中文 switch and light/dark theme are persisted in `localStorage`.

**Boundary.** A learning API, not a multi-tenant gateway. The browser UI does not send custom
Authorization headers.

---

### 23. External plugins

**What it does.** Discovers plugins through the standard `minidsh.plugins` entry point group.

**Try it.**

```bash
python3 -m pip install -e . -e ./examples/minidsh-clock
minidsh plugins                        # example.clock shown as disabled
minidsh --enable example.clock tools   # now it provides a real tool
```

**What to observe.** Disabled plugins expose only distribution metadata — the module is never
imported. Enabling is a second, explicit trust decision.

**Design notes.** The entry point may export a `Plugin` instance, a subclass, or a factory. The
entry point name must equal `plugin.name` and must not shadow a built-in feature. Uninstalling a
package without deleting its config entry fails closed at startup.

**Boundary.** No hot reload, no versioned plugin API, no sandbox for plugin code — enabling a
plugin gives it the host process's privileges.

---

### 24. Product distributions

**What it does.** Splits the product shells from the core.

| Package | Command | Extra runtime |
|---|---|---|
| `minidsh` / `minidsh-headless` | `minidsh`, `minidsh-headless` | none (stdlib only) |
| `minidsh-web` | `minidsh-web` (defaults to `serve`) | none |
| `minidsh-desktop` | `minidsh-desktop` (defaults to `desktop`) | `pywebview` |

```bash
python3 -m pip install -e . -e ./packages/minidsh-web
minidsh-web --port 3080

python3 -m pip install -e '.[desktop]' -e ./packages/minidsh-desktop
minidsh-desktop
```

**What to observe.** Importing the CLI, listing plugins, or running headless/Web never imports
`pywebview`. Only the Desktop command reports a missing GUI dependency, and only when used.

**Design notes.** Heavy optional dependencies belong at the product edge. A harness that requires a
browser toolkit to run a Bash command is a packaging failure.

---

### 25. Real DeepSeek adapter

**What it does.** Implements the DeepSeek Messages protocol: SSE streaming, thinking blocks with
replay signatures, tool calls, images, retries, and model listing.

**Try it.**

```bash
export DEEPSEEK_API_KEY='...'
minidsh --disable llm.mock --enable llm.deepseek run "read README.md"

minidsh --disable llm.mock --enable llm.deepseek models
minidsh --disable llm.mock --enable llm.deepseek files
```

**What to observe.**

- Request path is `/v1/messages` (or `/messages` when the base URL already ends in `/v1`).
- Headers: `x-api-key`, `anthropic-version: 2023-06-01`.
- `deepseek-messages` replay envelope and reasoning signatures are preserved; switching providers
  drops foreign signatures.
- Retries emit `llm/retry` and back off exponentially with jitter, honouring a bounded
  `Retry-After`.
- Images prefer the Files API in `auto` mode and fall back to inline base64; JSONL stores only the
  path, media type, size, and SHA-256.

**Design notes.** Most "works on the first turn, breaks on resume" bugs live in adapter protocol
accounting: content block order, signature replay, usage accounting, and error taxonomy. Keeping
that logic in one module makes it auditable.

**Boundary.** Real online acceptance has not been run in this repository; local Messages/SSE
end-to-end tests have. The adapter is a learning slice, not a compatibility guarantee. Never expose
this host to the public internet.

---

## Configuration reference

### File configuration

`config.example.json` contains every flag. File config is merged first; `--enable` / `--disable`
on the command line are applied afterwards, so the CLI always wins.

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

### Feature flags

**Core services**

| Flag | Default | Function |
|---|---|---|
| `core.system-prompt` | on | Identity block, tool guidance sections, runtime context |
| `core.tools` | on | Tool registry and execution pipeline |
| `core.sandbox-policy` | on | Workspace boundary and sandbox modes |
| `core.approval` | on | Approval service and audit events |
| `core.fs-observation` | on | Per-session file version tracking |
| `core.jobs` | on | Process lifecycle, output cursors, isolation |
| `core.compaction` | on | Token estimate, summary, checkpoints |
| `core.cancellation` | on | Run cancellation propagation |
| `core.streaming` | on | Incremental model deltas |
| `core.stream-recording` | on | Compressed raw chunk persistence |
| `core.sse-replay` | on | In-process SSE history ring |
| `core.pty` | on | Unix PTY service for persistent shells |
| `core.kernel-sandbox` | **off** | macOS Seatbelt isolation |
| `core.tool-result-pruner` | **off** | Manual tool-result pruning |
| `core.user-questions` | on | Question service and pending queue |
| `core.web` | on | Web fetch/search service |
| `core.subagents` | **off** | Local child agents |
| `core.workflow` | **off** | Node workflow engine |
| `core.mcp` | **off** | MCP client and manager |
| `core.skills` | **off** | Skill registry and discovery |
| `core.schedule` | **off** | Host scheduler |
| `core.goals` | **off** | Persistent goals |
| `core.goal-rounds` | **off** | Automatic goal rounds |
| `core.ptc` | **off** | Programmatic tool calling |
| `core.agent-presets` | **off** | Per-session capability profiles |
| `agent.core` | on | The agent loop itself |

**Tools**

| Flag | Default | Tools registered |
|---|---|---|
| `tools.read` | on | `read` |
| `tools.write` | on | `write` |
| `tools.edit` | on | `edit` |
| `tools.glob` | on | `glob` |
| `tools.bash` | on | one-shot `bash` |
| `tools.bash-persistent` | **off** | persistent `bash` (auto-disables `tools.bash`) |
| `tools.jobs` | on | `job_output`, `job_list`, `job_kill` |
| `tools.ask-user` | **off** | `ask_user_question` |
| `tools.ask-user-timed` | **off** | timed questions with pending answers |
| `tools.todo` | **off** | `todo_write` |
| `tools.web-fetch` | **off** | `web_fetch` |
| `tools.web-search` | **off** | `web_search` |
| `tools.subagent` | **off** | `subagent` |
| `tools.subagent-fork` | **off** | `subagent_fork` |
| `tools.subagent-control` | **off** | `send_message`, `interrupt_agent`, `list_agents`, `wait_agent` |
| `tools.workflow` | **off** | `workflow` |
| `tools.mcp-resources` | **off** | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` |
| `tools.skill` | **off** | `skill` |
| `tools.schedule` | **off** | `schedule_create/list/view/edit` |
| `tools.goal` | **off** | `get_goal`, `create_goal`, `update_goal` |
| `tools.run-code` | **off** | `run_code` |

**Storage and model**

| Flag | Default | Function |
|---|---|---|
| `session.jsonl` | on | JSONL generations, migration, recovery, locking |
| `llm.mock` | on | Deterministic offline model |
| `llm.deepseek` | **off** | Real Messages adapter |
| `llm.retry` | on | Bounded retry with backoff |
| `llm.model-listing` | on | Model list query and cache |
| `llm.files` | on | Files API upload, reuse, listing |

Disable every model and `agent.core` stays `pending` because its `llm` dependency is missing —
that is the dependency model working as designed.

### Environment variables

**Model and transport**

| Variable | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | Credential; read from env only, never written to disk |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/anthropic` | Messages root |
| `DEEPSEEK_MODEL` | `deepseek-flash` | Model id |
| `DEEPSEEK_REASONING_EFFORT` | `high` | `off` / `low` / `high` / `max` |
| `DEEPSEEK_MAX_TOKENS` | `256000` | Response cap |
| `DEEPSEEK_STREAM_IDLE_TIMEOUT_MS` | `300000` | SSE idle timeout |
| `DEEPSEEK_MODELS_TIMEOUT_MS` | `5000` | Model list timeout |
| `DEEPSEEK_MODELS_MAX_AGE_MS` | `600000` | List cache TTL (`0` = always refresh) |
| `DEEPSEEK_RETRY_MODE` | `normal` | `normal` or `always` |
| `DEEPSEEK_RETRY_MAX` | `5` | Max retries |
| `DEEPSEEK_RETRY_INITIAL_DELAY_MS` | `500` | First backoff |
| `DEEPSEEK_RETRY_MAX_DELAY_MS` | `10000` | Backoff ceiling |
| `DEEPSEEK_RETRY_JITTER_RATIO` | `0.1` | Symmetric jitter |
| `DEEPSEEK_IMAGE_MODE` | `auto` | `auto` / `inline` / `required` |
| `DEEPSEEK_FILES_API_TIMEOUT_MS` | `60000` | Files API timeout |
| `DEEPSEEK_FILE_EXPIRY_SECONDS` | `604800` | Upload lifetime (3600–2592000) |

**Harness behaviour**

| Variable | Default | Meaning |
|---|---|---|
| `MINIDSH_TOKEN` | — | Required when binding a non-loopback host |
| `MINIDSH_SUBAGENT_MAX_DEPTH` | `1` | Delegation depth (`0` disables) |
| `MINIDSH_GOAL_MAX_ROUNDS` | `256` | Default goal round cap |
| `MINIDSH_ASK_TIMEOUT_SECONDS` | `120` | Timed question wait |
| `MINIDSH_TODO_ALLOW_PARALLEL` | `0` | Allow multiple `in_progress` todos |
| `MINIDSH_SCHEDULE_NEW_INTERVAL_SECONDS` | `60` | Minimum schedule interval |
| `MINIDSH_WEB_TIMEOUT_MS` | `30000` | Fetch timeout |
| `MINIDSH_WEB_FETCH_MAX_OUTPUT_CHARS` | `200000` | Fetch output cap |
| `MINIDSH_WEB_SEARCH_URL` | — | Search provider endpoint |
| `MINIDSH_WEB_ALLOW_PRIVATE` | `0` | Allow private addresses (local testing) |
| `MINIDSH_PERSISTENT_TIMEOUT_MS` | `300000` | PTY command timeout |
| `MINIDSH_PERSISTENT_MAX_OUTPUT_CHARS` | `16000` | PTY output tail |
| `MINIDSH_PRUNE_THRESHOLD_CHARS` | `8192` | Prune trigger |
| `MINIDSH_PRUNE_HEAD_CHARS` | `4096` | Head kept |
| `MINIDSH_PRUNE_TAIL_CHARS` | `1024` | Tail kept |
| `MINIDSH_SKILL_DESCRIPTION_MAX` | `500` | Skill description limit |
| `MINIDSH_WORKFLOW_MAX_AGENTS` | `1000` | Workflow agent budget |
| `MINIDSH_WORKFLOW_MAX_CONCURRENT` | `min(32, cpu)` | Workflow concurrency |
| `MINIDSH_PTC_TIMEOUT_MS` | `120000` | `run_code` timeout |
| `MINIDSH_PTC_MAX_TIMEOUT_MS` | `600000` | `run_code` timeout cap |
| `MINIDSH_PTC_MAX_OUTPUT_BYTES` | `67108864` | `run_code` output cap |
| `MINIDSH_PTC_MAX_PARALLEL` | `10` | Parallel read-only subcalls |
| `MINIDSH_PTC_MAX_PENDING` | `128` | Pending subcall limit |
| `MINIDSH_PTC_MAX_SUBCALLS_PER_MINUTE` | `1024` | Rate budget |
| `MINIDSH_PTC_STOP_GRACE_MS` | `3000` | Grace before hard kill |
| `MINIDSH_PTC_PERSISTENT` | `0` | Default to persistent REPL |

---

## Event reference

Sessions are event logs. These are the event types this harness writes:

| Group | Events |
|---|---|
| Turn and step | `turn/start`, `turn/end`, `step/start`, `step/end` |
| Messages | `system/message`, `user/message`, `assistant/message`, `assistant/delta`, `assistant/usage` |
| Tools | `tool/call`, `tool/result`, `tools/pre-execute`, `tools/post-execute` |
| Approval | `approval/asked`, `approval/decided`, `approval/policy` |
| Files | `fs/observed` |
| Session | `session/start`, `session/resume`, `session/fork`, `session/migrated`, `session/repaired` |
| Process | `job/registered`, `job/settled`, `pty/created`, `pty/reset` |
| Memory | `compaction/start`, `compaction/end`, `compaction/prune` |
| Sandbox | `sandbox/mode` |
| Interaction | `question/asked`, `question/answered`, `todo/write` |
| Delegation | `subagent/start`, `subagent/end`, `subagent/catalog` |
| Workflow | `workflow/start`, `workflow/phase`, `workflow/log`, `workflow/agent-start`, `workflow/agent-end`, `workflow/end` |
| Goals | `goal/change`, `goal/changed`, `goal/activation-changed` |
| Schedule | `schedule/changed`, `schedule/inbox` |
| Model transport | `llm/retry`, `llm/image-upload`, `llm/image-fallback` |
| Presets | `preset/selected` |
| Runs | `run/result`, `run/error`, `run/cancelled`, `run/done` |

```bash
minidsh session <session-id>     # raw events
minidsh transcript <session-id>  # human-readable projection
minidsh surface <session-id>     # what the model currently sees
```

---

## How this maps to the 2026 agent ecosystem

MiniDSH is deliberately small, but its seams line up with what production agent platforms converged
on. Each row is a place where you can compare the teaching implementation with the current
industry answer.

| Practice in 2026 | Where it lives here | What the lab teaches |
|---|---|---|
| **Harness engineering** as its own discipline ([harness chapter](https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md)) | Entire repo | The harness, not the model, owns ordering, limits, persistence, and failure handling |
| **AGENTS.md** as the cross-vendor agent instruction file ([agents.md](https://agents.md)) | [`AGENTS.md`](AGENTS.md) | Instructions for coding agents live in a predictable place, separate from the human README |
| **Agent Skills** with progressive disclosure ([spec](https://agentskills.io/specification)) | `skills.py` | Advertise name + description; load the body only when the task needs it |
| **MCP** as the tool interop layer ([spec](https://modelcontextprotocol.io/specification/2026-07-28/changelog)) | `mcp.py` | Namespacing, credential redaction, cancellation, fail-closed discovery |
| **MCP 2026-07-28 statelessness** | Parity doc, boundaries | The classic handshake is implemented; the new stateless model is documented as a gap |
| **Context engineering** (compaction, observation masking, pruning) | `compaction.py`, `surface.py`, `pruner.py` | Compact the view, never the log |
| **Event sourcing for agents** | `session.py`, `generations.py` | Replay, audit, fork, and crash repair from one append-only log |
| **Sandboxing** (Seatbelt / Landlock / bwrap) ([discussion](https://github.com/zeroclaw-labs/zeroclaw/blob/a56c345d51dd8ab562e9351e0d4ab83f6a741db9/docs/book/src/security/sandboxing.md)) | `seatbelt.py` | Policy at the prompt, approval in the loop, enforcement in the kernel |
| **Kernel-level isolation** vs. prompt-level rules | `sandbox.py` + `seatbelt.py` | "Please don't write" is not a boundary; `Operation not permitted` is |
| **Subagents as context isolation** | `subagents.py` | Parent receives a summary, not the child transcript |
| **Deterministic orchestration** vs. agentic control flow | `workflow.py` | Use a script when the graph is known; use an agent when it is not |
| **Programmatic tool calling** | `ptc.py` | One code turn instead of ten tool turns, with quotas and isolation |
| **OpenTelemetry GenAI semantic conventions** ([spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/)) | `events.py` + `server.py` | Session/run/turn/step/attempt correlation is already span-shaped; export is the missing edge |
| **Human-in-the-loop with async answers** | `questions.py` | Late answers, pending states, and "skip is not approval" |
| **Long-horizon goals with budgets** | `goals.py` | Revision-checked state, disarm-on-restore, explicit blocked conditions |
| **Durable scheduling** | `schedule.py` | Delivered ≠ completed; catch-up policy matters |
| **Evaluation** (Terminal-Bench, τ²-bench style) ([leaderboard](https://benchlm.ai/benchmarks/terminal-bench-2)) | `docs/exercises.md` | Deterministic mock trajectories make regressions measurable |
| **Agent-to-agent interop** (A2A v1.0) ([announcement](https://agent2agent.info/blog/a2a-v1-announcement/)) | Boundary | In-process subagents today; A2A is a documented next edge |
| **Prompt-injection defense** | `web_tools.py` | External content is data, never instructions; budgets enforced at the tool |
| **Capability scoping** | `presets.py` | A reviewer agent should not carry a shell |

Two honest notes:

1. The **MCP specification moved in 2026-07-28**. MiniDSH implements the handshake-based protocol
   that most existing servers still speak; the parity document records the stateless revision as a
   known gap rather than pretending otherwise.
2. **Telemetry is span-shaped but not exported.** Events already carry the identity fields needed
   for OTel GenAI spans; wiring an exporter is a deliberate exercise, not an unfinished feature.

---

## The 27-layer learning path

Stages are numbered 0–26. Read them in order the first time; jump afterwards. Each layer names the module and the
question it answers.

| # | Layer | Module | Question it answers |
|---|---|---|---|
| 0 | Plugin kernel | `config.py`, `plugins.py` | How do features become services, and why do disabled features disappear cleanly? |
| 1 | Model adapter | `llm.py`, `deepseek.py` | How does a streaming protocol become content blocks and tool calls? |
| 2 | Approval and writes | `sandbox.py`, `approval.py`, `observations.py` | Why is "read before write" a correctness rule? |
| 3 | Shell and jobs | `shell.py`, `jobs.py` | What does a foreground timeout do to a running process? |
| 4 | Resume and compaction | `session.py`, `compaction.py` | Why does the projection change while history does not? |
| 5 | HTTP and hosting | `server.py` | How do concurrent runs stay isolated? |
| 6 | External plugins and products | `discovery.py`, `packages/` | How does a plugin ship without touching core? |
| 7 | Retry and cancellation | `retry.py`, `cancellation.py` | Which partial output must be discarded after a retry? |
| 8 | Images and Files API | `images.py`, `files.py` | Why is content addressing the right key for uploads? |
| 9 | Persistent shell and PTY | `pty_shell.py` | How do you keep shell state without keeping a process forever? |
| 10 | Migration and crash recovery | `generations.py`, `session_recovery.py` | How do you recover without inventing success? |
| 11 | Surface and pruning | `surface.py`, `pruner.py` | How can the model view shrink while the audit log does not? |
| 12 | Questions, todos, web tools | `questions.py`, `todo.py`, `web_tools.py` | How does a human answer arrive late and still be valid? |
| 13 | Streaming and backpressure | `stream_codec.py`, `server.py` | What happens to a slow SSE client? |
| 14 | Stream recording and replay | `stream_codec.py` | How do you resume a stream without replaying a whole run? |
| 15 | Timed questions | `questions.py` | What is the difference between pending, skipped, and approved? |
| 16 | Immutable generations | `generations.py` | Why is a manifest with a hash better than a mutable file? |
| 17 | Kernel sandbox | `seatbelt.py` | Where does a real permission denial come from? |
| 18 | Subagents | `subagents.py` | Why is context isolation the point of delegation? |
| 19 | Workflow | `workflow.py`, `workflow_guest.js` | When should orchestration be code instead of model turns? |
| 20 | Workflow jobs and recovery | `jobs.py`, `workflow.py` | How does a background run survive a host restart? |
| 21 | MCP | `mcp.py` | How do you integrate third-party tools without trusting them blindly? |
| 22 | Skills | `skills.py` | How much context should be loaded up front? |
| 23 | Schedule | `schedule.py`, `schedule_time.py` | What is the difference between delivered and done? |
| 24 | Goals | `goals.py` | What stops an autonomous loop from running forever? |
| 25 | PTC | `ptc.py`, `ptc_runner.js` | What do you gain and lose by letting the model write code? |
| 26 | Presets and persona | `presets.py` | How do you scope capability per session? |

Each layer has a matching exercise in [docs/exercises.md](docs/exercises.md).

---

## Debugging cookbook

| Symptom | First command | What to look for |
|---|---|---|
| The model never calls a tool | `minidsh tools` | Is the tool in the schema? Is its flag on? |
| The prompt mentions a tool that does not exist | `minidsh prompt` | Guidance is registered by the tool plugin — check for a stale section |
| A write was refused | `minidsh session <id>` | `approval/decided` outcome: `rejected`, `unavailable`, or `cancelled` |
| A write failed even after approval | `minidsh transcript <id>` | Missing or invalidated `fs/observed` observation |
| A resumed session behaves differently | `minidsh surface <id>` | A replacement or compaction changed the model view |
| Context explodes immediately | `minidsh --context-window 100 run ...` | Watch `compaction/start` and the summary shape |
| A background job seems hung | `minidsh session <id>` | `job/registered` without `job/settled` |
| A session will not load | `minidsh repair <id>` | Truncated tail, open turn, or hash mismatch |
| The agent keeps looping | `minidsh goal <id>` | Goal armed? Round budget? `blocked_reason`? |
| Streaming text is duplicated | `minidsh stream <id>` | Retry attempts; consumers must reset per `attempt` |

---

## Boundaries and non-goals

Honest limits, restated so nobody has to discover them the hard way:

- **Format.** Session v2 is a learning generation format, not the upstream Zstandard multi-generation
  format. No Windows write lock.
- **Sandboxing.** macOS Seatbelt only. No Linux Landlock/bwrap, no Windows isolation.
- **PTY.** Unix `/bin/bash` only. No ConPTY, no cross-process terminal recovery.
- **MCP.** Handshake-based protocol; the 2026-07-28 stateless revision is not implemented. No OAuth,
  no resource subscriptions, no reconnect budget.
- **Subagents.** In-process only. No ACP/Codex/Claude Code/remote SDK backends.
- **Telemetry.** Events are span-shaped but no OTel exporter ships.
- **Tokens.** Heuristic estimation, not a tokenizer; no image token pricing.
- **Approval.** A human control, not a security boundary; pair it with the kernel sandbox.
- **Online acceptance.** Real DeepSeek calls have not been validated in CI. Keys are read from the
  environment only.
- **Scope.** This is teaching code. Do not expose the HTTP host to the public internet.

The complete, bullet-by-bullet boundary list is in
[docs/upstream-parity.md](docs/upstream-parity.md).

---

## FAQ

**Is this a framework I should build a product on?**
No. It is a reading and experimentation instrument. Copy the patterns, not the process.

**Why a deterministic mock model?**
Because agent bugs are trajectory bugs. With a mock you can assert on the exact sequence of events;
with a real model you can only assert on the ending.

**Why is everything off by default?**
Each advanced layer widens the attack surface. Turning features on one at a time is how you learn
what each one actually costs.

**Do I need an API key?**
No. Every default path works offline. The DeepSeek adapter is opt-in.

**Why Chinese aliases in the mock grammar?**
The project started in Chinese, and real sessions mix languages. Aliases parse to the *same*
intents, so nothing downstream changes.

**How do I add a tool?**
See [`examples/minidsh-clock`](examples/minidsh-clock) and [docs/modules.md](docs/modules.md). The
short version: register a `Plugin` with `provides=("tools",)`, declare a schema, and call
`ctx.require("tools").register(...)`. Approval, policy, and events come for free.

**Where do I start if I only have an hour?**
Layers 0 → 1 → 2 → 3, then run the Web workbench and watch a session end to end.

---

## Documentation index

| Document | Language | Purpose |
|---|---|---|
| [README.md](README.md) | English | This guide |
| [README.zh-CN.md](README.zh-CN.md) | 中文 | Chinese guide |
| [docs/index.html](docs/index.html) | EN / 中文 | Textbook site: 31 chapters, each with concept, rationale, implementation, verification, cost and industry reference |
| [docs/modules.md](docs/modules.md) | English | Per-module role, contracts, traps |
| [docs/exercises.md](docs/exercises.md) | English | Runnable labs tied to current practice |
| [docs/mock-model.md](docs/mock-model.md) | English | Deterministic prompt grammar |
| [docs/upstream-parity.md](docs/upstream-parity.md) | English | What matches upstream and what does not |
| [docs/upstream-parity.zh-CN.md](docs/upstream-parity.zh-CN.md) | 中文 | Same report in Chinese |
| [AGENTS.md](AGENTS.md) | English | Instructions for coding agents |
| [config.example.json](config.example.json) | — | Every flag with defaults |
| [LICENSE](LICENSE) | — | MIT license |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | English | Upstream attribution and derived prose |

## Publishing the documentation site

`docs/index.html` is a self-contained bilingual site (no build step, no external assets except the
two screenshots in `docs/assets/`). The workflow at `.github/workflows/pages.yml` publishes `docs/`
to GitHub Pages on every push that touches it. It is live at
<https://norwinyu.github.io/dsh-python-lab/>.

**One-time setup (needed for a fork):**

1. Open **Settings → Pages**.
2. Under *Build and deployment*, set **Source** to **GitHub Actions**.
3. Re-run the `Pages` workflow (or push another change under `docs/`).

Until that switch is flipped, the `Configure Pages` step fails with a 404 — the workflow itself is
correct, Pages is just not enabled yet.

## License

MIT — see [LICENSE](LICENSE).

This project is an independent re-implementation, not a fork. It does reproduce a small amount of
upstream **prose** (the identity sentence, tool descriptions, tool guidance, and compaction summary
headings) so that behaviour can be compared line by line. DeepSeek Harness is MIT licensed,
Copyright (c) 2026 DeepSeek, so that reuse is permitted with attribution. What is derived, and the
upstream license text, are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

If you build something on top of MiniDSH, keep both notices: this repository's `LICENSE` and the
third-party notice.
