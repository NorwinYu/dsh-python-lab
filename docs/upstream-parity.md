[简体中文](upstream-parity.zh-CN.md)
# Upstream Parity Baseline

## Pinned Version

- Upstream: `deepseek-ai/deepseek-harness`
- Commit: `fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`
- Corresponding package version: `0.2.2-rc.1`
- Model protocol: `packages/llm/llm-deepseek`
- Prompts: `packages/core/system-prompt` and the individual tool plugins
- Approval and file policy: `packages/interaction/user-approval`, `packages/sandbox/sandbox-policy`
- File tools: `packages/fs/tool-fs`, `packages/fs/tool-fs-search`
- Shell and jobs: `packages/shell/tool-bash`, `packages/jobs/{jobs-local,tool-jobs}`
- Sessions and compaction: `packages/core/session`, `packages/session/session-persistence-jsonl`, `packages/compaction/compaction-basic`
- Product assembly: `apps/cli`, `apps/web`, `apps/desktop`; only their layering boundaries are borrowed, and no protocol or package-format compatibility is claimed

The parity claims cover only the slices listed below; they do not mean that the complete DSH has been ported.

## Currently Aligned

### System Prompt and Messages

- Fixed identity paragraph: `You are an AI agent powered by DeepSeek Harness.`
- `bash/read/write/edit/glob/jobs` are ordered at 1000/1100/1200/1300/1400/1600 respectively, using the upstream English text verbatim.
- Whether `write` includes `and prefer edit for targeted changes` varies with the `edit` toggle.
- Disabling a tool removes both its schema and its prompt section; when `jobs` is missing, the Shell/jobs plugin stays pending.
- The runtime context contains the working directory, file policy, and approval policy in that order, and is placed after the user instruction as a separate user snapshot.
- Assistant messages contain ordered `text / reasoning / tool-call` content blocks; tool results are backfilled via `toolCallId`.

```bash
PYTHONPATH=src python3 -m minidsh prompt
PYTHONPATH=src python3 -m minidsh --disable tools.edit prompt
PYTHONPATH=src python3 -m minidsh --disable core.jobs plugins
```

### DeepSeek Messages Protocol

- Default base URL: `https://api.deepseek.com/anthropic`
- Request URL: append `/messages` when the base URL ends with `/v1`, otherwise append `/v1/messages`
- Authentication: `x-api-key`; protocol header: `anthropic-version: 2023-06-01`
- Default model: `deepseek-flash`; default thinking: `high`
- Default `max_tokens`: 256000; default stream idle timeout: 300000ms
- Supports text, reasoning, tool_use, tool_result, and tool JSON Schema
- SSE supports text, thinking, signature, input_json_delta, usage, and stop reason
- Preserves the `deepseek-messages` replay envelope and reasoning signature; a cross-model switch discards the old signature
- HTTP redirects are not followed

### File Tools and Approval

- Implements the official `read`, `glob`, `write`, and `edit` names, schemas, prompts, and primary model output.
- `read` supports a 2000-line limit, a 2000-character per-line limit, a 50KiB byte limit, line numbers, and the output envelope.
- `glob` supports a 100-entry limit, hidden files, VCS exclusion, and modification-time ordering.
- `write` returns the `Created file / Updated file` envelope; `edit` supports unique matching and `replace_all`.
- Overwriting or editing requires a valid file-version observation in the same session; external modification invalidates the observation.
- `workspace-write` restricts absolute paths and symlinks; `danger-full-access` imposes no workspace path restrictions.
- `read-only` by default; `ask` with no responder returns `unavailable`, and `never` returns `rejected`.
- `approval/asked` and `approval/decided` are recorded in pairs with request id, toolName, callId, and outcome.
- `--yes` is the teaching edition's process-level explicit authorization: it permits all write/Shell operations in this process and should be used with caution.

### Shell and Background Jobs

- `bash` uses a fresh `bash -c` process and preserves no cwd, variables, or functions; the directory is specified via `workdir`.
- Returns stdout, labeled stderr, and `[exit code: N]`; a non-zero exit is a command result, not an infrastructure error.
- Every Bash call is registered as a job; `run_in_background` immediately returns `bash-<id>`.
- When the foreground wait times out, the same job is retained and `[still running after ...; moved to background job ...]` is returned.
- `job_output` consumes new output with independent stdout/stderr cursors and supports bounded waits and status lines.
- `job_list` lists the current session's jobs; `job_kill` terminates the process group and records a reason.
- Each session has at most 10 running/stopping jobs; other sessions cannot read or terminate a job.
- The in-memory output ring is 256KiB and shrinks to 16KiB after the first terminal-state read; full stdout/stderr are spooled to disk separately and their paths are reported.
- Surviving jobs are terminated and reaped when the application shuts down.
- The session records `job/registered` and `job/settled`.
- The Shell environment filters out `KEY/TOKEN/SECRET/PASSWORD`-shaped variables and pre-existing `DSH_*` variables, then injects the managed `DSH_HOME` and `DSH_SESSION_ID`.

### Sessions and Compaction

- `resume` rebuilds user, assistant, and tool messages from JSONL, increments the turn, writes `session/resume`, and refreshes the current prompt and runtime context.
- `fs/observed` is persisted with the session, and file-version observations are restored on resume.
- `fork` can copy the full session or the prefix up to a given event sequence number, producing an independent session id.
- Supports session listing and a readable transcript projection.
- The automatic pressure threshold is 80% of the context capacity, retaining the last 16% of messages by default.
- Uses the upstream compaction-engine prompt and the `<compacted-summary>` marker, and writes `compaction/start/end`.
- Compaction affects only the projection sent to the model and does not delete original events; a compacted session can still be resumed.
- The token meter is currently the teaching edition's UTF-8/JSON heuristic estimate, not the official exact tokenizer.

### Session Versions, Migration, and Crash Recovery (teaching-edition v0/v1/v2)

- New sessions contain a `minidsh-session-header` v2 and a complete event snapshot; existing headerless files are read-only compatible as v0, and single-file headers as v1.
- Each generation lives at `<session-id>/generations/NNNNNNNN.jsonl` and must never be overwritten; the `index.json` manifest records the current generation, path, and SHA-256.
- Appends, migrations, repairs, and forks all publish a new generation while old generations are kept read-only; the manifest is switched atomically via a temporary file, fsync, and os.replace.
- Normal reads do not migrate; the current format is written only on the first append, an explicit migrate, or a repair on resume, and future versions are rejected outright.
- Every event validates session identity, contiguous seq, type, payload, and created_at; invalid JSON/UTF-8 fails closed.
- A normal load rejects an incomplete tail; repair/resume keeps the complete prefix before the last newline, and rejects when no record can be recovered.
- Before migration or repair, the original bytes are saved as a content-hash-named backup; generation hashes are verified when reading the manifest, and external tampering fails closed.
- Tool calls that never started use the upstream TOOL_NOT_STARTED wording; started calls without a result use TOOL_OUTCOME_UNKNOWN plus a conservative retry warning.
- Repair first appends an error tool result, then closes open steps/turns with reason interrupted; fork uses the forked wording and does not modify the parent session.
- Audit events are `session/migrated` and `session/repaired`; the CLI supports repair and single/all migrate, and HTTP supports per-session repair/migrate.
- resume/compact use a POSIX flock covering the entire write turn; the lock file keeps a stable inode and is released by the kernel on process death; reads take no write lock.
- HTTP rejects concurrent turns on the same session; a watcher may append job audit records within a turn of the same Application through the storage serialization lock, with the condition wakeup in finally.
- This format is not the official multi-generation or Zstandard file format; the surface is only the teaching-edition slice described below, and the Windows write lock is not implemented.

### Surface Replacement and Tool-Result Pruning

- Legacy message events without a surfaceOp are treated as append for compatibility; current message events support append and `{op: replace, startSeq, endSeq}`.
- A replacement's start and end must be earlier and still on the current surface, with a valid interval order; sourceEventSeqs must be duplicate-free and cover every shadowed node.
- The replacement node occupies the original interval's position rather than being appended to the end of the model view; the original JSONL events are not rewritten.
- A tool/result replacement can shadow only one current tool result and modify only content; the first system prompt can be rewritten only by a single system replacement.
- `derive_messages`, resume, fork, and compact use the current surface; transcript excludes replacement copies and keeps the original appended content.
- The pruner is off by default and runs manually; thresholds are 8192, head 4096, and tail 1024, using the upstream fixed PRUNE_MARKER.
- Text is pruned by Unicode code point and non-text block order is preserved; the result must be smaller and must not exceed the threshold.
- Each prune first appends a `compaction/prune` metering event, then a tool-result replacement with source references; repeated execution does not prune again.
- Provides CLI surface/prune, HTTP surface/prune, and a Web prune button; configuration uses the MINIDSH_PRUNE_* environment variables.
- Not yet implemented: the official arbitrary-plugin message projection, the complete event type system, and an exact token meter.

### HTTP, SSE, and Concurrency

- Binds only `127.0.0.1` by default; binding a non-loopback address requires `MINIDSH_TOKEN`, otherwise startup is refused.
- Provides health, plugins, tools, prompt, session list/events/transcript, run/resume/fork/compact, and jobs/output/kill APIs.
- SSE carries turn, assistant, `llm/retry`, tool execution, run result/error/cancelled, and `job/settled`; an internal run/done closes the current stream.
- Every run has a unique `run_id`; captured session ids, awaited results, and stream events are all matched by `run_id`, so another run finishing does not close the current SSE early.
- Concurrent runs with the same prompt still get independent session ids; JSONL appends and reads use locks to avoid sequence collisions and half-line reads.
- Provides a Web workbench using EventSource that reuses the same Application, supports stopping a run, and does not duplicate Agent logic.

### Model Listing, Retry, and Cancellation

- `GET /models` uses the origin of the Messages base URL without inheriting its path; the authenticated request does not follow redirects and has a default 5000ms timeout.
- A successful list is cached for 600000ms by default and refreshed every time when `DEEPSEEK_MODELS_MAX_AGE_MS=0`; a deep copy is returned so callers cannot modify the cache.
- The list preserves the order of valid ids, skips empty and duplicate ids, and recognizes text/image, context capacity, and Messages capability; the official badges and client-version requirements are not yet implemented.
- By default, Messages in normal mode retries at most 5 times, covering `EMPTY_RESPONSE/RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT`; initial backoff 500ms, maximum 10000ms, jitter 0.1.
- Supports a bounded `Retry-After` in seconds or as an HTTP date, classification of auth/quota/request/rate-limit/server errors, the `llm/retry` event, and an explicit always mode.
- `llm.retry` and `llm.model-listing` can be disabled independently; listing requests themselves are not retried automatically.
- `core.cancellation` controls HTTP/Web cancellation; the token propagates to model streams, backoff waits, compaction, tools, and foreground Shell, and cancellation produces ABORTED and `run/cancelled`.
- An established response stream is cancelled by closing the response; foreground Bash terminates its process group, while cancelling a `job_output` wait does not terminate the background job.
- Before a network connection is established, cancellation can only be checked at boundaries; this is not yet an implementation compatible with the official AbortSignal full lifecycle.

### Image Attachments and the Files API

- Supports PNG/JPEG/GIF/WebP header detection, 32MiB per image, 600 images per request, and 128MiB raw cumulative; the inline base64 cumulative cap is 20MiB.
- Internal attachments record path, media type, size, and SHA-256, and base64 is never written to JSONL; DeepSeek preparation re-reads them and rejects changes.
- Images are allowed only in user/tool content; the normal file policy restricts them to the workspace, while `danger-full-access` can read external paths.
- The Messages inline format is `source.type=base64`, and file references use `source.type=file`; the latter carries `files-api-2025-04-14`.
- The Files base URL reuses the Messages `/v1` rule; multipart contains `expires_after[anchor]=created_at`, the seconds, and the file, with a default lifetime of 604800 seconds.
- The default Files operation timeout is 60000ms; upload, list, read, and delete validate objects, reject redirects, and preserve error classification.
- auto prefers upload by default and downgrades to inline when Files fails or Messages returns INVALID_REQUEST; required fails closed, and inline never uploads.
- The file cache is isolated by route, credential hash, and image SHA-256, and is treated as needing refresh one hour early; concurrent uploads are locked, and Messages retries reuse the file id.
- A rejected file reference is invalidated and re-uploaded next time; provides the `llm/image-upload`, `llm/image-fallback`, and `llm.files` toggles.
- Not yet implemented: the official image transformation, token pricing, persistent upload index, independent shared waiters, quota cleanup, and attachment storage protocol.

### Persistent Bash and PTY (teaching-edition vertical slice)

- `tools.bash-persistent` is off by default; when enabled it automatically disables one-shot `tools.bash`, and the model-facing tool name is still `bash`.
- Uses the upstream persistent-tool description and a schema containing only `command`; it keeps neither the one-shot Bash prompt section nor the background parameters.
- One interactive `/bin/bash` PTY per session, executed serially per owner; cwd, exports, functions, and Shell state persist within the process.
- Random start/end markers and an eval wrapper capture the exit code, and the result ends with `[Command finished with exit code N]`.
- Default command timeout is 300000ms, the model output tail is at most 16000 characters, and the internal scrollback is 1MiB.
- Initialization disables input echo and history expansion; the environment keeps the credential scrubbing and injects the managed DSH/PTY variables.
- Timeouts, cancellation, Shell exit, HTTP kill, and application shutdown all terminate and reset the terminal, and the next call starts from a fresh workspace environment.
- Provides `pty/created`, `pty/reset`, and terminal list/kill APIs; after a short-lived CLI process exits, in-memory PTY state cannot be recovered.
- Unix PTY only; the official terminal line buffering, OSC readiness detection, ConPTY, and cross-process recovery are not yet implemented; the sandbox mode fence while a persistent terminal is open is supported.

### Kernel Sandbox and Policy Escalation

- `core.kernel-sandbox` is off by default; when enabled, macOS Seatbelt takes over one-shot Bash, background jobs, and the persistent PTY, with default behavior unchanged.
- read-only allows execution/reads but forbids writes; workspace-write allows only the workspace, managed jobs, and platform temporary directories; danger-full-access applies no isolation.
- Operation not permitted is verified with a real subprocess, not a simulated ToolRegistry error; the `/tmp` mapping and `subpath` rules are verified on this machine.
- `sandbox/mode` persists the session override, and tools, runtime context, and process backends read the same policy.
- `approval/policy` switches ask/never and updates the runtime context in sync; a cancelled approval records cancelled and discards late answers.
- Mode switching is forbidden while a persistent terminal is open; after it closes, a new terminal is created under the new mode.
- Provides GET sandbox, POST sandbox, and POST approval-policy APIs; there is no Landlock/bwrap or Windows equivalent yet.

### Incremental Model Streaming and SSE Backpressure

- `core.streaming` is enabled by default; DeepSeek emits text/reasoning/tool-call deltas, tool block-starts, and usage synchronously from the original parser.
- The Agent adds session/run/turn/step; each request carries an attempt, so consumers can distinguish a failed draft from a new stream after a retry.
- The final ModelResponse, assistant/message, and reasoning replay come from the same parser as the deltas, without changing the existing persisted message structure.
- For models without delta support, the Agent re-emits the final text once marked fallback=true; with core.streaming disabled, no deltas are sent at all.
- max-token responses discard tool calls and their replay blocks to avoid executing incomplete tool operations.
- The Web UI concatenates only text-deltas, llm/retry clears the failed attempt's draft, and the raw-event area keeps the complete events.
- Each SSE listener queue is capped at 256; on put_nowait overflow the slow stream is closed and removed, while the Agent keeps running and persists the final result.
- Teaching-edition compressed persistence of raw chunks and same-process resumption are covered in the next section; this does not include the official full persistence protocol, a standalone assembler API, or SSE recovery across restarts.

### Raw Stream Persistence and SSE Resumption

- `core.stream-recording` is enabled by default; the assistant/message payload may carry the compressed stream and finish/replayState.
- Adjacent text/reasoning chunks are packed by type/index into a text array with time0 and dt; tool arguments are packed by index/id/name into args, preserving the original fragment boundaries.
- usage, block-start, and finish keep their raw chunks; Mock fallback uses index=0, and the final message and raw stream come from the same response.
- `stream_codec.expand_stream` validates the record shape, member arrays, dt length, and timing; session reads expand and validate the stream.
- CLI `stream` and HTTP GET `/stream` expand the fragments and attach seq/turn/step; fork preserves the recording, and no stream is written once the toggle is off.
- The broker assigns in-process incrementing event ids and retains the most recent 2048; Last-Event-ID replays only that session's newer events and then joins the live queue.
- Replay stitches history and subscription under the same lock, run/done is replayable, and no duplicate Agent is started; too-old, future, or unknown resume points fail explicitly.
- `core.sse-replay` can be disabled; the history ring does not survive process restarts, and persisted chunks have no global SSE sequence number.

### Questions, Todos, and Web Tools (teaching-edition vertical slice)

- `tools.ask-user` registers the official name ask_user_question and the questions/id/question/options/multi_select schema; the prompt text includes the upstream skip semantics.
- Supports CLI per-question answering, HTTP pending-list/answer, custom text, option validation, cancellation, and service shutdown; question/asked/answered are persisted.
- `tools.todo` registers todo_write with whole-table replacement, validating unique non-empty content, pending/in_progress/completed, and active counts.
- Todo events are todo/write; the latest write wins, turn/start clears, turn/end retains, and MINIDSH_TODO_ALLOW_PARALLEL controls parallel active tasks.
- `tools.web-fetch`/`tools.web-search` register the official names and use the upstream external-untrusted prompt section; off by default.
- Web fetch supports bounded reads, a 30-second default timeout, 200000-character output, re-checking redirects, private-network rejection, safe HTML downgrade, and cancellation.
- Web search uses the teaching-edition provider at MINIDSH_WEB_SEARCH_URL; it supports query deduplication, 10 queries/10 sources, 800-character summaries, and Markdown citations.
- `tools.ask-user-timed` supports the official timeout parameter, pending/callId/message, a default 120-second wait, and late answers; a timed-out question stays in the pending queue.
- A late answer is projected as a user/message with source.kind=answer_to_pending_question, carrying the callId and original question; the Web graphical form supports options, multi-select, custom text, and skip.
- Currently absent: the full official Client card system, Turndown/GFM, the DeepSeek search route, and DNS connection pinning.

### Local Subagents and Collaboration Control (teaching-edition vertical slice)

- `core.subagents` is off by default; when enabled it provides spawn/fork local backends, persistent child Sessions, a parent-child tree, names, and a depth limit.
- `subagent` starts a fresh child with no parent history; `subagent_fork` corresponds to the upstream configurable tool name and copies history up to the last complete turn/end, excluding the currently open turn.
- The tool immediately returns agent-N or the given name; child completion reaches later parent steps via a subagent-settled user message without leaking the child's intermediate context.
- `send_message` can queue input to a running child or wake an idle child; `interrupt_agent` validates the ancestor-descendant relationship and propagates cancellation; `list_agents` lists the root and descendants; `wait_agent` supports the official 10000–3600000ms bounds.
- Records the parent subagent/catalog and in-process subagent/start/end, and provides session subagents queries plus the teaching APIs subagent-message and subagent-interrupt.
- MINIDSH_SUBAGENT_MAX_DEPTH defaults to 1, and 0 forbids delegation; Application shutdown reaps children by depth.
- Currently absent: external ACP/Codex/Claude Code/DSH SDK backends, model/preset selection, structured output, and online catalog recovery across restarts; a child cwd is validated and persisted, but file tools still use the root workspace. Parent completion notifications are consumed in a later step of the teaching-edition Agent Loop, not the official full inbox/independent wakeup scheduler.

### Workflow Script Orchestration, Background Jobs, and Recovery (teaching-edition vertical slice)

- `core.workflow` and `tools.workflow` are off by default; they reuse the local subagent tree, require a Node.js executable, and add no Python runtime dependency.
- The Node child runs an ordinary JavaScript async body in a vm; scripts have no require/process/fs/net/timer, and meta/args are injected as JSON data without evaluating the metadata.
- Supports agent, pipeline, parallel, phase, log, and top-level await; each agent uses delivery=caller, and results return to the script without additionally waking the parent.
- Supports an object-rooted JSON Schema subset, mapping ordinary child failures/schema mismatches to null, aborting the whole run on fatal hook errors, and rejecting non-finite numbers/cycles/sparse arrays.
- MINIDSH_WORKFLOW_MAX_AGENTS defaults to 1000, and MINIDSH_WORKFLOW_MAX_CONCURRENT defaults to the CPU count capped at 32; a single combinator accepts at most 4096 entries.
- Provides workflow/start/phase/log/agent-start/agent-end/end and the four tool-workflow record types in the parent Session; cancellation stops the process and waits for child terminal states before pairing member ends.
- run_in_background uses a separate workflow job id; managed-job reuses job_output/job_list/job_kill, the output ring, concurrency caps, and session ownership.
- phase/log/member lifecycles enter incremental output, and the completion result is delivered exactly once; kill cancels both Node and the children, and job/registered/settled are persisted.
- Application restart recovers completed results from the Session and jobs output files; unsettled jobs are marked interrupted: host restarted with a single cause=recovery audit record appended, and old processes are not revived.
- HTTP jobs list/output/kill support an optional session_id ownership parameter; the original no-argument behavior is unchanged.
- Arbitrary model routing is not supported; the vm is teaching-edition capability isolation rather than the official full PTC/OS sandbox, and there is no cross-process reconnection to a running Node.

### MCP Client, Resources, and Manager (teaching-edition vertical slice)

- `core.mcp` is off by default; stdio and streamable-http use real JSON-RPC initialize/initialized, negotiating from 2026-07-28 down to 2024-11-05 in order.
- Tools are registered as mcp__server__raw while preserving the original call name; long/invalid names use a stable SHA-256 suffix. Pagination is at most 16 pages, remote duplicate names and registration conflicts reject the whole generation, and notification refreshes are serialized.
- stdio scrubs KEY/TOKEN/SECRET/PASSWORD/DSH_* environment variables and merges explicit env; HTTP supports JSON/SSE responses and MCP-Session-Id.
- `${credential:REF}` is resolved from the host environment; failure diagnostics replace the resolved value and its URL encoding with [redacted]; successful results stay verbatim.
- Tool calls support timeouts, cancellation, and notifications/cancelled; MCP isError fails closed, and text/structured/unsupported content blocks have explicit projections.
- The shared list_mcp_resources/list_mcp_resource_templates/read_mcp_resource require an explicit server; they support cursors, an empty list when the resources capability is absent, and a blob length description.
- Server instructions have a 32KiB default cap; the manager supports upsert/remove/enable/disable/restart/probe/list, state events, and HTTP APIs, and withdraws the shared resource tools when the last server is removed.
- Currently absent: automatic reconnect budgets, OAuth, full credential-provider rotation, resource subscriptions, MCP prompts, official SDK output-schema validation, and persistent image-attachment projection; HTTP cancellation waits for the transport timeout, and VM/process isolation does not apply to remote servers.

### Skills Registry, File Discovery, and Invocation (teaching-edition vertical slice)

- `core.skills` and `tools.skill` are off by default; they support runtime registration, provider registration, and merging project/custom/user/bundled roots by rank, where the higher-priority entry of the same name wins.
- File formats support `<name>/SKILL.md` and `<name>.md` under a root, parsing name/description, disable-model-invocation, user-invocable, and strict boolean words; invalid entries are skipped.
- Every discovery rescans, so frontmatter/member changes take effect on the next step; every load re-reads the body, and body edits do not change the catalog digest.
- The persistent skill-catalog user message is deduplicated by entries + completeness digest and supports initial, replacement, empty catalog, incomplete discovery, and resume; source keeps the structured entries.
- The `skill` tool loads by exact name, and disable-model-invocation excludes a skill from the catalog and from model loading; `/name` scans only direct user prompts, supports duplicate deduplication, and rejects user-invocable=false.
- Uniform `<skill_content>/<skill_resources>/<skill_instructions>` rendering provides directory/url/opaque/provider resource guidance; catalog descriptions are normalized and limited to 500 characters by default.
- Provides CLI skills/skill and HTTP list/load; when there are no skills or no tool, no initial empty catalog is published.
- The current frontmatter parsing is the teaching edition's scalar parser and does not support full YAML/Office built-in bundles/Chokidar/filesystem services or the official working-directory scope chain.

### Host Scheduling and Delivery (teaching-edition vertical slice)

- `core.schedule` and `tools.schedule` are off by default; they support seven schedule kinds (after/at/every/daily/weekly/cron/rrule), UTC millisecond instants, and local time zones.
- Cron supports five-field lists/ranges/steps and Vixie day-of-month/day-of-week OR semantics; RRULE supports MINUTELY/HOURLY/DAILY/WEEKLY/MONTHLY/YEARLY, INTERVAL/COUNT/UNTIL, and BYDAY/BYMONTHDAY/BYMONTH/BYHOUR/BYMINUTE/BYSECOND/BYSETPOS.
- The current RRULE does not support BYWEEKNO/BYYEARDAY; BYHOUR/BYMINUTE/BYSECOND each accept only one value; DST handling uses zoneinfo to skip nonexistent wall-clock times and chooses the earlier fold.
- `.minidsh/schedule.json` uses schedule v2 atomic whole-unit persistence; opening validates the version, task ids, and scheduledAt; corrupt input publishes no new file.
- schedule_create/list/view/edit support targets, active filtering, pagination, pause/resume, expectedUpdatedAt conflicts, and deletion; run does not move the original occurrence, history supports cursors, and deleteInactive cleans up only ended tasks.
- this_session reuses the existing session, new_once reserves and reuses a new session, and new creates one each time; delegated calls and delegation targets are rejected.
- Timers catch up only the most recent occurrence, and a running target injects at the next boundary; schedule/inbox acknowledges the receipt when the message is persisted, which does not mean the model has finished. History holds at most 200 entries/30 days, with a 60-second per-task delivery interval.
- HTTP provides create/list/view/history/edit/run/deleteInactive and schedule/changed; management reads do not activate the target Session.
- Currently absent: a complete RFC 5545 evaluator, associated Agent change notifications, archival admission, batch Session reminders, execution slots, and an independent storage backend.

### Goals and Automatic Rounds (teaching-edition vertical slice)

- `core.goals`, `tools.goal`, and `core.goal-rounds` are each off by default; the goal service and automatic scheduling can be enabled independently.
- Each Session has at most one current goal, the goal/change v1 event is the sole authority, and create/edit/pause/resume/complete/blocked/clear are supported.
- Updates compare and set on exact id+revision and reject stale updates; there are four states active/paused/blocked/complete, a clear tombstone, and replacement of a completed goal.
- Strict replay validates phase, revision, time monotonicity, and contiguous Goal Rounds; corrupt records fail closed.
- Automatic continuation uses a separate armed/disarmed process state; after resume/fork/restart it is disarmed and must be explicitly resumed; only persistent kind=goal user messages consume a Round.
- get_goal/create_goal/update_goal use direct-human and Goal Round source permissions; paused can be resumed only under user control; the blocked autonomous path requires at least 3 consecutive Rounds.
- After the driver goes idle, it schedules a Round on the same Session; round-limit blocks automatically, completion/blocking yields exactly one goal-wrapup final summary, and an abnormal cancellation does not restart automatically.
- Provides the goal CLI, HTTP user control, and goal/changed/activation-changed SSE.
- Currently absent: the official SessionProjection unit, an exact AgentRegistry initiator, subtree idle aggregation, durability flush preconditions, and an independent completion evaluator.

### run_code/PTC Execution Layer (teaching-edition vertical slice)

- `core.ptc` and `tools.run-code` are off by default; per-call uses an async TypeScript function, and persistent uses a Node REPL that retains variables per Session.
- The model sees only run_code and the dynamic SDK; native tools remain in the host registry for RPC invocation, preserving approval, cancellation, mutual exclusion, and event semantics.
- Node's native stripTypeScriptTypes supports erasable TypeScript; the REPL supports top-level await and trailing expressions. Independent stdout/stderr readers and the Session queue reuse the worker.
- Nested read-only calls run at most 10 in parallel, and mutating calls are exclusive; the pending default is 128, the child-call quota is timeout minutes × 1024, and run_code is exclusive.
- Default 120000ms timeout, 600000ms cap, 64MiB output, and 3000ms stop grace; supports timeout/abort/exception/protocol/worker-exit/output-limit/invalid-output.
- Returns status/value/logs/diagnostic; string logs are output verbatim, and a top-level undefined is mapped by the evaluator to null or a string.
- Supports process-group cleanup, persistent-process release, memory clearing on restart, and Seatbelt read-only/workspace-write/danger-full-access execution policies.
- Currently absent: the official Acorn source transform, full cooperative revoke/process-tree cleanup, PTC image projection, an environment reset control, sandbox_permissions escalation, and standalone TypeScript type checking.

### Agent Presets and Persona (teaching-edition vertical slice)

- `core.agent-presets` is off by default; the preset/selected event persists the choice per Session, the default standard inherits the enabled tools, and minimal exposes no tools.
- A custom plugins list determines the scoped ToolRegistry and prompt sections; each run binds per Session, concurrent sessions do not affect each other, and a missing historical preset fails closed.
- A blank Session can switch before its first model turn, repeated selection is idempotent, and switching after the first turn is rejected; fork and local child Agents inherit the parent preset.
- Persona supports prefix/suffix, complete, and includeRuntimeContext; complete keeps only the prefix, and disabling runtime no longer appends the snapshot.
- The scoped ServiceContext constrains both the PTC SDK and nested RPC, so a tool removed from the prompt cannot be called through run_code to bypass permissions.
- Provides CLI presets/preset and HTTP listing, Session query, and selection.
- The current plugin manifest is a teaching-edition name mapping, not the official Cordis YAML/Loader generation, runtime hot reload, activation audit, or full AgentPresetInspection.

### External Plugins and Product Assembly (teaching-edition equivalent layering)

- Independent distributions are discovered via Python standard `minidsh.plugins` entry points; external functionality is disabled by default, and plugin code is imported only when explicitly enabled.
- External plugins reuse `Plugin`, service dependencies, the tool registry, and feature toggles, and cannot override built-in plugin names.
- Headless, Web, and Desktop are separate Python distribution entry points that reuse the same Application; the core and Web do not depend on the GUI package.
- Desktop's `pywebview` is installed as an optional dependency and lazily imported only on the Desktop startup path.
- These are teaching-edition designs and do not align with the official Cordis profile/bundle, npm plugin loader, Electron shell, installer, or auto-update protocol.

## Not Yet Aligned

- The official complete tool set, profile/bundle format, runtime hot reload, and external npm plugins.
- The official Web Host's authentication, authorization, pages, RPC, and multiplexing protocol; the current HTTP/SSE is a teaching API, not an officially compatible implementation.
- Linux Landlock/bwrap and Windows isolation; currently only macOS Seatbelt exists, and without it enabled `read-only` conservatively rejects Shell.
- The official `sandbox_permissions` escalation protocol and full Client integration of approval-cancellation notifications; currently only the session API and the approval-cancel result are supported.
- The official complete terminal line buffering, OSC readiness protocol, PTY cross-process recovery, and Windows ConPTY; the current persistent backend supports only Unix `/bin/bash`.
- Automatic Agent wakeup on job completion, cross-process job recovery, and a long-lived resident host.
- Full attachment storage, image transformation/pricing, persistent upload indexes and quota recovery, extended request headers, and telemetry; the full model catalog metadata, background listing refresh, and the official generic LLM retry plugin are still unported.
- The official complete event types, arbitrary-plugin message projection, format catalog, Zstandard, and Windows write lock; the teaching edition has only custom v0/v1/v2 JSONL generations and the surface slice.
- The official compaction interval selection, token calibration, model-switch compaction, retry, and cancellation; the pruner uses teaching-edition metering events.
- `read` has not yet implemented streaming scans of large files; `glob` has not yet fully replicated ripgrep semantics and spill behavior.
- The anonymous user ID is stable only within the process and is not yet persisted.
- No credentialed online acceptance run against the real DeepSeek service has been performed yet; the current Web page does not support custom Authorization headers, and the token is mainly used for API clients.

## Verification Performed

- Mock Agent `glob/read` regression, feature toggles, and prompt composition.
- Two-round local Messages/SSE requests, reasoning signature replay, tool-result backfill, and JSONL persistence.
- Interleaved SSE content blocks keep their order, cross-model signatures are discarded, and HTTP 302 following is refused.
- File-policy allow, deny, no-responder fail-closed, and approval audit.
- Unread overwrite, external modification after a read, multiple matches, `replace_all`, path traversal, and symlink checks.
- Mock CLI write and read-before-edit flows.
- Shell foreground/background, timeout-to-background, incremental reads, kill, concurrency caps, and application-exit reaping.
- Large-output pruning and spooling, environment credential scrubbing, and job session events.
- Cross-process resume, turn increments, observation restoration, transcript, fork, and branch independence.
- Manual compaction preserves original events and supports resume; automatic pressure compaction, token meter, and feature toggles.
- An invalid context window is rejected; write/edit regression after Shell integration.
- HTTP health, HTML, tools, run/resume/fork/compact, and session APIs.
- SSE turn, assistant, run result, and job completion notifications.
- Bearer token acceptance/rejection; 10 concurrent requests with the same prompt generate independent sessions with intact JSONL.
- In a Python 3.9 editable install, duplicate metadata from the same distribution is deduplicated, and same-name conflicts from different distributions or exports are rejected.
- The external clock plugin is disabled by default and its module is not imported; after enabling via the config file and CLI it is active, its tool really executes, and it enters the prompt.
- Headless actually runs a Mock read; after Web actually starts, the health, tools, and external-plugin metadata APIs work, and a browser-side SSE run completes.
- The core, Headless, Web, Desktop, and external plugin all build wheels; the lightweight package from an offline wheel install contains no pywebview, and the Web static assets are complete.
- After installing pywebview into the Desktop wheel, it really creates a macOS window, loads the new page, calls the health API, and closes cleanly; official Electron packaging, signing, and installers are not verified.
- A local Messages service simulates 429→500→success, producing two RATE_LIMIT/SERVER retries and then success; 401 is not retried, and 429 fails immediately when retries are disabled.
- `/models` same-origin routing, authentication, default caching, zero-cache refresh, retention of entries with invalid capabilities, and deep-copy isolation of return values; a listing timeout is classified as TIMEOUT.
- A slow SSE response closes the socket and returns ABORTED after cancellation; a retry wait is also cancellable and does not issue the next request.
- Cancelling a full HTTP run kills the Bash process group and records aborted in JSONL; a concurrent SSE keeps its current stream after other runs finish.
- Clicking "Stop run" in the browser actually receives `run/cancelled`; the new toggles and the existing Mock file-read regression pass.
- The final wheel contains cancellation, retry, and Web assets; after offline installation, a real Mock `models` execution succeeds.
- A local Files/Messages service verifies multipart fields, image bytes, the beta header, file references, and the base64 inline format.
- Upload-cache reuse, Messages retries without re-uploading, auto downgrade on upload failure/invalid file id, and required fail-closed all pass.
- An invalid file id's cache entry is cleared and the next attempt re-uploads; 5 concurrent requests upload only once.
- Files list, read, delete, and model-listing commands pass against the local service; CLI/HTTP image sessions contain no base64 and support transcript and resume.
- Non-image content, out-of-bounds paths, and attachment hash changes are all rejected.
- This stage's final wheel contains images, Files, and updated Web assets; offline installation and the Mock `models` command pass.
- A real Unix PTY verifies cwd/export persistence across calls, non-zero exit codes, timeout reset, exit reset, and application reaping.
- Agent/Mock persistent-tool tests verify plugin replacement, schema, HTTP terminal list/kill, run cancellation, and JSONL reset events.
- The default one-shot Bash's active state, tool description, and system prompt remain unchanged.
- The final wheel contains PTY and terminal-event assets; after offline installation, a real persistent Bash execution produces correct output and exit code 0.
- After CLI repair of a v0 file with a truncated tail, the original bytes are backed up, it migrates to v1, TOOL_OUTCOME_UNKNOWN is appended, the step/turn is closed, and it can resume.
- A never-started tool call gets TOOL_NOT_STARTED; fork patches an open turn with forked while the parent session's original bytes stay unchanged.
- Future versions, seq gaps, and truncated normal loads all fail closed; recovery of only a version header plus a half-line event also passes.
- Two real processes verify flock mutual exclusion and subsequent appends after release; HTTP repair and CLI migrate pass.
- While resume holds the lock, job watcher auditing works and the conditional wait wakes promptly; concurrent HTTP turns on the same session are rejected and released after cancellation.
- The final wheel contains the version-migration and recovery modules; after offline installation, real run, resume, and repair succeed, and repairing a healthy session returns changed=false.
- A real over-long Shell tool result is pruned from 9011 to 5159 code points; the original events are unchanged, and the model projection contains PRUNE_MARKER while keeping the head and tail.
- Surface replacement shadows the original node while keeping the replacement position; resume/fork inherit the pruned view, and a repeated prune is empty.
- Transcript keeps the original full tool output; HTTP prune/surface and CLI surface/prune both pass.
- Replacements that change tool-call identity and those on non-message events are rejected before writing; Unicode and image-block ordering tests pass.
- The final wheel contains surface/pruner and Web prune assets; after offline installation, a real long Shell output is pruned via the CLI, and the surface can read the replacement.
- The question answerer, HTTP pending-list/answer, run cancellation, and queue cleanup pass; question/answered are persisted.
- todo_write whole-table projection, turn/start clearing, single-active rejection, the parallel policy, and CLI todos queries pass.
- Local Web redirect, HTML script/hidden-content removal, external-link notice, the 800-character search-summary cap, and the prompt section pass.
- Web depth-512 protection, the output cap, run cancellation, private-network/file-scheme rejection, and the unconfigured-provider error all pass; the public internet was not accessed.
- The four new tools are disabled by default and do not change the default system prompt.
- The final wheel contains questions/todo/web_tools; after offline installation, all four local real flows for questions, todos, Web fetch, and search pass.
- A local DeepSeek SSE verifies text/reasoning fragments, usage, tool arguments, and consistency with the final response; the retry event after a 500 has attempt=2.
- Tool calls and replay blocks in a max-token response are discarded; deltas from a real Agent tool call carry the correct run_id.
- Mock fallback and disabling core.streaming pass; after 300 events are written to a 256 queue, the slow listener is removed with dropped_streams=1.
- After a real browser run, the live text area shows the Mock body while the raw-event area still keeps the complete events.
- After offline installation of the final wheel, real text deltas from a local DeepSeek have correct run_ids, and slow-listener overflow disconnection passes.
- text/reasoning/tool/usage/finish codec round-trips preserve order, fields, and timing, and an invalid dt is rejected.
- After a real HTTP disconnect, a Last-Event-ID reconnect receives the subsequent assistant/result/done without creating a duplicate session; future ids and history truncation are rejected.
- The compressed stream is saved with the assistant event, and CLI/HTTP expanded reads pass; a missing index in Mock fallback was fixed.
- Disabling core.stream-recording/core.sse-replay and the existing backpressure regression pass.
- After offline installation of the final wheel, local DeepSeek fragments are compressed, saved, and expanded back to text, and the broker history replays assistant/message and turn/end.
- After a real 50ms timeout the pending question is retained, a late HTTP answer is projected as answer_to_pending_question and can resume, and the queue is cleared after cancellation.
- After the timeout, the browser submits a custom answer through the graphical form, and the pending panel closes normally.
- After offline installation of the final wheel, a 50ms timeout returns pending, a late answer is written back to the original session and can resume, and the graphical-form assets are bundled.
- A new session produces immutable generations and a manifest; after resume the first generation's bytes are unchanged, fork's current generation is 2, and repair/migrate on a healthy session returns unchanged.
- v0/v1 migration leaves the original file unchanged, the migrated audit goes into generation 1, subsequent events go into the next generation, and the original backup bytes match.
- Externally modifying the current generation makes the SHA-256 check fail; v0 truncated-tail migration, interrupted recovery, and resume pass.
- The final wheel contains the generations module; after offline installation, new sessions, resume, and v1→v2 migration pass, with the first generation's bytes unchanged.
- Seatbelt testing shows read-only writes rejected by the kernel, workspace-write succeeding while out-of-bounds /var/tmp writes are rejected, and the /tmp subpath rule passing.
- sandbox/mode and approval/policy switching, runtime-context updates, the cancelled approval, and late-answer discarding pass.
- Mode switching is rejected while a persistent PTY is open; after kill, a write under the new mode is rejected by the kernel; the default non-kernel behavior is unchanged.
- The final wheel contains the seatbelt backend; after offline installation, workspace writes succeed while out-of-bounds writes and read-only writes are rejected by the kernel.
- A real Mock delegation starts a child that reads README, and the parent receives subagent-settled; spawn excludes the parent instructions, and fork includes the completed parent history.
- A later child message produces a second turn/end; max-depth rejection, wait wakeup and invalid timeout, root-level interrupt aborted, HTTP query/message, and application-shutdown reaping pass.
- Fixed the lost reserved child session id, session continuation wrongly creating a new session, and the missing root-level interrupt permission; all subagent features are disabled by default and do not change the original prompt.
- The final wheel contains the subagents module; after offline installation, real spawn, file reading, and completion notification pass in a separate workspace, and both parent and child Sessions are persisted.
- Real JavaScript parallel starts two child Agents and returns a text array; a two-stage pipeline has no global barrier, and the structured objects and index are correct.
- process/require are invisible; unknown hook options, cyclic return values, and exceeding the two-Agent cap all fail closed; ordinary schema results can return to the script.
- Active cancellation reports cancelled, agent-end has outcome=cancelled after the children go idle, and Application shutdown reaps runs likewise; neither success nor failure leaves stray control processes.
- The model workflow tool's foreground result, the six SSE lifecycle types, and the four persistent record types pass; cross-realm objects, Node pending-Promise exit, and control-pipe races were fixed.
- The final wheel contains the Python engine and workflow_guest.js; after offline installation, a real run of two child Agents persists three independent Sessions.
- Background publication generates separate run/job ids; incremental phase/log, single final-result delivery, cross-owner rejection, and job auditing pass.
- job_kill maps killed/cancelled and waits for children to go idle; the model background tool, job_output, and HTTP session_id list/output pass.
- After restart, completed results are re-readable, and an unsettled managed job is marked interrupted: host restarted; the recovery audit has cause=recovery, and repeated restarts do not append it again.
- One-shot Bash non-zero exit codes and the foreground workflow regression pass; after offline installation of the final wheel, foreground, background, and completed-result recovery across restart pass.
- A standalone stdio fixture verifies initialize, paginated discovery, a real echo, resource list/read, server instructions, and standalone probe; binaries show only their length.
- Streamable HTTP verifies JSON/SSE responses, MCP-Session-Id, credential templates, and structured results; no real credentials were used.
- Notification-driven tool addition, remote duplicates keeping the old generation, recovery clearing errors, RunCancelled with notifications/cancelled, management enable/disable/probe, and resource cleanup pass.
- After offline installation of the final wheel, real stdio MCP tool and resource calls pass, and the child process exits after shutdown.
- Real skill files verify the initial catalog, model loading, user-triggered loading of skills hidden from the model, description updates, body re-reads, and empty-catalog replacement.
- project/custom/runtime priority, strict booleans, incomplete discovery and recovery, user-invocable=false rejection, and HTTP and CLI loading pass.
- After offline installation of the final wheel, the skill tool, /name injection, and catalog replacement pass; the default configuration produces no skill tool or catalog.
- Cron working-time instants, last weekday of the month with BYSETPOS=-1, DAILY COUNT=2 exhaustion, and the DTSTART default pass; aware/naive comparison and negative indexing were fixed.
- Create/pause/resume/conflict, a real one-second scheduled receipt, new_once's new session, history, pending, and v2 storage pass.
- Restart delivers once and advances future instants, and queue injection for a running target, delegation rejection, HTTP management, and default invisibility pass.
- After offline installation of the final wheel, real scheduled delivery, receipts, and restart catch-up pass.
- Goal create/edit/stale revision/pause-resume/complete-replacement/clear pass; after recovery and fork it is active but disarmed, and armed after an explicit resume.
- Two real Goal Rounds exhausting produce round-limit, and after the model finishes the second round there is exactly one goal-wrapup and final summary.
- Non-human creation, early blocking, HTTP/CLI control, corrupt-revision replay fail-closed, and default invisibility pass.
- After offline installation of the final wheel, consecutive Rounds, round-limit, disarmed-after-recovery, and explicit resume pass.
- Real TypeScript nested read, console logs, ToolCallError, parallel read-only calls, and PTC model/schema projection pass; the stripping wrapper for a top-level return was fixed.
- Two REPL calls retain variables, and per-call isolation and clearing on restart pass; REPL stdout interleaving, duplicate readers, and persistent-pipe false closes were fixed.
- Synchronous infinite-loop timeout, cancel abort, child-call quota, output cap, invalid-output, exception, and process reaping pass.
- Under Seatbelt, Node reads succeed while read-only writes are rejected by the kernel, and the model's run_code returns a structured result; both execution modes pass in the final wheel.
- standard's read/glob, minimal's complete persona and absence of runtime context, idempotent repeated selection on a blank session, rejection after the first turn, and child-Agent inheritance pass.
- The scoped PTC SDK can read allowed files, and a removed write still reports tool-not-found via RPC; HTTP and CLI listing/selection pass.
- After offline installation of the final wheel, preset isolation, the complete persona, and scoped PTC permissions pass.

If a later change alters any of the aligned behavior above, this file must be updated in the same Git change with an explanation; when upgrading the upstream commit, the claims must be re-verified, and prompts or protocols must never be silently replaced.
