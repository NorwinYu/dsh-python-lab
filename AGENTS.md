# AGENTS.md

Guidance for coding agents working in this repository. Humans should read
[README.md](README.md) instead; this file only contains what an agent needs to act correctly.

## What this repository is

MiniDSH is a teaching re-implementation of a DeepSeek Harness-style agent harness, in
dependency-free Python. It is a *lab*, not a product framework. Optimize for clarity and
inspectability over cleverness and abstraction.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
```

If `import minidsh` fails after an editable install, the interpreter may skip the pip-generated
`__editable__*.pth` (some patched/vendored CPython builds do). Use either:

```bash
python3 -m pip install -e . --config-settings editable_mode=compat
# or run from source
PYTHONPATH=src python3 -m minidsh plugins
```

## Verify before you claim success

There is no test suite; the CLI *is* the harness test surface. Run these after any change:

```bash
python3 -m compileall -q src packages examples
minidsh plugins | head -5
minidsh tools | head -5
minidsh prompt | head -20

# offline end-to-end (no API key)
minidsh run "read README.md" --trace
mkdir -p /tmp/minidsh-check
minidsh --workspace /tmp/minidsh-check --sandbox-mode workspace-write --yes run "write demo.txt: hello"
cat /tmp/minidsh-check/demo.txt   # must contain: hello

# HTTP host
minidsh serve --port 3181 &        # GET /health and GET / must return 200
```

For UI changes, load the page and check it at 1500×1000 and 430×900, in light and dark theme,
in both languages. A quick headless check:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --screenshot=/tmp/shot.png --window-size=1400,900 http://127.0.0.1:3181/
```

## Non-negotiable rules

1. **Language split.** Source code, comments, docstrings, log/error strings, and the English README
   are English. Chinese lives in `README.zh-CN.md`, `docs/upstream-parity.zh-CN.md`, and the
   `zh` dictionary inside `src/minidsh/web/index.html` and `docs/index.html`. Never add Chinese to
   Python source — except the deliberate Chinese aliases in `src/minidsh/llm.py`, which are part of
   the mock prompt grammar.
2. **Two-way mock grammar.** `src/minidsh/llm.py` accepts English prefixes and Chinese aliases that
   map to the *same* `Intent` objects. Any new mock trigger needs both, plus a row in
   `docs/mock-model.md` and the README cheat sheet.
3. **Fail closed.** Missing approver, unknown feature flag, corrupted session, missing preset,
   oversized request: raise an explicit error. Never degrade silently.
4. **Facts vs. projection.** Session JSONL is append-only; never rewrite or delete events. Pruning
   and compaction produce *new* events that change the model view only.
5. **One provider per service.** `ServiceContext.provide()` raises on duplicates. Do not add
   fallback resolution or service overrides.
6. **Dependencies.** Core must stay stdlib-only. `pywebview` is an optional extra used only by the
   Desktop product. Do not add runtime dependencies without explicit approval.
7. **Docs parity.** If you change a flag, default, limit, event name, or API path, update all of:
   `README.md`, `README.zh-CN.md`, `docs/modules.md`, `docs/upstream-parity.md` and its `.zh-CN`
   counterpart, and `config.example.json` if it is a flag.

## Where things live

| Concern | File |
|---|---|
| Feature flags and defaults | `src/minidsh/config.py` (`DEFAULT_FEATURES`) |
| Composition root | `src/minidsh/app.py` |
| Agent loop | `src/minidsh/agent.py` |
| Tool schemas and dispatch | `src/minidsh/tools.py` |
| Sandbox / approval / observations | `src/minidsh/sandbox.py`, `approval.py`, `observations.py` |
| Shell and jobs | `src/minidsh/shell.py`, `jobs.py`, `pty_shell.py` |
| Session storage and projection | `src/minidsh/session.py`, `generations.py`, `surface.py` |
| Model adapters | `src/minidsh/llm.py` (mock), `deepseek.py` (real) |
| HTTP/SSE host | `src/minidsh/server.py` |
| Web workbench | `src/minidsh/web/index.html` |
| Docs site | `docs/index.html` |

## Style

- Python 3.9 compatible. No `match`, no `X | Y` type unions, no `tomllib`.
- Standard library only in `src/`. `from __future__` not required.
- Keep functions short and linear; this code is read far more than it is run.
- Prefer explicit error messages that state the violated invariant and the observed value.
- No logging framework; events are the observability mechanism.
- UI: vanilla HTML/CSS/JS, no build step, no external CDNs. Use the existing CSS custom properties.

## Commit hygiene

Small, single-purpose commits. The message should name the layer (e.g. `session:`, `web:`,
`docs:`). Do not commit `.venv/`, `.minidsh/`, screenshots, or generated caches.
