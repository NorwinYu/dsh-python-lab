# Exercises

Eighteen labs, ordered like the [learning path](../README.md#the-27-layer-learning-path). Each lab
has a goal, the exact commands, what you should observe, and the industry practice it connects to.

Every lab runs offline with the deterministic mock model. Nothing here needs an API key.

**Conventions**

- `minidsh` means the installed CLI; from a source checkout you can also use
  `PYTHONPATH=src python3 -m minidsh`.
- `<id>` is a session id printed by the previous command or by `minidsh sessions`.
- Labs create files only under a scratch directory. Never point `danger-full-access` at a real
  project.

```bash
mkdir -p /tmp/minidsh-lab && cd /tmp/minidsh-lab
minidsh --workspace /tmp/minidsh-lab plugins | head -3
```

---

## Lab 1 — Prove that disabling a feature removes its instructions

**Goal.** See the dependency graph, not just a checkbox.

```bash
minidsh prompt
minidsh --disable tools.read prompt
minidsh --disable tools.edit prompt
minidsh --disable core.jobs plugins
```

**Observe.** The `read` tool section and its schema disappear together. Removing `edit` changes the
`write` guidance (the "prefer edit for targeted changes" clause vanishes). With `core.jobs`
disabled, every plugin that requires `jobs` reports `missing=jobs`.

**Connects to.** Capability-gated prompting. A model told to use a tool it cannot call is a harness
bug; the fix is to derive the prompt from the capability set, which is what
[2026 harness engineering](https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md)
calls "capability declaration, not instruction text".

---

## Lab 2 — Trace one turn end to end

**Goal.** Learn the event vocabulary before reading any other module.

```bash
minidsh run "read README.md" --trace
minidsh sessions
minidsh session <id>
```

**Observe.** The order: `session/start` → `turn/start` → `assistant/message` → `tools/pre-execute`
→ `tools/post-execute` → `assistant/message` → `turn/end`. Compare `session/start` (raw log) with
`transcript` (human projection).

**Connects to.** Agent observability. The event names here are deliberately span-shaped, matching
[OpenTelemetry GenAI conventions](https://opentelemetry.io/docs/specs/otel/logs/semantic_conventions/gen-ai/)
for session/run/turn/step correlation.

---

## Lab 3 — Watch approval fail closed

**Goal.** Understand that "no answerer" is not "yes".

```bash
minidsh run "write denied.txt: hello"
minidsh --sandbox-mode workspace-write --approval-policy never run "write denied.txt: hello"
minidsh --sandbox-mode workspace-write run "write denied.txt: hello"      # non-interactive: unavailable
minidsh --sandbox-mode workspace-write --yes run "write ok.txt: hello"
ls /tmp/minidsh-lab
```

**Observe.** Four distinct outcomes: policy refusal, `rejected`, `unavailable`, and `allowed-once`.
`minidsh session <id>` shows paired `approval/asked` / `approval/decided` events with request id,
tool name, and call id.

**Connects to.** Human-in-the-loop safety. Approval is a *human control*, not a security boundary —
which is exactly why the next lab exists.

---

## Lab 4 — Read-before-write as a correctness rule

**Goal.** See why file observations are persisted.

```bash
minidsh --sandbox-mode workspace-write --yes run "write note.txt: alpha"
minidsh --sandbox-mode workspace-write --yes run "write note.txt: beta"    # no read: refused
minidsh --sandbox-mode workspace-write --yes run "edit note.txt: replace alpha with beta"
minidsh --sandbox-mode workspace-write --yes resume <id> "edit note.txt: replace beta with gamma"
```

**Observe.** The second write is refused because that *session* never observed the file. The edit
prefix performs `read` then `edit` in one turn. On resume, the observation is restored from the
event log — the third command succeeds because of persisted state, not in-memory caching.

**Connects to.** Optimistic concurrency control. This is the same idea as an editor buffer version
or an ETag: the write is valid only against the version you read.

---

## Lab 5 — Kernel sandbox versus prompt rules

**Goal.** Feel the difference between asking and enforcing.

```bash
minidsh --enable core.kernel-sandbox run "run printf denied > blocked.txt"
minidsh --enable core.kernel-sandbox --sandbox-mode workspace-write --yes \
  run "run printf allowed > inside.txt"
ls /tmp/minidsh-lab
```

**Observe.** The first fails with `Operation not permitted` from `/bin/bash`, not from the harness.
The second succeeds inside the workspace.

**Connects to.** Defense in depth. Compare with the current sandboxing discussion around Seatbelt,
[Landlock and bubblewrap](https://github.com/zeroclaw-labs/zeroclaw/blob/a56c345d51dd8ab562e9351e0d4ab83f6a741db9/docs/book/src/security/sandboxing.md).

---

## Lab 6 — Jobs: timeout moves, it does not kill

**Goal.** Understand process lifecycle and output cursors.

```bash
minidsh --sandbox-mode workspace-write --yes run "run in background sh -c 'for i in 1 2 3; do echo tick \$i; sleep 1; done'"
minidsh --sandbox-mode workspace-write --yes run "jobs"
minidsh --sandbox-mode workspace-write --yes run "job output bash-1"
```

**Observe.** The background call returns a job id immediately. `job_output` consumes *new* output
since the last read; run it twice and compare. A foreground command that exceeds its wait window is
moved to the background rather than killed.

**Connects to.** Durable execution. Cursor-based output reading is how you avoid re-sending 200 KB
of logs to the model on every poll.

---

## Lab 7 — Resume, fork, and repair

**Goal.** Separate history from projection from branches.

```bash
minidsh --sandbox-mode workspace-write --yes run "write history.txt: v1"
minidsh resume <id> "edit history.txt: replace v1 with v2"
minidsh fork <id> --at-seq 12
minidsh transcript <id>
minidsh repair <id>
```

**Observe.** Resume appends to the same session and regenerates the runtime context. Fork creates a
new session id with copied history and never touches the parent. `transcript` shows the append-only
view; `repair` writes a backup before closing an interrupted turn.

**Connects to.** Event sourcing and crash recovery. Turning a crash into `TOOL_OUTCOME_UNKNOWN`
instead of a fabricated success is the difference between a recoverable system and a corrupt one.

---

## Lab 8 — Compact the view, keep the facts

**Goal.** See memory pressure handled honestly.

```bash
minidsh --context-window 60 run "read README.md"
minidsh session <id> | grep -c compaction
minidsh transcript <id> | head -20
minidsh compact <id>
```

**Observe.** `compaction/start` and `compaction/end` appear, with a `<compacted-summary>` block. The
original user and assistant events are still in the log and still in `transcript`.

**Connects to.** Context engineering. "Compaction is a projection, not a deletion" is the single
most important property to get right before adding summarization to a product.

---

## Lab 9 — Surface replacement and pruning

**Goal.** Reshape the model view without rewriting history.

```bash
minidsh run "read README.md"
minidsh surface <id>
minidsh --enable core.tool-result-pruner prune <id>
minidsh surface <id>
minidsh transcript <id> | head -30
```

**Observe.** After pruning there is a `compaction/prune` event plus a `tool/result` replacement with
`surfaceOp={op:"replace",...}` and `sourceEventSeqs`. `surface` shows the smaller view; `transcript`
still shows the original tool output.

**Connects to.** Observation masking. Production harnesses prune tool output far more often than
they summarize conversation; the mechanism matters more than the policy.

---

## Lab 10 — Deterministic trajectories with the mock grammar

**Goal.** Turn behaviour into something you can assert on.

```bash
minidsh --sandbox-mode workspace-write --yes run "write demo.txt: hello"
minidsh --sandbox-mode workspace-write --yes run "写入 demo2.txt：你好"
minidsh run "read README.md"
minidsh run "whatever the model wants"
```

**Observe.** Both languages produce the same intent. The last command calls no tool at all — the
mock returns the help text instead of guessing.

**Connects to.** Agent evaluation. Deterministic trajectories are the cheapest regression test an
agent repo can have; compare with benchmark-style evaluation such as
[Terminal-Bench](https://benchlm.ai/benchmarks/terminal-bench-2).

---

## Lab 11 — Ask a question, answer it late

**Goal.** Treat human input as an asynchronous event.

```bash
minidsh --enable tools.ask-user run "ask Should I continue?"
minidsh --enable tools.ask-user --enable tools.ask-user-timed run "ask Continue?"
```

**Observe.** The question id is stable, and an empty answer means *skipped*, not *approved*. In
timed mode, a timeout returns `pending=true` and leaves the question in the host queue
(`GET /v1/questions`).

**Connects to.** Interruptible autonomy. Late-answer handling is what separates a demo chatbot from
a system a human can supervise.

---

## Lab 12 — Subagents as context isolation

**Goal.** See what the parent does and does not receive.

```bash
minidsh --enable core.subagents --enable tools.subagent --enable tools.subagent-control \
  run "delegate audit | read README.md"
minidsh --enable core.subagents --enable tools.subagent-control run "agents"
```

**Observe.** The parent gets `agent-1` immediately and later a `subagent-settled` message — a
summary, not the child's transcript. The child has its own session id and its own events.

**Connects to.** Context isolation. Delegation is valuable because the parent's context does *not*
grow with the child's work; treating it as free parallelism misses the point.

---

## Lab 13 — Workflow: orchestration as code

**Goal.** Compare deterministic orchestration with agentic control flow.

```bash
minidsh --enable core.subagents --enable core.workflow --enable tools.workflow \
  run "workflow alpha|beta|gamma"
minidsh --enable core.subagents --enable core.workflow --enable tools.workflow \
  run "workflow background alpha|beta"
```

**Observe.** `workflow/phase`, `workflow/agent-start`, `workflow/agent-end`, `workflow/end`. The
background form returns a job id usable with `job_output`. The guest script cannot touch the
filesystem or network — it can only call `agent()`.

**Connects to.** Orchestration patterns. Use a script when the control graph is known in advance;
use an agent when it is not. Mixing the two is where most multi-agent systems lose their budget.

---

## Lab 14 — MCP round trip with a local server

**Goal.** Implement the smallest possible MCP server and see it appear as tools.

```bash
cat > /tmp/minidsh-lab/server.py <<'PY'
import json, sys

def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n"); sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lab", "version": "0.1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{
            "name": "echo", "description": "Echo a string",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                            "required": ["text"]}}]}})
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": msg["params"]["arguments"]["text"]}]}})
PY

cat > /tmp/minidsh-lab/mcp.json <<'JSON'
{
  "features": { "core.mcp": true, "tools.mcp-resources": true },
  "mcp_servers": {
    "lab": { "transport": "stdio", "command": "python3", "args": ["-u", "/tmp/minidsh-lab/server.py"] }
  }
}
JSON

minidsh --config /tmp/minidsh-lab/mcp.json tools
```

**Observe.** A tool named `mcp__lab__echo`. With `core.mcp` enabled but no server configured, no MCP
tool appears at all — the namespace is derived from real discovery, not static registration.

**Connects to.** The [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28/changelog).
This lab speaks the handshake protocol; the 2026-07-28 revision removes the handshake entirely and
replaces it with `server/discover` plus `_meta` version fields — read the changelog and note exactly
which steps this implementation would have to drop.

---

## Lab 15 — Skills and progressive disclosure

**Goal.** Load instructions only when they are needed.

```bash
mkdir -p /tmp/minidsh-lab/skills/review
cat > /tmp/minidsh-lab/skills/review/SKILL.md <<'MD'
---
name: review
description: Review a diff for correctness, not style
---
Read the changed files first. Report only defects with file and line references.
MD

minidsh --enable core.skills --enable tools.skill skills
minidsh --enable core.skills --enable tools.skill run "skill review"
```

**Observe.** The catalog is a persistent user message with name and description only. The `skill`
tool returns the full body wrapped in `<skill_content>` on each call.

**Connects to.** The [Agent Skills specification](https://agentskills.io/specification). Compare the
cost: ten skills advertised at ~20 tokens each versus ten full instructions always in context.

---

## Lab 16 — Presets scope capability

**Goal.** Give one session a shell and another none.

```bash
cat > /tmp/minidsh-lab/presets.json <<'JSON'
{
  "features": { "core.agent-presets": true },
  "default_preset": "standard",
  "presets": {
    "standard": { "plugins": ["tools.read", "tools.glob", "tools.bash"] },
    "reviewer": { "plugins": ["tools.read", "tools.glob"] }
  }
}
JSON

minidsh --config /tmp/minidsh-lab/presets.json --enable core.agent-presets presets
minidsh --config /tmp/minidsh-lab/presets.json --enable core.agent-presets \
  preset <id> reviewer
minidsh --config /tmp/minidsh-lab/presets.json --enable core.agent-presets tools
```

**Observe.** After the preset is applied, `bash` is absent from that session's tool schema. Trying
to switch again after the first model turn is refused.

**Connects to.** Least privilege for agents. A reviewer agent with a shell is not a reviewer; it is
an operator with a review prompt.

---

## Lab 17 — Goals and the round budget

**Goal.** See why "keep going until done" needs a budget and a re-arm step.

```bash
minidsh --enable core.goals --enable tools.goal run "goal create finish the migration|2"
minidsh --enable core.goals goal <id>
minidsh --enable core.goals --enable core.goal-rounds goal <id> resume
minidsh --enable core.goals goal <id>
```

**Observe.** The goal stores objective, status, revision, and round count. `goal/change` requires an
exact id + revision. After resume the goal is armed; after a restore or fork it is disarmed and must
be resumed by a human.

**Connects to.** Long-horizon autonomy. Optimistic concurrency plus a round budget is the minimum
viable guardrail; without them, an autonomous loop has no exit condition except money.

---

## Lab 18 — Programmatic tool calling

**Goal.** Collapse several tool turns into one code turn.

```bash
minidsh --enable core.ptc --enable tools.run-code \
  run 'run code {"code":"const a = await tools.read({file_path:\"README.md\"}); const b = await tools.glob({pattern:\"docs/*.md\"}); return [a.length, b.length]","description":"Measure files"}'
```

**Observe.** Only `run_code` is visible in the schema; other tools are reachable as
`await tools.name(args)`. Approval, cancellation, and events still apply because calls go through
the same registry. Read-only subcalls run in parallel; mutating calls are exclusive.

**Connects to.** The "code as the action space" trend. Code execution is a large latency win and a
large blast-radius increase — which is why quotas, isolation, and error classification are part of
the same module.

---

## Capstone — Build a regression harness

**Goal.** Turn the whole lab into something a team could run in CI.

1. Write an offline scenario for each of: read, write+approve, rejected approval, background job,
   resume, compact, prune, subagent settle.
2. For each, run `minidsh ...` into a scratch workspace and assert on `minidsh session <id>` event
   types and order (not on prose).
3. Record the expected event sequences as fixtures.
4. Fail CI when a sequence changes without a corresponding fixture update.

**Why this is the capstone.** Everything else is a feature; this is the property that lets you
change a harness without silently changing its behaviour. Deterministic trajectories plus
append-only logs make it possible without a real model.

Compare the shape with public agent benchmarks — the difference is that your fixtures test *the
harness*, while benchmarks test *the model plus harness*. Both are needed; only one is cheap enough
to run on every commit.

---

## Further reading

- [Module atlas](modules.md) — per-file roles, contracts, and traps.
- [Mock grammar](mock-model.md) — every trigger, in both languages.
- [Upstream parity](upstream-parity.md) — what matches DeepSeek Harness and what does not.
- [README](../README.md) — the full guide.
