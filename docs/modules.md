# Module Atlas

Every source file in MiniDSH has one job, and that job is small enough to read in one sitting.
This atlas explains what each module is responsible for, which contracts it must respect, and
what you should observe while reading it.

Use it as a map, not a substitute for the code: the exact limits, event names, and failure
messages live in the source and in [`upstream-parity.md`](upstream-parity.md).

## How to read the atlas

Each entry follows the same shape:

- **Role** — the single responsibility of the module.
- **Key contracts** — types, services, files, or invariants other modules rely on.
- **Learn this** — the concept the module exists to demonstrate.
- **Watch out for** — the boundary that is easy to misread.

The dependency direction is always:

```text
config → plugins → app (composition root) → agent runtime → tools/host products
```

Nothing below `app.py` imports a product entry point (`cli.py`, `server.py`, `desktop.py`),
and no tool reaches into the Agent. Services are resolved through `ServiceContext`, so a
disabled plugin is simply absent instead of being a special case.

---

## 1. Kernel and composition

### `src/minidsh/config.py`

**Role** — Parse, merge, validate, and freeze configuration before any plugin exists.

**Key contracts**

- `AppConfig` is the single source of truth for `workspace`, `session_dir`, sandbox mode,
  approval policy, context window, MCP server definitions, skill directories, presets, and
  the `features` dictionary.
- File configuration is loaded first; `--enable` / `--disable` flags are applied afterwards,
  so the command line always wins.
- Unknown feature names or invalid combinations fail at startup rather than being ignored.
- `resolved_approval_policy()` folds `approve_all` (`--yes`) into the effective policy.

**Learn this** — Explicit configuration is a safety property. A harness that silently accepts
typos in tool names will one day run a tool you thought was disabled.

**Watch out for** — Feature flags gate *registration*, not just visibility. Disabling a tool
removes its schema, its prompt section, and its service provider in one step.

### `src/minidsh/plugins.py`

**Role** — The service container and plugin lifecycle.

**Key contracts**

- `ServiceContext` maps a service name to exactly one provider. A second provider raises.
- `Plugin` declares `name`, `provides`, `requires`, `activate`, and `deactivate`.
- `PluginManager.activate_all()` repeatedly scans pending plugins until no progress is made.
  Plugins whose dependencies are missing stay `pending` (or become `error` if activation
  itself fails).
- Deactivation runs in reverse registration order.
- `DisabledExternalPlugin` is a placeholder that makes "installed but not enabled" visible
  without importing third-party code.

**Learn this** — Dependency-driven activation is a tiny, inspectable alternative to a
heavyweight DI framework, and it makes "why is this feature off?" answerable with
`minidsh plugins`.

**Watch out for** — There is no hot reload. The service graph converges once at startup.

### `src/minidsh/discovery.py`

**Role** — Discover external plugins through `importlib.metadata` entry points.

**Key contracts**

- Group name is fixed: `minidsh.plugins`.
- While disabled, only distribution metadata is read; the module is never imported.
- The entry point can export a `Plugin` instance, a `Plugin` subclass, or a factory.
- The entry point name must equal `plugin.name`, and it must not shadow a built-in feature.

**Learn this** — Install-time discovery plus opt-in activation is the minimum viable plugin
security model. Installing a package is a trust decision; enabling it is a second one.

**Watch out for** — Removing a plugin package without removing its config entry fails closed.

### `src/minidsh/app.py`

**Role** — The one and only composition root.

**Key contracts**

- Registers every built-in plugin in a deliberate order, then external entry points.
- `AgentPlugin` constructs the `Agent` from optional services: session, observations,
  compaction, subagents, skills, schedule, goals, PTC, presets.
- `Application` exposes read-only accessors (`agent`, `tools`, `system_prompt`,
  `runtime_context`, `session`) so CLI, HTTP, and Desktop share one object graph.
- `close()` deactivates plugins in reverse order and releases processes.

**Learn this** — A harness is an assembly problem before it is a prompting problem. One
composition root keeps CLI, Web, and Desktop behaviour identical.

**Watch out for** — `Application` never imports `pywebview`; the Desktop product does.

### `src/minidsh/events.py`

**Role** — A synchronous, in-process publish/subscribe bus.

**Key contracts**

- Subscribers receive `(event_type, payload)`.
- Emission is synchronous; a slow subscriber slows the emitter. Durable buffering is the
  job of `server.py` (SSE broker), not the bus.

**Learn this** — Events are the observability seam: tools, sessions, jobs, and approvals all
report through the same channel.

---

## 2. Agent runtime

### `src/minidsh/agent.py`

**Role** — The turn/step loop that turns a prompt into model calls, tool calls, and events.

**Key contracts**

- One `run()` is one turn. `max_steps` (default 4) bounds model→tool→model iterations.
- Before the first model call it assembles: system prompt sections, runtime context
  (workspace, sandbox policy, approval policy), skill catalog, and schedule inbox messages.
- It derives history from the session projection (surface-aware), not from raw files.
- Tool results are appended as `tool/result` and fed back to the model.
- Streaming deltas (`assistant/delta`) are forwarded with session/run/turn/step/attempt
  metadata; the final message stays authoritative.
- Compaction runs before a request when the estimated context crosses the pressure threshold.
- Cancellation is checked at model, tool, and shell boundaries.
- After a step it consumes subagent completion notifications and schedule messages.

**Learn this** — The agent loop is deliberately boring. Intelligence lives in the model; the
harness owns ordering, limits, persistence, and failure handling.

**Watch out for** — `max_steps` is a budget, not a guarantee of completion. A turn can end
with `max_steps` reached and partial work done.

### `src/minidsh/system_prompt.py`

**Role** — Compose the system prompt from ordered sections.

**Key contracts**

- Identity section is fixed: `You are an AI agent powered by DeepSeek Harness.`
- Tool guidance sections are registered by tool plugins with an order and are removed with
  the tool.
- Runtime context is a separate user snapshot placed after the user instruction, not part of
  the static system prompt.
- `write` guidance changes when `edit` is enabled (the upstream "prefer edit" clause).

**Learn this** — Prompts are assembled from capability declarations. Turning a tool off must
turn its instructions off too, otherwise the model is told to use a tool it cannot call.

### `src/minidsh/llm.py`

**Role** — Model abstraction plus the deterministic offline model.

**Key contracts**

- `Model.complete()` returns `ModelResponse` blocks (`text`, `reasoning`, `tool-call`).
- `MockModel` uses an explicit prefix grammar, parsed into an `Intent` (English and Chinese).
- No natural-language understanding: the same prompt always produces the same tool call.
- Compaction prompts are answered with a fixed summary shape.

**Learn this** — A deterministic model is the single most valuable testing tool in an agent
repo. It makes trajectories reproducible and lets you assert on events.

**Watch out for** — Mock triggers are a lab fixture, not a product feature. Real models infer
intent; the mock refuses to.

### `src/minidsh/deepseek.py`

**Role** — The real DeepSeek Messages adapter.

**Key contracts**

- Base URL defaults to `https://api.deepseek.com/anthropic`; requests go to `/v1/messages`
  (or `/messages` when the base already ends with `/v1`).
- Headers: `x-api-key`, `anthropic-version: 2023-06-01`.
- Defaults: model `deepseek-flash`, thinking effort `high`, `max_tokens` 256000, stream idle
  timeout 300000 ms.
- Serializes text, reasoning, tool_use/tool_result, JSON Schema, and image blocks.
- Parses SSE text/thinking/signature/input_json_delta/usage/stop events.
- Preserves the `deepseek-messages` replay envelope; drops foreign reasoning signatures when
  switching providers.
- Never follows HTTP redirects (credentials must not leak across origins).
- Delegates Files API work to `files.py` and retry decisions to `retry.py`.

**Learn this** — Provider adapters are mostly protocol accounting: content block order,
signature replay, usage, and error taxonomy. This is where most "works on one turn, breaks on
resume" bugs live.

**Watch out for** — The adapter is a learning slice, not a claim of byte-for-byte compatibility
with every upstream feature.

### `src/minidsh/retry.py`

**Role** — Bounded retry policy for transient model failures.

**Key contracts**

- Error classes: `EMPTY_RESPONSE`, `RATE_LIMIT`, `SERVER`, `TIMEOUT`, `TRANSPORT`.
- Exponential backoff from 500 ms to 10000 ms, 10% symmetric jitter, bounded `Retry-After`.
- `normal` (default) retries at most 5 times; `always` keeps retrying and is only for
  cancellation-aware tests.
- Each attempt emits `llm/retry`.

**Learn this** — Retries are a latency/cost tradeoff. The harness must expose the attempt
number so consumers can discard failed partial output.

### `src/minidsh/cancellation.py`

**Role** — Cooperative cancellation shared by model, tools, and shells.

**Key contracts**

- `RunCancelled` is raised at checkpoints; `CancellationToken` carries callbacks.
- Closing a response stream, killing a process group, and aborting a wait all register here.
- A cancelled waiting `job_output` does not kill the observed background job.

**Learn this** — Cancellation must be cooperative and resource-aware. Python threads cannot be
forcibly stopped safely, so every long operation needs a checkpoint.

### `src/minidsh/images.py` and `src/minidsh/files.py`

**Role** — Image attachments and DeepSeek Files API integration.

**Key contracts**

- Magic-byte detection for PNG/JPEG/GIF/WebP; 32 MiB per image, 600 images per request,
  128 MiB raw total, 20 MiB inline base64.
- JSONL stores path, media type, size, and SHA-256 — never base64.
- Files upload uses multipart, `files-api-2025-04-14`, and a default 7-day lifetime.
- `auto` prefers upload and falls back inline; `required` fails closed; `inline` never uploads.
- Cache key is route + credential hash + image hash; in-flight uploads share a lock.

**Learn this** — Binary assets need content addressing and explicit budgets; otherwise session
files silently become multi-megabyte blobs.

### `src/minidsh/stream_codec.py`

**Role** — Compress and validate recorded model stream chunks.

**Key contracts**

- Adjacent text/reasoning chunks become arrays plus `time0`/`dt`.
- Tool-call chunks keep index/id/name and argument fragments.
- Usage, block-start, and finish stay raw.
- `expand_stream()` validates shape, lengths, timing, and call identity; corruption fails the
  session read.

**Learn this** — Full fidelity is expensive. Store the minimum that can rebuild the stream and
verify it on read.

### `src/minidsh/compaction.py` and `src/minidsh/pruner.py`

**Role** — Context reduction without destroying evidence.

**Key contracts**

- Compaction triggers at 80% of the context window and keeps roughly 16% of the tail.
- It writes `compaction/start` and `compaction/end`, and the summary is marked
  `<compacted-summary>`.
- Token accounting is a heuristic estimate, not a tokenizer.
- Pruning is manual and disabled by default; default threshold 8192, head 4096, tail 1024
  Unicode code points.
- Pruning appends `compaction/prune` plus a `tool/result` replacement that only changes
  `content`.

**Learn this** — The model sees a projection; the session keeps facts. "Compact the view, not
the log" is the central persistence idea of the project.

---

## 3. Tools, permissions, and processes

### `src/minidsh/tools.py`

**Role** — Tool schemas, execution pipeline, and the four filesystem tools.

**Key contracts**

- `ToolRegistry` owns definitions and dispatch; every call passes policy, approval, and
  observation checks.
- `read`: 2000 lines, 2000 chars per line, 50 KiB, line numbers, wrapper envelope.
- `glob`: max 100 results, hidden files included, VCS directories excluded, sorted by mtime.
- `write`: create/overwrite, `Created file` / `Updated file` envelope, atomic replace.
- `edit`: literal unique match, `replace_all` option, same-session read observation required.
- Paths are resolved against the workspace; symlink and `..` escapes are rejected in
  `workspace-write`.

**Learn this** — Tool descriptions are API design. Vague schemas produce invalid calls;
precise envelopes make failures debuggable.

**Watch out for** — Reading an existing file is mandatory before overwrite. External
modification invalidates the observation and blocks the write.

### `src/minidsh/sandbox.py`, `approval.py`, `observations.py`, `seatbelt.py`

**Role** — The permission stack.

**Key contracts**

- Sandbox modes: `read-only` (default), `workspace-write`, `danger-full-access`.
- Approval policies: `ask` (default), `never`; outcomes include `allowed-once`, `rejected`,
  `unavailable`, `cancelled`.
- `approval/asked` and `approval/decided` are paired audit events.
- Observations record file version identity per session and survive resume.
- Seatbelt (macOS) enforces the sandbox at kernel level for bash, jobs, and PTY; refusals
  surface as `Operation not permitted` from the child process, not as harness messages.

**Learn this** — Prompt-level "please don't write" is not a security boundary. Layer policy,
approval, and OS isolation, and fail closed when an approval answerer is missing.

### `src/minidsh/shell.py`, `jobs.py`, `pty_shell.py`

**Role** — Command execution and long-lived terminals.

**Key contracts**

- `bash`: a new `bash -c` per call; output includes stdout, marked stderr, and
  `[exit code: N]`.
- Foreground timeout moves the same job to the background instead of killing it.
- Jobs: per-session isolation, max 10 running/stopping, 256 KiB memory ring shrinking to
  16 KiB after the first terminal read, full stdout/stderr on disk, process-group kill.
- Persistent bash: one `/bin/bash` PTY per session, serialized commands, random completion
  markers, `[Command finished with exit code N]`, cwd/env/functions preserved.
- PTY resets on timeout, cancel, `exit`, HTTP kill, or app shutdown.
- Environment scrubbing removes `*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, and `DSH_*`,
  then injects managed `DSH_HOME` and `DSH_SESSION_ID`.

**Learn this** — Processes are the most dangerous tool. Job identity, output cursors, process
groups, and cleanup are the difference between a useful agent and a fork bomb.

---

## 4. Persistence and projection

### `src/minidsh/session.py`

**Role** — Durable, event-sourced session storage.

**Key contracts**

- New sessions use format v2: immutable generations under
  `.minidsh/sessions/<id>/generations/NNNNNNNN.jsonl` plus an `index.json` manifest with the
  current generation, relative path, and SHA-256.
- Each append publishes a new generation; old generations are read-only.
- Loading validates session identity, contiguous sequence numbers, event shape, and UTF-8.
- `derive_messages` projects surface-aware history for the model.
- `transcript` returns the human-readable append-only view (pruning does not erase it).
- Resume/fork/compact take a POSIX `flock` write lease for the whole turn.
- v0 (headerless JSONL) and v1 (single-file header) are read-only compatible.

**Learn this** — Event sourcing gives you replay, audit, and crash repair almost for free, at
the cost of a projection layer. This module is that cost, made visible.

### `src/minidsh/generations.py`

**Role** — Immutable generation publishing and hash verification.

**Key contracts**

- Writes go to a temporary file, `fsync`, then `os.replace` the manifest.
- Content-hash backups are written before migration or repair.
- A tampered generation fails hash verification on read.

**Learn this** — Atomic file replacement and content hashes are cheap durability. Do them
before you need them.

### `src/minidsh/session_recovery.py`

**Role** — Close interrupted turns honestly.

**Key contracts**

- A tool call that never started gets `TOOL_NOT_STARTED`.
- A call that started without a durable result gets `TOOL_OUTCOME_UNKNOWN`, with an explicit
  warning not to blindly retry side effects.
- Open steps and turns are closed with reason `interrupted`; fork uses `forked`.

**Learn this** — Crash recovery must not invent success. Reporting uncertainty is a feature.

### `src/minidsh/surface.py`

**Role** — Fold append/replace events into the model-visible view.

**Key contracts**

- `surfaceOp={op: replace, startSeq, endSeq}` replaces a contiguous range; `sourceEventSeqs`
  references the shadowed nodes.
- Replacements validate existence, order, current membership, and non-duplicated sources.
- A tool result replacement may change `content` only.
- The first system prompt can only be rewritten by a single system replacement.

**Learn this** — Separating "what happened" from "what the model currently sees" unlocks
pruning, summarization, and redaction without losing the audit trail.

---

## 5. Host products

### `src/minidsh/server.py`

**Role** — Local HTTP + SSE host and the Web API.

**Key contracts**

- Binds `127.0.0.1` by default; non-loopback binds require `MINIDSH_TOKEN`.
- `POST /v1/runs/stream` starts a run; `turn/start` carries the `run_id` used for
  cancellation.
- Every SSE listener has a bounded queue (256 events); slow clients are dropped without
  stopping the agent.
- The broker keeps the last 2048 events in-process for `Last-Event-ID` replay.
- One write turn per session; concurrent runs are rejected.
- Pending questions, jobs, terminals, schedule, MCP, skills, presets, and goals each expose
  small JSON endpoints.

**Learn this** — An HTTP host is mostly lifecycle and backpressure. `run_id` correlation and
bounded queues are what keep concurrent runs from bleeding into each other.

### `src/minidsh/cli.py`

**Role** — The terminal product and operator toolkit.

**Key contracts**

- Subcommands: `config`, `plugins`, `tools`, `models`, `files`, `prompt`, `sessions`,
  `skills`, `skill`, `goal`, `presets`, `preset`, `run`, `resume`, `session`, `transcript`,
  `todos`, `fork`, `compact`, `repair`, `migrate`, `surface`, `stream`, `prune`, `serve`,
  `desktop`.
- `--trace` prints turn, tool, and event summaries.
- Interactive approval and question answerers; non-interactive runs return `unavailable`.
- CLI is a short-lived process: background jobs and PTYs are reclaimed on exit.

**Learn this** — Operators need introspection commands (`plugins`, `tools`, `prompt`,
`session`) as much as they need a `run` command. Observability is a UX feature.

### `src/minidsh/desktop.py`

**Role** — Optional native window around the same HTTP host.

**Key contracts**

- Imports `pywebview` lazily.
- Core imports, plugin listing, and headless/Web usage never require GUI libraries.

**Learn this** — Optional heavy dependencies belong at the product edge, not in the kernel.

### `src/minidsh/web/index.html`

**Role** — The local workbench UI served by `minidsh serve`.

**Key contracts**

- Vanilla HTML/CSS/JS; no build step.
- Uses `EventSource` for live events and plain `fetch` for the API.
- Supports run/resume, fork, compact, prune, cancel, session list, image paths, and pending
  question forms.
- Has an English/Chinese language switch; choice is persisted in `localStorage`.

**Learn this** — A thin client proves the API is complete. If the UI needs a private shortcut,
the API is missing something.

---

## 6. Collaboration and long-running work

### `src/minidsh/subagents.py`

**Role** — In-process agent tree.

**Key contracts**

- `subagent` spawns a child with no parent history; `subagent_fork` copies history up to the
  last complete `turn/end`.
- Child names are `agent-N`, unique per parent; root is `lead`.
- Completion arrives as a `subagent-settled` user message consumed by a later parent step.
- Control tools: `send_message`, `interrupt_agent`, `list_agents`, `wait_agent`.
- `MINIDSH_SUBAGENT_MAX_DEPTH` defaults to 1; 0 disables delegation.
- Child `cwd` is validated and recorded, but file tools still use the root workspace.

**Learn this** — Subagents are context isolation first, concurrency second. The parent should
receive a summary, not the child's transcript.

### `src/minidsh/workflow.py` and `workflow_guest.js`

**Role** — Deterministic multi-agent orchestration in an isolated Node process.

**Key contracts**

- Script hooks: `agent`, `pipeline`, `parallel`, `phase`, `log`, top-level `await`.
- No `require`, `process`, `fs`, `net`, or timers in the guest.
- Host communication is JSON-RPC; each child returns only its result to the script.
- Object-root JSON Schema subset; child failure or schema mismatch becomes `null`; fatal hook
  errors abort the run.
- `MINIDSH_WORKFLOW_MAX_AGENTS` 1000, concurrency default `min(32, cpu_count)`.
- Background mode reuses the jobs pipeline; host restart marks unsettled runs
  `interrupted: host restarted`.

**Learn this** — "Workflow" is code-shaped orchestration (deterministic), distinct from
"agent" (model-shaped control flow). Use a workflow when the control graph is known.

### `src/minidsh/ptc.py` and `ptc_runner.js`

**Role** — Programmatic tool calling (`run_code`).

**Key contracts**

- `per-call` async TypeScript body; `persistent` Node REPL with per-session top-level state.
- Tools are exposed as `await tools.name(args)` through the same registry, so approval,
  cancellation, and events still apply.
- Read-only subcalls run in parallel (default 10); mutating calls are exclusive.
- Defaults: 120 s timeout, 600 s cap, 64 MiB output, 3000 ms stop grace, 128 pending,
  1024 subcalls/minute.
- Errors are classified: timeout, abort, exception, protocol, worker-exit, output-limit,
  invalid-output.

**Learn this** — Code execution collapses multi-step tool churn into one model turn, but it
also raises the stakes on quotas and isolation.

### `src/minidsh/presets.py`

**Role** — Per-session capability profiles.

**Key contracts**

- A preset selects plugins (tools) and an optional persona (prefix/suffix, `complete`,
  `includeRuntimeContext`).
- `preset/selected` is persisted; a session cannot switch after its first model turn.
- Forked sessions and subagents inherit the parent preset.
- A missing preset in history fails closed.

**Learn this** — Capability scoping is a product feature: a reviewer agent should not carry a
shell tool it never needs.

### `src/minidsh/goals.py`

**Role** — Event-sourced persistent goals with optional automatic rounds.

**Key contracts**

- One current goal per session; `goal/change` v1 stores objective, status
  (active/paused/blocked/complete), revision, round count, and blocker reason.
- Every update requires exact `goal_id` + `revision`.
- `core.goal-rounds` is a separate switch: armed goals continue the same session when idle.
- Only a `kind=goal` user message consumes the round budget; exhaustion records
  `round-limit`.
- Resume, fork, or restart always leaves the goal disarmed until explicitly resumed.
- Autonomous `blocked` requires three consecutive rounds and a concrete blocker reason.

**Learn this** — Long-horizon autonomy needs explicit state, budgets, and a human re-arm step.
"Keep going until done" without revision checks is how loops run forever.

### `src/minidsh/schedule.py` and `schedule_time.py`

**Role** — Host-level scheduled delivery.

**Key contracts**

- Tools: `schedule_create/list/view/edit`; tasks belong to the host, not the session.
- Targets: `this_session`, `new_once`, `new`.
- Timing forms: `after/at/every/daily/weekly/cron/rrule`; five-field cron; partial RRULE.
- Atomic storage in `.minidsh/schedule.json` (v2); missed occurrences deliver only the latest.
- `schedule/inbox` confirms persistence, not model completion.
- History keeps 200 deliveries / 30 days; minimum delivery interval 60 s.

**Learn this** — A scheduler is a durability problem: idempotency, missed time handling, and
the difference between "delivered" and "done".

### `src/minidsh/skills.py`

**Role** — Progressive-disclosure instruction packs.

**Key contracts**

- Skills are directories with `SKILL.md` or flat Markdown files with frontmatter.
- Roots: project `.dsh/skills`, `.agents/skills`, configured dirs, `~/.dsh/skills`,
  `~/.agents/skills`, optional bundled root.
- The catalog is a persistent user message; changes append a replacement catalog.
- The `skill` tool reloads the full body on each call and returns `<skill_content>`.
- `disable-model-invocation` hides the model entry point; `user-invocable: false` blocks the
  explicit user gesture.

**Learn this** — Progressive disclosure beats upfront context. Advertise names and one-line
descriptions; load the body only when the task needs it.

### `src/minidsh/mcp.py`

**Role** — Model Context Protocol client and server manager.

**Key contracts**

- Transports: stdio (scrubbed environment) and Streamable HTTP (JSON/SSE, `MCP-Session-Id`).
- Performs real `initialize` / `notifications/initialized` and paginated discovery.
- Public tool names are `mcp__<server>__<tool>` with stable hashes for long names.
- Tool calls support timeout, cancellation, and `notifications/cancelled`; server `isError`
  fails closed.
- Shared resource tools: `list_mcp_resources`, `list_mcp_resource_templates`,
  `read_mcp_resource`.
- `${credential:NAME}` resolves from the host environment; diagnostics redact values.
- Name conflicts reject the entire new tool generation, leaving old tools intact.

**Learn this** — The protocol boundary is where trust, naming, credentials, and cancellation
all meet. Read this module with the spec open.

**Watch out for** — The implementation follows the classic handshake; the 2026-07-28 MCP
revision makes the protocol stateless and removes `initialize`. See
[`upstream-parity.md`](upstream-parity.md) for the exact boundary.

### `src/minidsh/questions.py` and `src/minidsh/todo.py`

**Role** — Human-in-the-loop questions and session-scoped todos.

**Key contracts**

- Question ids are stable; options support single/multi select; empty answer means skip.
- Unanswered questions live in a host queue with `GET /v1/questions` and
  `POST /v1/questions/<id>/answer`.
- Timed mode waits 120 s by default; timeout returns `pending=true` and keeps the question.
- A late answer is injected as `answer_to_pending_question` with the original call id.
- Todos are whole-list replacements, unique non-empty content, one `in_progress` unless
  `MINIDSH_TODO_ALLOW_PARALLEL=1`.

**Learn this** — Human input is an asynchronous, possibly-late event. Treat it like any other
untrusted external signal, with validation and explicit lifecycle events.

### `src/minidsh/web_tools.py`

**Role** — Bounded web access with prompt-injection awareness.

**Key contracts**

- `web_fetch`: HTTP(S), 5 redirects, 30 s timeout, 1 MiB read cap, 200000-character output
  cap, private/loopback addresses refused, HTML scripts/styles/hidden nodes removed.
- `web_search`: replaceable provider via `MINIDSH_WEB_SEARCH_URL`; up to 10 queries, 10
  sources, 800-character snippets, Markdown citations.
- Both prepend a "web content is untrusted" reminder and carry the URL.

**Learn this** — Fetched content is data, never instructions. Budgets and re-validation on
redirect are baseline SSRF hygiene, not a complete defense.

---

## 7. Product distributions

### `packages/minidsh-headless`, `packages/minidsh-web`, `packages/minidsh-desktop`

**Role** — Thin entry points over the same `Application`.

| Package | Command | Extra runtime |
|---|---|---|
| `minidsh` / `minidsh-headless` | `minidsh`, `minidsh-headless` | None (stdlib only) |
| `minidsh-web` | `minidsh-web` (defaults to `serve`) | None |
| `minidsh-desktop` | `minidsh-desktop` (defaults to `desktop`) | `pywebview` |

**Learn this** — Product shells should be replaceable and thin. If the Web UI and the CLI
diverge in behaviour, the composition root is leaking.

### `examples/minidsh-clock`

**Role** — A standalone external plugin used to prove the entry-point contract.

**Key contracts**

- Declares `[project.entry-points."minidsh.plugins"]` with `example.clock`.
- Registers a real tool through the same service/feature machinery.
- Disabled until explicitly enabled, even after installation.

**Learn this** — The best plugin documentation is a package that installs, lists, enables, and
runs without touching core code.

---

## Where to start reading

1. `config.py` → `plugins.py` → `app.py` for the skeleton.
2. `agent.py` → `tools.py` → `shell.py` for the loop and its riskiest capabilities.
3. `session.py` → `surface.py` → `compaction.py` for memory.
4. `server.py` → `cli.py` → `web/index.html` for products.
5. `subagents.py` → `workflow.py` → `ptc.py` for multi-agent and code execution.
6. `mcp.py` → `skills.py` → `schedule.py` → `goals.py` for ecosystem and long-horizon work.

The [exercises](exercises.md) map each of these paths to a runnable lab and to current
industry practice.
