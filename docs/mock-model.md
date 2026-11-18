# The Mock Model Grammar

`MockModel` exists so that agent behaviour is **reproducible**. It has no natural-language
understanding: a prompt either matches a documented prefix or it does not. That constraint is the
feature — you can assert on an exact tool-call sequence instead of hoping a sampled trajectory
matches your intent.

Source: `src/minidsh/llm.py` (`parse_intent`, `_parse_english_intent`, `_parse_chinese_intent`).

## Rules

1. Matching is prefix-based and case-insensitive for ASCII; Chinese aliases match literally.
2. English is the canonical grammar. Every intent also accepts one Chinese alias.
3. Both grammars produce the **same** `Intent` object, so nothing downstream can tell them apart.
4. An unmatched prompt returns the help text and **no tool call**. The mock never guesses.
5. A tool is only called if it is registered. If a prefix matches an intent whose tool is disabled,
   the mock falls through to the help text.

## Intent table

| Intent | English prefix | Chinese alias | Tool call |
|---|---|---|---|
| `list` | `list`, `ls`, `glob`, or a prompt containing `directory` | `目录` or a prompt containing `目录` | `glob {pattern: "*"}` |
| `read` | `read <path>` | `读取 <path>` | `read {file_path}` |
| `write` | `write <path>: <content>` | `写入 <path>：<content>` | `write {file_path, content}` |
| `edit` | `edit <path>: replace <old> with <new>` | `编辑 <path>：把<old>替换为<new>` | `read` then `edit` |
| `edit` (arrow form) | `edit <path>: <old> -> <new>` | — | `read` then `edit` |
| `run` | `run <command>` | `执行 <command>` | `bash {command, run_in_background: false}` |
| `run` (background) | `run in background <command>`, `background <command>` | `执行后台 <command>` | `bash {command, run_in_background: true}` |
| `job-list` | `jobs`, `job list`, `list jobs` | `任务列表` | `job_list {}` |
| `job-output` | `job output <job-id>` | `读取任务 <job-id>` | `job_output {job_id, wait: true}` |
| `job-kill` | `kill job <job-id>`, `stop job <job-id>` | `终止任务 <job-id>` | `job_kill {job_id}` |
| `ask` | `ask <question>`, `question <question>` | `提问 <question>` | `ask_user_question` |
| `todo` | `todo <task>` | `待办 <task>` | `todo_write` |
| `fetch` | `fetch <url>`, `web-fetch <url>` | `获取 <url>` | `web_fetch {url}` |
| `search` | `search <query>`, `web-search <query>` | `搜索 <query>` | `web_search {queries}` |
| `skill` | `skill <name>` | `使用技能 <name>` | `skill {name}` |
| `subagent` | `delegate <description> \| <task>` | `委派 <描述> \| <任务>` | `subagent` |
| `subagent-fork` | `fork agent <task>`, `subagent fork <task>` | `派生 <task>` | `subagent_fork` |
| `agent-list` | `agents`, `agent list`, `subagents` | `子代理列表` | `list_agents {}` |
| `message` | `message <agent>: <text>` | `发送消息 <agent>：<text>` | `send_message` |
| `interrupt` | `interrupt <agent>` | `中断子代理 <agent>` | `interrupt_agent` |
| `wait` | `wait agent 5000`, `wait for agent` | `等待子代理输入 5000` | `wait_agent {timeout_ms}` |
| `workflow` | `workflow <a>\|<b>` | `工作流 <a>\|<b>` | `workflow` |
| `workflow` (background) | `workflow in background <a>` | `工作流后台 <a>` | `workflow {run_in_background: true}` |
| `run-code` | `run code <json>` | `运行代码 <json>` | `run_code` |
| `goal-create` | `goal create <objective>\|<rounds>` | `创建目标 <objective>\|<rounds>` | `create_goal` |
| `goal-get` | `goal`, `goal get`, `goal show`, `goal status` | `读取目标` | `get_goal {}` |
| `goal-update` | `goal update <json>` | `更新目标 <json>` | `update_goal` |
| `schedule-create` | `schedule create <json>` | `定时任务 <json>` | `schedule_create` |
| `schedule-list` | `schedule`, `schedule list` | `定时列表` | `schedule_list {}` |
| `schedule-view` | `schedule view <id>` | `定时查看 <id>` | `schedule_view {id}` |
| `schedule-edit` | `schedule edit <json>` | `定时编辑 <json>` | `schedule_edit` |

## Behaviour details

- **Edit is two steps.** The first turn calls `read`; the follow-up turn (when the last message is
  the tool result) calls `edit`. This exercises the "read before write" observation rule.
- **Compaction prompts are special-cased.** If the system prompt starts with
  `You are now acting as a compaction engine`, the mock returns a fixed structured summary.
- **Subagent settlement is special-cased.** A user message with source kind `subagent-settled`
  returns "Subagent result received and summarized." and no tool call.
- **JSON arguments are parsed literally.** `schedule create {...}` and `run code {...}` accept a
  JSON object after the prefix; malformed JSON raises during tool-argument construction.
- **Quoted paths are unwrapped.** `read "a b.txt"` strips one layer of `"`, `'`, or backticks.
- **Runtime-context and skill-catalog messages are skipped** when looking for the active prompt.

## Adding a trigger

1. Add the English rule to `_parse_english_intent`.
2. Add the Chinese alias to `_parse_chinese_intent`.
3. Map the `Intent` to a tool call in `MockModel.complete`, guarded by
   `if "tool_name" in available`.
4. Add a row to this table and the README cheat sheet.
5. Add a `quick` chip in `src/minidsh/web/index.html` if it is a common demo.
