# Third-party notices

MiniDSH is an independent re-implementation. All Python source in `src/`, `packages/`, and
`examples/` was written for this project. Some **prose** is derived from the upstream project below,
because the point of this lab is to study how that project words its instructions.

## DeepSeek Harness

- Project: `deepseek-ai/deepseek-harness` (<https://github.com/deepseek-ai/deepseek-harness>)
- Version studied: commit `fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159` (`0.2.2-rc.1`)
- License: MIT, Copyright (c) 2026 DeepSeek

### What is derived

Text reproduced or closely paraphrased from upstream, kept verbatim in English so that behaviour can
be compared one-to-one:

- The identity sentence in `src/minidsh/system_prompt.py`:
  `You are an AI agent powered by DeepSeek Harness.`
- Tool descriptions and tool-usage guidance registered by the built-in tool plugins: `read`, `write`,
  `edit`, `glob`, `bash`, `job_output`, `job_list`, `job_kill`, `ask_user_question`, `todo_write`,
  `web_fetch`, `web_search`, `skill`, `schedule_*`, `get_goal`/`create_goal`/`update_goal`,
  `subagent`, `subagent_fork`, and `workflow`.
- The compaction summary headings used by `src/minidsh/compaction.py`.
- The runtime-context wording produced by the sandbox and approval services.
- Descriptive statements about upstream behaviour in `docs/upstream-parity.md` and
  `docs/upstream-parity.zh-CN.md`.

Everything else — plugin kernel, agent loop, tool execution, permission stack, session store,
subagent tree, workflow engine, MCP client, skills registry, scheduler, goal driver, and PTC
runtime — is an original Python implementation of the same ideas, not a port of upstream source.

### Upstream license text

```text
MIT License

Copyright (c) 2026 DeepSeek

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Python standard library

The core has no third-party runtime dependencies. `pywebview` is an optional extra used only by the
`minidsh-desktop` distribution and is licensed separately by its authors.
