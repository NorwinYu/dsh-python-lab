/* MiniDSH textbook chapters. Each entry is one chapter of the site.
   Shape: { id, n, part, partNo, partTitle{en,zh}, partDesc{en,zh},
            files[], flags[[name,on|off]], industry[label,en,zh,url],
            en|zh: { title, thesis, concept, why, how, try, observe[], tradeoff } }
   Only the first chapter of a part carries partNo/partTitle/partDesc. */
window.MINIDSH_CHAPTERS = [
  {
    id: "plugin-kernel", n: "01", part: "kernel",
    partNo: "PART I",
    partTitle: { en: "Kernel", zh: "内核" },
    partDesc: { en: "How features become services, and why turning one off removes it everywhere.", zh: "功能如何变成服务，以及为什么关掉一处就会处处消失。" },
    files: ["config.py", "plugins.py", "discovery.py"],
    en: {
      title: "Plugin kernel and dependency activation",
      thesis: "A harness is an assembly problem before it is a prompting problem.",
      concept: "Every capability is a plugin that declares the services it provides and the services it needs. The kernel builds a service graph from a dictionary of feature flags, then activates plugins in dependency order.",
      why: "Without this, disabling a feature leaves half of it alive: a tool schema disappears but its prompt section stays, or a service is gone but its dependents keep running. Each of those produces a model that is told to do something it cannot do.",
      how: "PluginManager.activate_all scans pending plugins repeatedly until a full pass makes no progress, so registration order never matters — only dependencies do. A plugin whose requirements are missing stays pending; a plugin that raises becomes error. Deactivation runs in reverse order, and ServiceContext allows exactly one provider per service name.",
      try: "minidsh plugins\nminidsh --disable core.jobs plugins\nminidsh --disable llm.mock --disable llm.deepseek plugins",
      observe: ["Plugins that need jobs revert to missing=jobs instead of crashing.", "Disabling both models leaves agent.core pending, because its llm dependency is absent.", "Deactivation is reverse-ordered, so resources unwind in the opposite order they were created."],
      tradeoff: "The graph converges once at startup. There is no hot reload, no scoped child graph beyond presets, and no version negotiation between plugins.",
      industry: ["Harness engineering", "The harness, not the model, owns ordering, limits, persistence and failure handling.", "顺序、边界、持久化与失败处理属于 Harness 而不属于模型。", "https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md"]
    },
    zh: {
      title: "插件内核与依赖激活",
      thesis: "Harness 首先是组装问题，然后才是提示词问题。",
      concept: "每个能力都是一个插件，声明自己提供哪些服务、依赖哪些服务。内核根据功能开关字典构建服务图，再按依赖顺序激活插件。",
      why: "没有这层，关掉一个功能会留下半条命：工具 schema 消失了，提示词段落还在；服务没了，依赖它的插件还在跑。这两种情况都会得到同一个结果——模型被要求做它做不到的事。",
      how: "PluginManager.activate_all 反复扫描 pending 插件，直到整整一轮没有任何进展，所以注册顺序无关紧要，只有依赖关系重要。依赖缺失的插件保持 pending，激活抛错的变成 error。卸载按注册逆序执行，ServiceContext 对每个服务名只允许一个提供者。",
      try: "minidsh plugins\nminidsh --disable core.jobs plugins\nminidsh --disable llm.mock --disable llm.deepseek plugins",
      observe: ["依赖 jobs 的插件变成 missing=jobs，而不是直接崩溃。", "两个模型都关掉后，agent.core 保持 pending，因为缺少 llm 依赖。", "卸载是逆序的，资源按创建的相反顺序释放。"],
      tradeoff: "服务图只在启动时收敛一次。没有热重载，除预设外没有子作用域，也没有插件之间的版本协商。"
    }
  },
  {
    id: "composition", n: "02", part: "kernel",
    files: ["app.py", "events.py"],
    en: {
      title: "Composition root and service context",
      thesis: "One place assembles the system; everything else receives what it needs.",
      concept: "Application is the only assembly point. It registers built-in plugins, then external entry points, then exposes read-only accessors so CLI, HTTP and Desktop share one object graph.",
      why: "If each product assembles its own stack, they drift: the Web UI gains a tool the CLI lacks, approval behaves differently, and a bug reproduces in one shell but not another.",
      how: "AgentPlugin builds the Agent from required services (llm, tools, systemPrompt, sandboxPolicy, approval) and optional ones (session, observations, compaction, subagents, skills, schedule, goals, ptc, presets). Events are emitted through a synchronous EventBus; durable buffering is the SSE broker's job, not the bus's.",
      try: "minidsh config\nminidsh plugins | head -5\nminidsh serve --port 3080",
      observe: ["config prints the merged configuration, including every flag.", "The HTTP host and the CLI report the same plugin and tool lists.", "Closing the application deactivates plugins in reverse order."],
      tradeoff: "A single composition root plus a synchronous bus means no per-request isolation: two concurrent runs share the same process, services and event loop.",
      industry: ["Dependency injection at the edge", "Products stay thin; the kernel is assembled once and reused by every shell.", "产品保持轻薄，内核只组装一次并被所有外壳复用。", "https://github.com/NorwinYu/dsh-python-lab/blob/main/docs/modules.md"]
    },
    zh: {
      title: "组合根与服务上下文",
      thesis: "系统只在一个地方组装，其余部分只接收自己需要的东西。",
      concept: "Application 是唯一的组装点：先注册内置插件，再注册外部 entry point，最后暴露只读访问器，让 CLI、HTTP 与 Desktop 共享同一份对象图。",
      why: "如果每个产品各自组装一套栈，它们就会漂移：Web 多了一个 CLI 没有的工具，审批行为不一致，同一个 bug 只在一个外壳里复现。",
      how: "AgentPlugin 用必需服务（llm、tools、systemPrompt、sandboxPolicy、approval）与可选服务（session、observations、compaction、subagents、skills、schedule、goals、ptc、presets）构造 Agent。事件通过同步 EventBus 发出；持久缓冲是 SSE broker 的职责，不是 bus 的。",
      try: "minidsh config\nminidsh plugins | head -5\nminidsh serve --port 3080",
      observe: ["config 输出合并后的完整配置，包含每个开关。", "HTTP 宿主与 CLI 报告的插件、工具列表一致。", "关闭应用时插件按逆序卸载。"],
      tradeoff: "单一组合根 + 同步总线意味着没有按请求隔离：两个并发运行共享同一进程、同一批服务与同一个事件循环。"
    }
  },
  {
    id: "flags", n: "03", part: "kernel",
    files: ["config.py", "system_prompt.py", "tools.py"],
    en: {
      title: "Feature flags as capability declaration",
      thesis: "A flag is not a checkbox; it is a statement about what the model can do.",
      concept: "Fifty-three flags describe the whole harness. Some gate services, some gate tools, some gate model providers or storage. Turning one off is a claim about the environment, not a UI preference.",
      why: "Flags that only hide UI leave dead instructions behind. A prompt that says use read when read is not registered trains the model to hallucinate tool calls — and that failure looks like a model bug, which is why it is expensive to debug.",
      how: "The flag dictionary decides which plugins are even registered, so disabling tools.edit removes its schema, its prompt section, and its service in one step. The write guidance is assembled conditionally: it gains the prefer edit clause only when edit is on. Unknown flag names fail at startup instead of being ignored.",
      try: "minidsh prompt\nminidsh --disable tools.edit prompt\nminidsh --disable tools.glob prompt",
      observe: ["The write section grows the prefer-edit sentence only while edit is enabled.", "A disabled tool loses both its JSON schema and its guidance paragraph.", "An unknown flag in config makes startup fail closed."],
      tradeoff: "Flags are process-wide and resolved at startup. There is no per-turn capability toggle except through presets, and no audit log of who changed a flag.",
      industry: ["Capability declaration", "Prompts should be derived from the capability set, never maintained by hand beside it.", "提示词应由能力集合推导，而不是在它旁边手工维护。", "https://agents.md"]
    },
    zh: {
      title: "功能开关即能力声明",
      thesis: "开关不是复选框，而是关于模型能做什么的声明。",
      concept: "53 个开关描述整个 Harness：有的门控服务，有的门控工具，有的门控模型提供方或存储。关掉一个开关，是对运行环境作出断言，而不是调整界面偏好。",
      why: "只在 UI 上隐藏的开关会把失效指令留在提示词里。提示词说用 read，而 read 根本没注册，等于训练模型幻觉工具调用——这种故障看起来像模型的问题，所以调试代价极高。",
      how: "开关字典决定插件是否被注册，所以关掉 tools.edit 会同时移除 schema、提示词段落与服务。write 的指引是条件拼装的：只有 edit 开启时才会加上 prefer edit 那句话。未知开关名在启动时直接失败，而不是被忽略。",
      try: "minidsh prompt\nminidsh --disable tools.edit prompt\nminidsh --disable tools.glob prompt",
      observe: ["只有 edit 开启时，write 段落才会多出 prefer-edit 那句话。", "被禁用的工具同时失去 JSON schema 与指引段落。", "配置里的未知开关会让启动失败关闭。"],
      tradeoff: "开关是进程级、启动时解析的。除预设外没有按回合切换能力的机制，也没有记录谁改过开关的审计日志。"
    }
  },

  {
    id: "agent-loop", n: "04", part: "runtime",
    partNo: "PART II",
    partTitle: { en: "Runtime", zh: "运行时" },
    partDesc: { en: "The loop, the prompt, the tool pipeline, and the stream.", zh: "循环、提示词、工具管线与流式输出。" },
    files: ["agent.py", "cancellation.py"],
    en: {
      title: "Agent loop: turns and steps",
      thesis: "One prompt is one turn; model calls and tool calls inside it are steps.",
      concept: "run() executes one turn. Inside a turn the loop alternates model calls and tool calls, bounded by max_steps (default 4). It owns ordering, budgets, persistence and failure handling — but not intelligence.",
      why: "Without an explicit bound, a model that keeps calling tools produces an unbounded bill. Without a turn/step distinction, there is no stable unit for persistence, cancellation, or observability.",
      how: "Before the first model call the loop assembles context: system prompt sections, runtime context, skill catalog, schedule inbox, and history derived from the surface projection. Tool results are appended and fed back. Cancellation is checked at model, tool and shell boundaries, and a turn can end with stop, max_steps, aborted or error.",
      try: "minidsh run \"read README.md\" --trace\nminidsh sessions\nminidsh session <id>",
      observe: ["Events arrive in a fixed order: turn/start, assistant/message, tools/pre-execute, tools/post-execute, turn/end.", "max_steps reached is a budget outcome, not a crash.", "A cancelled turn records turn/end with reason aborted."],
      tradeoff: "max_steps is fixed per Agent instance and not adaptive. There is no planner, no reflection step, and no automatic retry of a failed tool call beyond the model deciding to try again.",
      industry: ["Agent arithmetic", "Turns and steps are the unit of cost, cancellation and telemetry in every production harness.", "回合与步骤是生产级 Harness 里成本、取消与遥测的基本单位。", "https://opentelemetry.io/docs/specs/semconv/gen-ai/"]
    },
    zh: {
      title: "Agent Loop：回合与步骤",
      thesis: "一次输入是一个回合；回合里的模型调用与工具调用是步骤。",
      concept: "run() 执行一个回合。回合内循环交替进行模型调用与工具调用，由 max_steps（默认 4）限制。它负责顺序、预算、持久化与失败处理，但不负责智能。",
      why: "没有显式上限，一个不断调用工具的模型会产出无上限的账单。没有回合/步骤的区分，就没有稳定的持久化、取消与观测单位。",
      how: "首次模型调用前，循环组装上下文：系统提示词各段、runtime context、技能目录、定时收件箱，以及从 surface 投影得到的历史。工具结果被追加并回喂。取消在模型、工具与 Shell 边界检查，回合可以以 stop、max_steps、aborted 或 error 结束。",
      try: "minidsh run \"read README.md\" --trace\nminidsh sessions\nminidsh session <id>",
      observe: ["事件顺序固定：turn/start、assistant/message、tools/pre-execute、tools/post-execute、turn/end。", "达到 max_steps 是预算结果，不是崩溃。", "被取消的回合记下 reason=aborted 的 turn/end。"],
      tradeoff: "max_steps 按 Agent 实例固定，不做自适应。没有规划器、没有反思步骤，也没有在模型决定重试之外自动重试失败工具调用。"
    }
  },
  {
    id: "prompt", n: "05", part: "runtime",
    files: ["system_prompt.py", "sandbox.py", "approval.py"],
    en: {
      title: "System prompt assembly and runtime context",
      thesis: "The prompt is generated from capabilities; the environment is a snapshot, not a sentence.",
      concept: "Sections carry an order and a name. Identity is fixed at -1000, tool guidance is registered at 1000-1600, and the runtime context — workspace, sandbox policy, approval policy — is sent as a separate user message after the instruction.",
      why: "Baking the environment into the system prompt makes it stale: a resumed session would keep the policy it started with. Sending it as a snapshot means resume regenerates it from current configuration.",
      how: "render() joins the registered sections in order; render_without_tools() supports the complete persona mode. Guidance sections are registered by the tool plugin itself, so they can never outlive their tool. The write clause is conditional on edit being enabled.",
      try: "minidsh prompt\nminidsh --sandbox-mode workspace-write --yes prompt\nminidsh --disable tools.read prompt",
      observe: ["Runtime context appears as its own message, not inside the system text.", "Changing the sandbox mode changes the snapshot on the next run or resume.", "Disabling a tool removes its paragraph from the rendered prompt."],
      tradeoff: "Section order is fixed in code; beyond persona prefix/suffix in presets there is no per-session prompt authoring.",
      industry: ["Context engineering", "Separate stable instructions from volatile environment facts so caches survive and resume stays correct.", "把稳定指令与易变环境事实分开，缓存才能命中，恢复才不会带错策略。", "https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md"]
    },
    zh: {
      title: "系统提示词组装与 runtime context",
      thesis: "提示词由能力生成；环境是一份快照，不是一句话。",
      concept: "每个段落带名字与顺序。身份段固定在 -1000，工具指引注册在 1000–1600，而 runtime context（工作区、沙箱策略、审批策略）作为独立用户消息放在指令之后。",
      why: "把环境写死进系统提示词会过期：恢复的会话会继续沿用启动时的策略。改成快照后，resume 会按当前配置重新生成。",
      how: "render() 按顺序拼接已注册段落，render_without_tools() 支持 complete persona 模式。指引段落由工具插件自己注册，因此不可能比工具活得更久。write 那句话是否出现取决于 edit 是否启用。",
      try: "minidsh prompt\nminidsh --sandbox-mode workspace-write --yes prompt\nminidsh --disable tools.read prompt",
      observe: ["runtime context 是独立消息，不在系统提示词正文里。", "切换沙箱模式后，下一次运行或 resume 的快照随之改变。", "禁用工具后，它对应的提示词段落一起消失。"],
      tradeoff: "段落顺序写死在代码里；除预设的 persona 前后缀外，没有按会话编写提示词的能力。"
    }
  },
  {
    id: "tool-registry", n: "06", part: "runtime",
    files: ["tools.py", "shell.py"],
    en: {
      title: "Tool registry and execution pipeline",
      thesis: "Every tool call passes the same four gates: policy, approval, observation, execution.",
      concept: "ToolRegistry owns the schemas the model sees and the pipeline that executes them. A tool is a name, a JSON Schema, a description, and a function that returns a result or raises a classified error.",
      why: "If each tool implements its own checks, security becomes a per-tool feature and one forgotten check is a breach. A single pipeline makes every capability inherit the same guarantees.",
      how: "The pipeline resolves and validates arguments, applies the sandbox path policy, requests approval when needed, records file observations for writes, executes, then emits tool/call and tool/result (plus tools/pre-execute and tools/post-execute on the bus). Errors are classified so callers can tell a command failure from a harness failure.",
      try: "minidsh tools\nminidsh run \"read README.md\" --trace\nminidsh --sandbox-mode workspace-write --yes run \"run printf hi\"",
      observe: ["tools prints the exact schemas the model receives.", "A non-zero exit code is a command result, not a harness error.", "Policy and approval decisions appear as events, not as prose in the result."],
      tradeoff: "Tools are synchronous and in-process; there is no per-tool timeout, no streaming result, and no cancellation token passed into a tool body beyond the shared run token.",
      industry: ["Tool calling as an API", "Schemas and envelopes are the contract; vague ones produce invalid calls and unfixable errors.", "schema 与返回值信封就是契约；含糊的契约只会带来非法调用和无法定位的错误。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "工具注册表与执行管线",
      thesis: "每次工具调用都经过同一四道闸门：策略、审批、观察、执行。",
      concept: "ToolRegistry 掌握模型看到的 schema 与执行它们的管线。一个工具由名字、JSON Schema、描述，以及一个返回结果或抛出分类错误的函数组成。",
      why: "如果每个工具各自做检查，安全就变成了按工具实现的功能，漏掉一处就是一次越界。统一管线让所有能力自动继承同样的保证。",
      how: "管线依次解析并校验参数、应用沙箱路径策略、必要时请求审批、为写操作记录文件观察、执行，然后发出 tool/call 与 tool/result（并在总线上发 tools/pre-execute 与 tools/post-execute）。错误被分类，调用方可以区分命令失败与 Harness 失败。",
      try: "minidsh tools\nminidsh run \"read README.md\" --trace\nminidsh --sandbox-mode workspace-write --yes run \"run printf hi\"",
      observe: ["tools 输出模型实际收到的 schema。", "非零退出码是命令结果，不是 Harness 异常。", "策略与审批决定以事件形式出现，而不是写在结果文本里。"],
      tradeoff: "工具是同步、进程内的；没有按工具的超时、没有流式结果，除共享运行令牌外也没有把取消传进工具内部。"
    }
  },
  {
    id: "file-tools", n: "07", part: "runtime",
    files: ["tools.py"],
    en: {
      title: "The four filesystem tools",
      thesis: "File tools are where an agent is most useful and most dangerous.",
      concept: "read returns line-numbered text inside an envelope; glob finds paths; write creates or replaces atomically; edit replaces a literal string, unique by default.",
      why: "Reading and writing files is the core loop of a coding agent. Get the limits wrong and the model truncates silently, retries forever, or overwrites work it never read.",
      how: "read caps at 2000 lines, 2000 characters per line and 50 KiB total. glob returns at most 100 paths, includes hidden files, excludes VCS directories and sorts by modification time. write uses temp-file plus atomic replace. edit requires an exact literal match unless replace_all is set, and the same session must have read the file first.",
      try: "minidsh run \"read README.md\"\nminidsh run \"list\"\nminidsh --sandbox-mode workspace-write --yes run \"write demo.txt: hello\"",
      observe: ["read output carries a path/type/content envelope, not raw text.", "write answers Created file or Updated file, not a diff.", "An edit whose old_string appears twice is refused until it is made unique."],
      tradeoff: "No patch or diff tool, no binary editing, no multi-file transaction. A large file is read in windows and the model must remember to use offset.",
      industry: ["Envelope design", "Deterministic wrappers let a model tell a truncated read from an empty file.", "确定的返回信封让模型能区分被截断的读取和空文件。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "文件工具四件套",
      thesis: "文件工具是 Agent 最有用的地方，也是最危险的地方。",
      concept: "read 返回带行号的文本并包在信封里；glob 发现路径；write 原子地创建或覆盖；edit 替换字面字符串，默认要求唯一匹配。",
      why: "读写文件是编码 Agent 的核心循环。限制设错，模型就会静默截断、无限重试，或者覆盖掉自己从没读过的内容。",
      how: "read 上限为 2000 行、单行 2000 字符、总计 50 KiB。glob 最多 100 条，包含隐藏文件、排除 VCS 目录、按修改时间排序。write 用临时文件加原子替换。edit 要求字面精确匹配（除非 replace_all），且同一会话必须先读过该文件。",
      try: "minidsh run \"read README.md\"\nminidsh run \"list\"\nminidsh --sandbox-mode workspace-write --yes run \"write demo.txt: hello\"",
      observe: ["read 输出带 path/type/content 信封，而不是裸文本。", "write 回答 Created file 或 Updated file，不返回 diff。", "old_string 出现两次的 edit 会被拒绝，直到它变得唯一。"],
      tradeoff: "没有 patch/diff 工具、没有二进制编辑、没有多文件事务。大文件只能分窗读取，模型必须记得用 offset。"
    }
  },
  {
    id: "streaming", n: "08", part: "runtime",
    files: ["deepseek.py", "agent.py", "stream_codec.py"],
    en: {
      title: "Streaming deltas",
      thesis: "Streaming is an observability feature first and a latency feature second.",
      concept: "While the model response is parsed, incremental events are emitted: text deltas, reasoning deltas, tool-call deltas, block starts and usage. The final message is still assembled as one authoritative object.",
      why: "A UI that waits for the full response cannot show work in progress, and a user cannot interrupt something they cannot see. But a stream must never become the source of truth, or a dropped connection corrupts history.",
      how: "Each delta carries session, run, turn, step and attempt, so a consumer can discard the draft of a failed attempt and start clean on retry. Models that cannot stream get their final text re-emitted once with fallback=true. A max-token response drops its tool calls, because executing a truncated call is worse than not calling it.",
      try: "minidsh serve --port 3080\ncurl -N \"http://127.0.0.1:3080/v1/runs/stream?prompt=read%20README.md\"",
      observe: ["assistant/delta carries attempt numbers; attempt 2 means the first draft must be thrown away.", "assistant/message still arrives once and is what gets persisted.", "A truncated response produces no tool call."],
      tradeoff: "Deltas are best-effort: a slow consumer loses its stream rather than slowing the agent. Streaming text is not replayed from disk on the next connection except through recorded chunks.",
      industry: ["Token streaming", "Incremental output plus an authoritative final message is the standard split.", "增量输出加上一个权威的最终消息，是业界标准的拆分方式。", "https://opentelemetry.io/docs/specs/semconv/gen-ai/"]
    },
    zh: {
      title: "流式增量",
      thesis: "流式首先是可观测性特性，其次才是延迟特性。",
      concept: "在解析模型响应的同时发出增量事件：文本增量、思考增量、工具调用增量、block 开始与 usage。最终消息仍然被组装成一个权威对象。",
      why: "等完整响应的界面无法展示进行中的工作，用户也无法中断自己看不见的东西。但流不能成为事实来源，否则一次断线就会污染历史。",
      how: "每个增量都带 session、run、turn、step 与 attempt，消费方因此可以在重试时丢弃失败尝试的草稿。不支持流式的模型会把最终文本补发一次，标记 fallback=true。max-token 截断的响应会丢弃工具调用，因为执行一个被截断的调用比不调用更糟。",
      try: "minidsh serve --port 3080\ncurl -N \"http://127.0.0.1:3080/v1/runs/stream?prompt=read%20README.md\"",
      observe: ["assistant/delta 带 attempt；attempt=2 意味着第一份草稿必须丢弃。", "assistant/message 仍然只到达一次，且它是被持久化的内容。", "被截断的响应不会产生工具调用。"],
      tradeoff: "增量是尽力而为：慢消费者会丢掉自己的流，而不是拖慢 Agent。除记录的分片外，流式文本不会在下一次连接时从磁盘重放。"
    }
  },
  {
    id: "sandbox", n: "09", part: "safety",
    partNo: "PART III",
    partTitle: { en: "Safety", zh: "安全" },
    partDesc: { en: "Policy, approval, observation, kernel enforcement, processes, credentials.", zh: "策略、审批、观察、内核强制、进程与凭据。" },
    files: ["sandbox.py"],
    en: {
      title: "Sandbox policy",
      thesis: "A path check is a policy decision, not a security guarantee.",
      concept: "Three modes decide where an operation may touch: read-only, workspace-write, danger-full-access. The policy resolves paths against the workspace and rejects escapes before the tool runs.",
      why: "The model does not know your directory layout, and a relative path plus a symlink is enough to leave the project. Without a single resolver, every tool invents its own boundary logic.",
      how: "SandboxPolicy resolves the workspace, rejects parent traversal and symlink escapes under workspace-write, and allows outside paths only under danger-full-access. The same object feeds the tool pipeline, the runtime context, and the process backend, so prompt and enforcement cannot disagree.",
      try: "minidsh run \"write denied.txt: hello\"\nminidsh --sandbox-mode workspace-write --yes run \"write ok.txt: hello\"\nminidsh --sandbox-mode danger-full-access run \"write /tmp/outside.txt: hi\"",
      observe: ["Default runs refuse writes with a policy error, not a tool error.", "workspace-write still rejects .. and symlink escapes.", "danger-full-access removes the workspace restriction and the approval prompt."],
      tradeoff: "This layer is advisory: it stops the model, not a compromised tool. Real enforcement comes from the kernel sandbox in chapter 12.",
      industry: ["Least privilege", "Workspace scoping is the baseline; kernel isolation is the enforcement.", "工作区限定只是基线，内核隔离才是强制执行。", "https://github.com/zeroclaw-labs/zeroclaw/blob/a56c345d51dd8ab562e9351e0d4ab83f6a741db9/docs/book/src/security/sandboxing.md"]
    },
    zh: {
      title: "沙箱策略",
      thesis: "路径检查是策略判断，不是安全保证。",
      concept: "三种模式决定操作可以碰哪里：read-only、workspace-write、danger-full-access。策略在工具运行前把路径解析到工作区，并拒绝越界。",
      why: "模型不知道你的目录结构，一个相对路径加一个符号链接就足以离开项目。没有统一的解析器，每个工具都会各写一套边界逻辑。",
      how: "SandboxPolicy 解析工作区，在 workspace-write 下拒绝父级穿越与符号链接逃逸，只有 danger-full-access 才允许外部路径。同一个对象同时供工具管线、runtime context 与进程后端使用，因此提示词与强制执行不可能互相矛盾。",
      try: "minidsh run \"write denied.txt: hello\"\nminidsh --sandbox-mode workspace-write --yes run \"write ok.txt: hello\"\nminidsh --sandbox-mode danger-full-access run \"write /tmp/outside.txt: hi\"",
      observe: ["默认运行以策略错误拒绝写入，而不是工具错误。", "workspace-write 仍然拒绝 .. 与符号链接逃逸。", "danger-full-access 同时移除工作区限制与审批提示。"],
      tradeoff: "这一层是劝告性的：它拦住的是模型，而不是被攻陷的工具。真正的强制在 12 章的内核沙箱。"
    }
  },
  {
    id: "approval", n: "10", part: "safety",
    files: ["approval.py"],
    en: {
      title: "Approval and failing closed",
      thesis: "No answerer is not the same as yes.",
      concept: "Before a write or a shell command, the pipeline asks an approver. Outcomes are allowed-once, rejected, unavailable and cancelled, and every ask/decide pair is recorded as an audit event.",
      why: "The most common harness vulnerability is a default allow path: a missing prompt, a non-interactive environment, and suddenly the model can write anywhere. Failing closed turns that into a refusal.",
      how: "ask returns unavailable when no interactive answerer exists; never rejects automatically; --yes approves everything in the process. Cancelling an approval records cancelled immediately and discards a late answer from the answerer thread. Decisions carry request id, tool name and call id.",
      try: "minidsh --sandbox-mode workspace-write --approval-policy never run \"write x.txt: hi\"\nminidsh --sandbox-mode workspace-write run \"write y.txt: hi\"\nminidsh session <id>",
      observe: ["A non-interactive run produces unavailable, not allowed.", "--yes produces allowed-once with the same audit shape.", "approval/asked and approval/decided always come in pairs."],
      tradeoff: "Approval is a human control, not a boundary: --yes disables it entirely, and a compromised tool can call the same code path. Pair it with the kernel sandbox.",
      industry: ["Human-in-the-loop", "Default deny plus explicit answers is the pattern; silent fallbacks are how agents escape.", "默认拒绝加显式回答才是正解；静默回退正是 Agent 越权的方式。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "审批与失败关闭",
      thesis: "「没有回答者」不等于「同意」。",
      concept: "写入或执行 Shell 之前，管线会询问审批者。结果有 allowed-once、rejected、unavailable、cancelled 四种，每次询问与决定都作为审计事件成对记录。",
      why: "Harness 最常见的漏洞就是「默认放行」路径：提示缺失、环境非交互，模型就突然可以任意写入。失败关闭把这种情况变成一次拒绝。",
      how: "没有交互式回答者时 ask 返回 unavailable；never 自动拒绝；--yes 批准本进程内的一切。取消审批会立即记录 cancelled，并丢弃回答线程迟到的结果。决定事件携带 request id、工具名与 call id。",
      try: "minidsh --sandbox-mode workspace-write --approval-policy never run \"write x.txt: hi\"\nminidsh --sandbox-mode workspace-write run \"write y.txt: hi\"\nminidsh session <id>",
      observe: ["非交互运行产生 unavailable，而不是 allowed。", "--yes 产生 allowed-once，审计结构完全相同。", "approval/asked 与 approval/decided 永远成对出现。"],
      tradeoff: "审批是人工控制手段，不是边界：--yes 会整体关闭它，被攻陷的工具也能走同一代码路径。因此要和内核沙箱配合使用。"
    }
  },
  {
    id: "observations", n: "11", part: "safety",
    files: ["observations.py", "tools.py"],
    en: {
      title: "File observations and read-before-write",
      thesis: "A write is only valid against the version you read.",
      concept: "Each session records which files it has read and what version it saw. write and edit refuse to proceed unless a valid observation exists for that exact path and content version.",
      why: "Without versioning, an agent that read a file ten turns ago happily overwrites work someone else changed in between. That is not a model mistake; it is a missing invariant.",
      how: "Observations store a version identity per path and are persisted as fs/observed events, so resume restores them. An external change invalidates the observation and blocks the write. The edit flow performs read first, then edit, which is why the mock model needs two steps.",
      try: "minidsh --sandbox-mode workspace-write --yes run \"write note.txt: alpha\"\nminidsh --sandbox-mode workspace-write --yes run \"write note.txt: beta\"\nminidsh --sandbox-mode workspace-write --yes run \"edit note.txt: replace alpha with beta\"",
      observe: ["The second write is refused: this session never read the file.", "The edit intent reads first, then edits in the same turn.", "After resume, the observation is restored from the log."],
      tradeoff: "Version identity is content-derived, not a filesystem generation number, and there is no cross-process lock during the window between read and write.",
      industry: ["Optimistic concurrency", "Editor buffers and ETags solved this decades ago; agents need the same check.", "编辑器缓冲区和 ETag 几十年前就解决了这个问题，Agent 需要同样的检查。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "文件观察与先读后写",
      thesis: "写入只对「你读过的那个版本」有效。",
      concept: "每个会话记录自己读过哪些文件、看到的是什么版本。write 与 edit 只有在该路径存在有效观察且版本一致时才允许执行。",
      why: "没有版本信息，一个十回合前读过文件的 Agent 会心安理得地覆盖别人期间的修改。这不是模型的错误，而是一条缺失的不变量。",
      how: "观察按路径保存版本标识，并以 fs/observed 事件持久化，resume 时恢复。外部修改会让观察失效并阻止写入。edit 流程先 read 再 edit，这也是 Mock 模型需要两步的原因。",
      try: "minidsh --sandbox-mode workspace-write --yes run \"write note.txt: alpha\"\nminidsh --sandbox-mode workspace-write --yes run \"write note.txt: beta\"\nminidsh --sandbox-mode workspace-write --yes run \"edit note.txt: replace alpha with beta\"",
      observe: ["第二次写入被拒绝：这个会话从没读过该文件。", "edit 意图在同一回合内先读再改。", "resume 之后观察从日志恢复。"],
      tradeoff: "版本标识由内容派生，而不是文件系统代际号；读与写之间的窗口也没有跨进程锁。"
    }
  },
  {
    id: "seatbelt", n: "12", part: "safety",
    files: ["seatbelt.py"],
    en: {
      title: "Kernel sandbox",
      thesis: "Operation not permitted is a boundary; please do not write is a wish.",
      concept: "On macOS, Seatbelt profiles wrap bash, background jobs and the persistent PTY so the operating system enforces the same policy the harness advertises.",
      why: "Prompt-level rules and path checks both live in the same process as the model's tool calls. A tool that shells out is outside that logic; only the kernel can stop it.",
      how: "The service generates a profile from the session sandbox mode: read-only allows execution and reads but denies writes; workspace-write allows only the workspace, the managed jobs directory and platform temp directories; danger-full-access disables isolation. Refusals surface as real kernel errors from the child process.",
      try: "minidsh --enable core.kernel-sandbox run \"run printf denied > blocked.txt\"\nminidsh --enable core.kernel-sandbox --sandbox-mode workspace-write --yes run \"run printf ok > inside.txt\"",
      observe: ["The error text comes from /bin/bash, not from the harness.", "The workspace-write run succeeds inside the workspace.", "Sandbox mode cannot change while a persistent terminal is open."],
      tradeoff: "macOS only. No Linux Landlock or bubblewrap, no Windows equivalent, and no per-path allowlist beyond the three built-in directories.",
      industry: ["Defense in depth", "Sandboxing belongs at the kernel boundary, not in a prompt or a wrapper.", "沙箱应落在内核边界，而不是提示词或包装层。", "https://github.com/zeroclaw-labs/zeroclaw/blob/a56c345d51dd8ab562e9351e0d4ab83f6a741db9/docs/book/src/security/sandboxing.md"]
    },
    zh: {
      title: "内核沙箱",
      thesis: "Operation not permitted 是边界；「请不要写」只是愿望。",
      concept: "在 macOS 上，Seatbelt 配置包裹 bash、后台任务与持久 PTY，让操作系统强制执行 Harness 所声明的同一套策略。",
      why: "提示词规则与路径检查都和模型的工具调用处在同一进程里。一个会另起 Shell 的工具绕过了这些逻辑，只有内核能拦住它。",
      how: "服务按会话沙箱模式生成配置：read-only 允许执行与读取但禁止写入；workspace-write 只允许工作区、受管任务目录与平台临时目录；danger-full-access 关闭隔离。拒绝表现为子进程返回的真实内核错误。",
      try: "minidsh --enable core.kernel-sandbox run \"run printf denied > blocked.txt\"\nminidsh --enable core.kernel-sandbox --sandbox-mode workspace-write --yes run \"run printf ok > inside.txt\"",
      observe: ["错误文本来自 /bin/bash，而不是 Harness。", "workspace-write 运行在工作区内成功。", "持久终端打开时不能切换沙箱模式。"],
      tradeoff: "仅 macOS。没有 Linux Landlock/bubblewrap，没有 Windows 实现，除三个内置目录外也没有按路径的允许清单。"
    }
  },
  {
    id: "processes", n: "13", part: "safety",
    files: ["shell.py", "jobs.py", "pty_shell.py"],
    en: {
      title: "Processes, jobs and PTY",
      thesis: "A process is the most dangerous tool, so its lifecycle must be explicit.",
      concept: "bash runs one fresh bash -c per call. Background work becomes a job with its own output cursors. The persistent profile keeps one interactive PTY per session for stateful shells.",
      why: "Long-running commands are the norm in real work. Without job identity you cannot read partial output, cannot kill a runaway process group, and cannot tell a timeout from a failure.",
      how: "A foreground timeout moves the same job to the background instead of killing it. Jobs are per-session, capped at ten running or stopping, keep a 256 KiB memory ring that shrinks to 16 KiB after the first terminal read, and write full output to disk. Killing a job kills its process group. The PTY wraps commands in random markers and ends with an exit code line.",
      try: "minidsh --sandbox-mode workspace-write --yes run \"run in background sleep 10\"\nminidsh --sandbox-mode workspace-write --yes run \"jobs\"\nminidsh --enable tools.bash-persistent --sandbox-mode workspace-write --yes run \"run export DEMO=hello\"",
      observe: ["A background run returns a job id immediately.", "job_output consumes only new output; run it twice to see the difference.", "The persistent shell keeps cwd and exported variables between calls."],
      tradeoff: "Jobs are reclaimed when a short-lived CLI exits, and the PTY does not survive a restart. Unix only: no ConPTY, no cross-process terminal recovery.",
      industry: ["Durable execution", "Cursor-based output and process-group kill are the minimum viable job model.", "游标式输出与进程组终止是最小可用的任务模型。", "https://opentelemetry.io/docs/specs/semconv/gen-ai/"]
    },
    zh: {
      title: "进程、任务与 PTY",
      thesis: "进程是最危险的工具，所以它的生命周期必须显式。",
      concept: "bash 每次调用都起一个新的 bash -c。后台工作变成带独立输出游标的任务。持久 profile 为每个会话保留一个交互式 PTY，用来维持有状态的 Shell。",
      why: "真实工作里长命令是常态。没有任务标识，你读不到部分输出、杀不掉失控的进程组，也分不清超时和失败。",
      how: "前台等待超时会把同一个任务转入后台，而不是杀掉它。任务按会话隔离，最多十个运行中或停止中，内存环 256 KiB、首次终态读取后收缩到 16 KiB，完整输出落盘。终止任务会终止整个进程组。PTY 用随机标记包裹命令，并以退出码行收尾。",
      try: "minidsh --sandbox-mode workspace-write --yes run \"run in background sleep 10\"\nminidsh --sandbox-mode workspace-write --yes run \"jobs\"\nminidsh --enable tools.bash-persistent --sandbox-mode workspace-write --yes run \"run export DEMO=hello\"",
      observe: ["后台运行立刻返回任务 id。", "job_output 只消费新增输出；连续执行两次就能看出差别。", "持久 Shell 在调用之间保留 cwd 与导出的变量。"],
      tradeoff: "短生命周期的 CLI 退出时会回收任务，PTY 也不跨重启。仅 Unix：没有 ConPTY，也没有跨进程终端恢复。"
    }
  },
  {
    id: "credentials", n: "14", part: "safety",
    files: ["shell.py", "pty_shell.py", "mcp.py", "deepseek.py"],
    en: {
      title: "Credentials and environment scrubbing",
      thesis: "A secret that reaches a child process is already leaked.",
      concept: "Every child process gets a filtered environment: anything that looks like a key, token, secret or password is removed, along with DSH_* variables, before managed values are injected.",
      why: "An agent runs shell commands written by a model that has read untrusted content. If the environment contains the API key, a single env command exfiltrates it.",
      how: "Passwords and tokens are matched by name pattern, not by an allowlist, so new variables are covered by default. MCP credentials are resolved from ${credential:NAME} at call time and redacted from diagnostics. The model API key is read from the environment and never written to config or session files.",
      try: "DEEPSEEK_API_KEY=secret-demo minidsh --sandbox-mode workspace-write --yes run \"run env\"\nminidsh --enable core.mcp --config mcp.json tools",
      observe: ["env output contains no variable whose name matches KEY, TOKEN, SECRET or PASSWORD.", "Managed DSH_HOME and DSH_SESSION_ID are still present.", "MCP failures print [redacted] instead of the resolved credential."],
      tradeoff: "Name-based scrubbing can miss a secret with an innocent name, and it does not protect against a tool that reads a credentials file directly.",
      industry: ["Secret hygiene", "Filter by pattern, inject by allowlist, redact in errors, never persist.", "按模式过滤、按白名单注入、错误里脱敏、永不落盘。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "凭据与环境清洗",
      thesis: "一旦密钥进了子进程，它就已经泄露了。",
      concept: "每个子进程拿到的是过滤后的环境：名字像 key、token、secret、password 的变量连同 DSH_* 一起被移除，然后才注入受管变量。",
      why: "Agent 执行的是模型写的 Shell 命令，而这个模型读过不可信内容。如果环境里有 API Key，一条 env 就足以把它带走。",
      how: "密码与令牌按名字模式匹配，而不是靠白名单，这样新变量默认就被覆盖。MCP 凭据在调用时从 ${credential:NAME} 解析，并在诊断信息里脱敏。模型 API Key 只从环境读取，绝不写入配置或会话文件。",
      try: "DEEPSEEK_API_KEY=secret-demo minidsh --sandbox-mode workspace-write --yes run \"run env\"\nminidsh --enable core.mcp --config mcp.json tools",
      observe: ["env 输出里没有名字匹配 KEY、TOKEN、SECRET 或 PASSWORD 的变量。", "受管的 DSH_HOME 与 DSH_SESSION_ID 仍然存在。", "MCP 失败时打印 [redacted]，而不是解析后的凭据。"],
      tradeoff: "按名字清洗会漏掉名字无害的密钥，也挡不住直接读取凭据文件的工具。"
    }
  },

  {
    id: "session", n: "15", part: "memory",
    partNo: "PART IV",
    partTitle: { en: "Memory", zh: "记忆" },
    partDesc: { en: "Sessions, recovery, model view, compaction, replay.", zh: "会话、恢复、模型视图、压缩与回放。" },
    files: ["session.py", "generations.py"],
    en: {
      title: "Sessions as immutable generations",
      thesis: "Append facts, never edit them; publish a new snapshot instead.",
      concept: "A session is a directory with generations of JSONL plus an index.json manifest. Each append publishes a new generation containing the full event snapshot; older generations are read-only.",
      why: "Mutable logs make crash recovery guesswork and make fork, audit and diff impossible. Immutable generations make every state reconstructable and every corruption detectable.",
      how: "The manifest records the current generation, its relative path and a SHA-256. Publication goes through a temp file, fsync and os.replace, so a reader never sees a half-written manifest. Reads validate session identity, contiguous sequence numbers, event shape and UTF-8. A POSIX flock write lease covers the whole turn, and the kernel releases it if the process dies.",
      try: "minidsh sessions\nminidsh session <id>\nminidsh transcript <id>",
      observe: ["generations/ grows by one file per append; old files never change.", "index.json points at the newest generation and carries its hash.", "transcript is a human projection; session is the raw event list."],
      tradeoff: "Each append rewrites the whole snapshot, which is fine for a lab and wrong for a long session at scale. There is no Windows write lock.",
      industry: ["Event sourcing", "Append-only logs give replay, audit, fork and repair from one primitive.", "只追加日志用同一个原语同时实现重放、审计、分支与修复。", "https://opentelemetry.io/docs/specs/semconv/gen-ai/"]
    },
    zh: {
      title: "会话即不可变代际",
      thesis: "只追加事实，永不修改；用新快照发布状态。",
      concept: "一个会话是一个目录，包含多代 JSONL 与一份 index.json 清单。每次追加都会发布一个包含完整事件快照的新代际，旧代际只读。",
      why: "可变日志会让崩溃恢复变成猜测，也让分支、审计与对比无法进行。不可变代际让每个状态都能重建，每处损坏都能被检测。",
      how: "清单记录当前代际、相对路径与 SHA-256。发布经过临时文件、fsync 与 os.replace，读者永远看不到写了一半的清单。读取时校验会话身份、连续序号、事件形状与 UTF-8。POSIX flock 写租约覆盖整个回合，进程死亡时由内核释放。",
      try: "minidsh sessions\nminidsh session <id>\nminidsh transcript <id>",
      observe: ["每次追加 generations/ 多一个文件，旧文件永不改变。", "index.json 指向最新代际并记录其哈希。", "transcript 是面向人的投影；session 是原始事件列表。"],
      tradeoff: "每次追加都重写整份快照，这在实验室里没问题，在长会话里不可接受。没有 Windows 写锁。"
    }
  },
  {
    id: "recovery", n: "16", part: "memory",
    files: ["session.py", "session_recovery.py"],
    en: {
      title: "Resume, fork, repair and migration",
      thesis: "Recovery must not invent success.",
      concept: "resume continues a session, fork branches it, repair closes an interrupted turn honestly, and migrate upgrades an old format. Each one writes a content-hashed backup before publishing.",
      why: "A crash leaves a turn open and tool results missing. Retrying blindly can duplicate a side effect; pretending it succeeded corrupts the record. The only safe answer is to record uncertainty.",
      how: "A tool call that never started gets TOOL_NOT_STARTED; one that started without a durable result gets TOOL_OUTCOME_UNKNOWN with an explicit warning against blind retries. Open steps and turns are then closed with reason interrupted, while fork uses forked. Truncated tails are cut at the last complete newline only during repair or resume.",
      try: "minidsh repair <id>\nminidsh fork <id> --at-seq 12\nminidsh migrate <id>",
      observe: ["repair prints the backup path it wrote under .minidsh/sessions/backups.", "The parent session is byte-identical after a fork.", "A future-format session is refused, not downgraded."],
      tradeoff: "This is a learning v0/v1/v2 format. There is no upstream multi-generation directory layout and no Zstandard encoding.",
      industry: ["Crash consistency", "Recording uncertainty explicitly is safer than optimistic replay.", "显式记录不确定性，比乐观重放安全得多。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "恢复、分支、修复与迁移",
      thesis: "恢复不能凭空捏造成功。",
      concept: "resume 继续会话，fork 分支，repair 诚实地关闭中断的回合，migrate 升级旧格式。每一步都会先写出以内容哈希命名的备份。",
      why: "崩溃会留下未闭合的回合和缺失的工具结果。盲目重试可能重复副作用，假装成功则污染记录。唯一安全的答案是记录不确定性。",
      how: "从未开始的工具调用补 TOOL_NOT_STARTED；已开始但没有持久结果的补 TOOL_OUTCOME_UNKNOWN，并明确警告不要盲目重试。随后把开放的步骤与回合以 reason=interrupted 关闭，fork 则用 forked。被截断的尾部只在 repair 或 resume 时截到最后一个完整换行。",
      try: "minidsh repair <id>\nminidsh fork <id> --at-seq 12\nminidsh migrate <id>",
      observe: ["repair 会打印写入 .minidsh/sessions/backups 的备份路径。", "fork 之后父会话逐字节不变。", "未来版本的会话会被拒绝读取，而不是降级处理。"],
      tradeoff: "这是学习版 v0/v1/v2 格式：没有上游的多代际目录布局，也没有 Zstandard 编码。"
    }
  },
  {
    id: "surface", n: "17", part: "memory",
    files: ["surface.py", "pruner.py"],
    en: {
      title: "The surface: what the model currently sees",
      thesis: "Separate what happened from what is visible, and you unlock pruning without losing evidence.",
      concept: "The surface is a folded view over the event log. Ordinary events append; replacement events carry surfaceOp={op: replace, startSeq, endSeq} and shadow a range of earlier nodes.",
      why: "Pruning, masking and summarization all need to change history from the model's perspective. If the only way to change the view is to edit the log, every such feature destroys auditability.",
      how: "Folding validates that the range exists, is ordered, contains only current nodes, and that sourceEventSeqs is complete and free of duplicates. A tool-result replacement may change content only, never the tool name or call id, and the first system prompt can only be rewritten by a single system replacement.",
      try: "minidsh surface <id>\nminidsh --enable core.tool-result-pruner prune <id>\nminidsh transcript <id>",
      observe: ["surface shows the smaller model view; transcript still shows the original.", "Replacements carry sourceEventSeqs pointing at the shadowed nodes.", "An invalid range or a foreign field is refused."],
      tradeoff: "Only message-level and tool-result replacements are supported; there is no general plugin projection or full upstream event type system.",
      industry: ["Observation masking", "Mask tool output in the view, keep it in the log.", "在视图里遮蔽工具输出，在日志里保留原文。", "https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md"]
    },
    zh: {
      title: "Surface：模型此刻看到什么",
      thesis: "把「发生了什么」和「现在可见什么」分开，就能在不丢证据的前提下裁剪。",
      concept: "surface 是事件日志之上的折叠视图。普通事件追加；替换事件携带 surfaceOp={op: replace, startSeq, endSeq}，遮蔽前面一段节点。",
      why: "裁剪、遮蔽与摘要都需要从模型视角改变历史。如果改视图的唯一方式是改日志，这些功能每一次都会破坏可审计性。",
      how: "折叠会校验区间存在、有序、只包含当前节点，且 sourceEventSeqs 完整无重复。工具结果替换只能改 content，不能改工具名或 call id；首个系统提示词只能由单个 system 替换改写。",
      try: "minidsh surface <id>\nminidsh --enable core.tool-result-pruner prune <id>\nminidsh transcript <id>",
      observe: ["surface 显示收缩后的模型视图，transcript 仍显示原文。", "替换事件带 sourceEventSeqs 指向被遮蔽的节点。", "非法区间或越权字段会被拒绝。"],
      tradeoff: "只支持消息级与工具结果替换；没有通用插件投影，也没有上游完整的事件类型系统。"
    }
  },
  {
    id: "compaction", n: "18", part: "memory",
    files: ["compaction.py", "pruner.py"],
    en: {
      title: "Compaction and pruning",
      thesis: "Compaction changes the projection, never the facts.",
      concept: "When estimated context exceeds 80% of the window, the middle of the conversation is replaced by a structured summary and the tail is kept. Pruning truncates oversized tool output below a threshold.",
      why: "Context windows are finite and prices are linear in tokens. Doing nothing ends in an API error; deleting history ends in an unexplainable session.",
      how: "Compaction writes compaction/start and compaction/end and wraps the result in a compacted-summary block; the dry run uses the same prompt shape. Pruning keeps 4096 code points of head and 1024 of tail around an 8192 threshold, emits compaction/prune, then a replacement that only changes content. Token accounting is a deliberate estimate.",
      try: "minidsh --context-window 120 run \"read README.md\"\nminidsh compact <id>\nminidsh --enable core.tool-result-pruner prune <id>",
      observe: ["compaction/start and compaction/end bracket the rewrite.", "Original events remain; only the view shrinks.", "Pruning twice does not re-prune an already small node."],
      tradeoff: "The estimate is not a tokenizer, and there is no image token pricing or re-compaction on model switch.",
      industry: ["Context engineering", "Summarize the view, keep the log; measure before you cut.", "压缩视图、保留日志；先度量再裁剪。", "https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md"]
    },
    zh: {
      title: "上下文压缩与裁剪",
      thesis: "压缩改变投影，不改变事实。",
      concept: "当估算上下文超过窗口的 80% 时，用结构化摘要替换对话中段并保留尾部；裁剪则把超过阈值的工具输出截短。",
      why: "上下文窗口有限，费用与 token 线性相关。什么都不做会撞 API 错误，删除历史会让会话无法解释。",
      how: "压缩写入 compaction/start 与 compaction/end，并用 compacted-summary 包裹结果；试运行使用同一提示词形状。裁剪在 8192 阈值下保留头部 4096 码点与尾部 1024 码点，先发 compaction/prune，再发只改 content 的替换事件。Token 统计是刻意的估算。",
      try: "minidsh --context-window 120 run \"read README.md\"\nminidsh compact <id>\nminidsh --enable core.tool-result-pruner prune <id>",
      observe: ["compaction/start 与 compaction/end 成对包围改写。", "原始事件仍在，只有视图变小。", "重复裁剪不会再次裁剪已经足够小的节点。"],
      tradeoff: "估算不是 tokenizer，也没有图片 token 计价，切换模型时不会自动重新压缩。"
    }
  },
  {
    id: "stream-record", n: "19", part: "memory",
    files: ["stream_codec.py", "server.py"],
    en: {
      title: "Stream recording and SSE replay",
      thesis: "Two different recovery problems: rebuild the response, and rejoin the stream.",
      concept: "Recorded chunks compress raw model output into the assistant message payload. The SSE broker keeps a bounded in-process ring so a dropped client can resume with Last-Event-ID.",
      why: "Without recording, the raw stream is lost after the response is assembled. Without replay, any network hiccup means the UI silently stops mid-run.",
      how: "Adjacent text and reasoning chunks become arrays with a start time and deltas; tool-call chunks keep index, id, name and argument fragments; usage, block start and finish stay raw. Expansion validates lengths, timing and call identity, so a corrupted record fails the session read. The broker keeps 2048 events and replays only events newer than the breakpoint for the same session.",
      try: "minidsh stream <id>\ncurl -H 'Last-Event-ID: 42' 'http://127.0.0.1:3080/v1/runs/stream?session_id=<id>'",
      observe: ["stream prints expanded chunks with seq, turn and step.", "Replay never restarts the agent; it only rejoins.", "A too-old or future event id is rejected explicitly."],
      tradeoff: "The ring is per process, so a restart loses replay history. Recorded chunks are not global SSE sequence numbers.",
      industry: ["Resumable streams", "Resume by identity, not by re-running the work.", "按标识续传，而不是重跑工作。", "https://opentelemetry.io/docs/specs/semconv/gen-ai/"]
    },
    zh: {
      title: "流记录与 SSE 续传",
      thesis: "这是两个不同的恢复问题：重建响应，和重新接入流。",
      concept: "记录分片把原始模型输出压缩进 assistant 消息载荷；SSE broker 维护一个有界的进程内事件环，掉线客户端可以用 Last-Event-ID 续传。",
      why: "没有记录，响应组装完成后原始流就丢了。没有续传，一次网络抖动就会让界面在运行中途静默停止。",
      how: "相邻的文本与思考分片合并为带起始时间与时间差的数组；工具调用分片保留 index、id、name 与参数片段；usage、block 开始与结束保持原始。展开时会校验长度、时间与调用身份，损坏记录会让会话读取失败。broker 保留 2048 条事件，只回放同一会话中比断点更新的事件。",
      try: "minidsh stream <id>\ncurl -H 'Last-Event-ID: 42' 'http://127.0.0.1:3080/v1/runs/stream?session_id=<id>'",
      observe: ["stream 打印展开后的分片，并附 seq、turn、step。", "回放不会重启 Agent，只是重新接入。", "过旧或未来的事件 id 会被明确拒绝。"],
      tradeoff: "事件环只在进程内，重启即丢；记录的分片也不是全局 SSE 序号。"
    }
  },
  {
    id: "subagents", n: "20", part: "collab",
    partNo: "PART V",
    partTitle: { en: "Collaboration", zh: "协作与生态" },
    partDesc: { en: "Delegation, orchestration, MCP, skills, schedules, goals, code execution, presets.", zh: "委派、编排、MCP、技能、定时、目标、代码执行与预设。" },
    files: ["subagents.py"],
    en: {
      title: "Subagents: context isolation first",
      thesis: "The point of delegation is that the parent does not have to read the child's work.",
      concept: "subagent spawns a child agent with its own session and name. subagent_fork does the same but inherits the parent's history up to the last complete turn.",
      why: "Parallelism is the obvious benefit, but the durable one is isolation: ten files of exploration never enter the parent's context, so the parent's window stays clean.",
      how: "The parent receives a subagent-settled user message with the child's result and consumes it in a later step. Control tools send_message, interrupt_agent, list_agents and wait_agent manage the tree. Depth defaults to one, names are unique per parent, and children inherit the parent preset.",
      try: "minidsh --enable core.subagents --enable tools.subagent --enable tools.subagent-control run \"delegate audit | read README.md\"\nminidsh --enable core.subagents --enable tools.subagent-control run \"agents\"",
      observe: ["The tool returns agent-1 immediately, not the finished answer.", "The parent sees a summary, not the child transcript.", "A child at max depth cannot delegate further."],
      tradeoff: "In-process only: no ACP, Codex, Claude Code or remote SDK backend, and no per-child model selection. A child cwd is recorded but file tools still use the root workspace.",
      industry: ["Context isolation", "Delegation is a context budget technique before it is a speed technique.", "委派首先是一种上下文预算技术，其次才是提速手段。", "https://opentelemetry.io/docs/specs/semconv/gen-ai/"]
    },
    zh: {
      title: "子代理：首先是上下文隔离",
      thesis: "委派的意义在于父级不必读子级的工作过程。",
      concept: "subagent 启动一个拥有独立会话与名称的子代理；subagent_fork 相同，但会继承父级直到最后一个完整回合的历史。",
      why: "并行是显而易见的好处，但真正持久的好处是隔离：探索十个文件的过程永远不会进入父级上下文，父级窗口因此保持干净。",
      how: "父级收到一条带子级结果的 subagent-settled 用户消息，并在后续步骤消费它。控制工具 send_message、interrupt_agent、list_agents、wait_agent 管理整棵树。深度默认 1，名称在每个父级下唯一，子级继承父级预设。",
      try: "minidsh --enable core.subagents --enable tools.subagent --enable tools.subagent-control run \"delegate audit | read README.md\"\nminidsh --enable core.subagents --enable tools.subagent-control run \"agents\"",
      observe: ["工具立刻返回 agent-1，而不是最终答案。", "父级看到的是摘要，不是子级 transcript。", "达到最大深度的子级无法继续委派。"],
      tradeoff: "仅进程内：没有 ACP、Codex、Claude Code 或远程 SDK 后端，也没有按子级选择模型。子级 cwd 会被记录，但文件工具仍使用根工作区。"
    }
  },
  {
    id: "workflow", n: "21", part: "collab",
    files: ["workflow.py", "workflow_guest.js"],
    en: {
      title: "Workflow: orchestration as code",
      thesis: "Use a script when the control graph is known; use an agent when it is not.",
      concept: "A workflow runs a JavaScript async body in an isolated Node process. The script calls agent, pipeline, parallel, phase and log; each agent call becomes a real subagent through JSON-RPC back into Python.",
      why: "Multi-agent fan-out expressed as model turns is nondeterministic and expensive to debug. Expressed as code it is reviewable, testable and bounded.",
      how: "The guest has no require, process, fs, net or timers: meta and args are injected as JSON data and never evaluated. A child failure or schema mismatch becomes null, while a hook misuse aborts the whole run. Limits are 1000 agents, min(32, cpu) concurrent, 4096 items per combinator. Background mode reuses the jobs pipeline.",
      try: "minidsh --enable core.subagents --enable core.workflow --enable tools.workflow run \"workflow alpha|beta\"\nminidsh --enable core.subagents --enable core.workflow --enable tools.workflow run \"workflow background alpha|beta\"",
      observe: ["workflow/phase, agent-start, agent-end and end events bracket the run.", "A schema mismatch yields null instead of a crash.", "Background runs return a job id usable with job_output."],
      tradeoff: "The vm is a learning boundary, not a hardened sandbox. No model routing per agent, and a host restart marks unsettled runs as interrupted instead of resuming them.",
      industry: ["Deterministic orchestration", "Code-shaped control flow beats model-shaped control flow when the graph is known.", "控制图已知时，代码形态的控制流优于模型形态。", "https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md"]
    },
    zh: {
      title: "Workflow：把编排写成代码",
      thesis: "控制图已知时用脚本，未知时用 Agent。",
      concept: "workflow 在隔离的 Node 进程里执行一段 JavaScript async body。脚本调用 agent、pipeline、parallel、phase 与 log；每次 agent 调用都通过 JSON-RPC 回到 Python，变成真正的子代理。",
      why: "用模型回合表达多代理扇出既不确定又难调试；用代码表达则可评审、可测试、有边界。",
      how: "guest 里没有 require、process、fs、net 与计时器：meta 与 args 作为 JSON 数据注入，绝不被求值。子级失败或 schema 不匹配返回 null，而 hook 误用会中止整次运行。限制为 1000 个代理、min(32, cpu) 并发、每个组合器 4096 项。后台模式复用任务管线。",
      try: "minidsh --enable core.subagents --enable core.workflow --enable tools.workflow run \"workflow alpha|beta\"\nminidsh --enable core.subagents --enable core.workflow --enable tools.workflow run \"workflow background alpha|beta\"",
      observe: ["workflow/phase、agent-start、agent-end 与 end 事件包围整次运行。", "schema 不匹配返回 null，而不是崩溃。", "后台运行返回可用 job_output 读取的任务 id。"],
      tradeoff: "vm 是学习用的边界，不是加固沙箱。没有按代理的模型路由；宿主重启会把未结算的运行标记为中断，而不是恢复。"
    }
  },
  {
    id: "mcp", n: "22", part: "collab",
    files: ["mcp.py"],
    en: {
      title: "MCP client",
      thesis: "Third-party tools are a trust boundary, a naming problem and a cancellation problem at once.",
      concept: "The MCP client speaks JSON-RPC to stdio and Streamable HTTP servers, discovers their tools with pagination, and registers them as mcp__<server>__<tool>.",
      why: "Every server brings its own credentials, its own failures and its own idea of names. Without namespacing and fail-closed discovery, one bad server can shadow built-in tools.",
      how: "The client performs a real initialize handshake, scrubs the child environment, resolves ${credential:NAME} from the host, and redacts resolved values from diagnostics. A name conflict rejects the entire new tool generation while the old one keeps working. Server isError becomes an explicit tool failure, and resource blobs report only their base64 length.",
      try: "minidsh --config mcp.json tools\ncurl http://127.0.0.1:3080/v1/mcp",
      observe: ["Tools appear as mcp__server__tool, never as bare names.", "With no server configured, no MCP tool and no MCP prompt section exist.", "A duplicate tool name fails the whole generation instead of half-registering."],
      tradeoff: "The implementation follows the handshake protocol. The 2026-07-28 MCP revision removes initialize, sessions and SSE resumability, and replaces them with server/discover and _meta version fields: a known gap, documented rather than hidden.",
      industry: ["Model Context Protocol", "Namespace, redact, time out, and fail closed on discovery.", "命名空间、脱敏、超时，发现失败即关闭。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "MCP 客户端",
      thesis: "第三方工具同时是一个信任边界、一个命名问题和一个取消问题。",
      concept: "MCP 客户端用 JSON-RPC 与 stdio、Streamable HTTP 服务器通信，分页发现工具，并以 mcp__<server>__<tool> 注册。",
      why: "每个服务器都带来自己的凭据、自己的失败方式和自己的命名习惯。没有命名空间与失败关闭，一个坏服务器就能遮蔽内置工具。",
      how: "客户端执行真实的 initialize 握手，清洗子进程环境，从宿主解析 ${credential:NAME}，并在诊断信息里脱敏。命名冲突会拒绝整个新工具代际，旧工具继续可用。服务器的 isError 变成显式工具失败，资源 blob 只报告 base64 长度。",
      try: "minidsh --config mcp.json tools\ncurl http://127.0.0.1:3080/v1/mcp",
      observe: ["工具以 mcp__server__tool 形式出现，绝不是裸名字。", "未配置服务器时，没有 MCP 工具也没有 MCP 提示词段落。", "重名会让整代注册失败，而不是注册一半。"],
      tradeoff: "实现的是握手协议。2026-07-28 的 MCP 修订移除了 initialize、会话与 SSE 续传，改为 server/discover 与 _meta 版本字段：这是记录在案的已知缺口，而不是藏起来的缺失。"
    }
  },
  {
    id: "skills", n: "23", part: "collab",
    files: ["skills.py"],
    en: {
      title: "Skills and progressive disclosure",
      thesis: "Advertise cheaply, load expensively, and only on demand.",
      concept: "A skill is a directory with SKILL.md or a flat Markdown file with frontmatter. The model sees a catalog of names and one-line descriptions; the full body loads only when the skill tool is called.",
      why: "Loading every instruction pack upfront spends the context budget on capabilities the task will never use. Progressive disclosure turns a fixed cost into a per-task cost.",
      how: "Roots are resolved in order: project .dsh/skills, .agents/skills, configured dirs, ~/.dsh/skills, ~/.agents/skills, plus an optional bundled root. The catalog is a persistent user message; changes append a replacement catalog with a digest, and removing all skills explicitly retires old names. disable-model-invocation hides the catalog entry but still allows an explicit user gesture.",
      try: "mkdir -p skills/review && printf -- '---\\nname: review\\ndescription: Review a diff\\n---\\nRead the diff first.\\n' > skills/review/SKILL.md\nminidsh --enable core.skills --enable tools.skill skills",
      observe: ["skills lists name and description only.", "The tool result is wrapped in skill_content.", "A skill with disable-model-invocation is absent from the catalog but loadable by name."],
      tradeoff: "Frontmatter is a scalar parser, not full YAML. No file watching, no bundled office skills, no upstream working-directory scope chain.",
      industry: ["Agent Skills", "Progressive disclosure is the difference between ten skills and ten contexts.", "渐进式披露决定了十个技能是十行目录还是十份上下文。", "https://agentskills.io/specification"]
    },
    zh: {
      title: "技能与渐进披露",
      thesis: "用最低成本公布，用最高成本加载，而且只在需要时加载。",
      concept: "一个技能就是带 SKILL.md 的目录，或带 frontmatter 的平铺 Markdown。模型先看到名称与一句话描述组成的目录，只有调用 skill 工具时才加载完整正文。",
      why: "把所有指令包一次性加载，会把上下文预算花在任务根本用不到的能力上。渐进披露把固定成本变成按任务成本。",
      how: "根目录按顺序解析：项目的 .dsh/skills、.agents/skills、配置目录、~/.dsh/skills、~/.agents/skills，以及可选的 bundled 根。目录是一条持久用户消息；变化时追加带摘要的替换目录，全部删除时明确停用旧名字。disable-model-invocation 会隐藏目录项，但仍允许用户显式手势加载。",
      try: "mkdir -p skills/review && printf -- '---\\nname: review\\ndescription: Review a diff\\n---\\nRead the diff first.\\n' > skills/review/SKILL.md\nminidsh --enable core.skills --enable tools.skill skills",
      observe: ["skills 只列出名称与描述。", "工具结果被包在 skill_content 里。", "标记 disable-model-invocation 的技能不在目录中，但可以按名字加载。"],
      tradeoff: "frontmatter 是标量解析器，不是完整 YAML。没有文件监听、没有内置办公技能、也没有上游的工作目录作用域链。"
    }
  },
  {
    id: "schedule", n: "24", part: "collab",
    files: ["schedule.py", "schedule_time.py"],
    en: {
      title: "Scheduled delivery",
      thesis: "Delivered is not done; a receipt only proves persistence.",
      concept: "A schedule belongs to the host, not to a session. It delivers a prompt at a time described by after, at, every, daily, weekly, cron or rrule, into this session, a reused session, or a fresh one.",
      why: "Timers look trivial until you need catch-up semantics, idempotency and an honest answer to whether the work actually happened.",
      how: "Tasks are stored atomically in .minidsh/schedule.json (v2). After downtime only the most recent missed occurrence is delivered. Every delivery writes a schedule/inbox receipt, which confirms the message was persisted and nothing more. History keeps 200 deliveries over 30 days with a 60-second minimum interval.",
      try: "minidsh --enable core.schedule --enable tools.schedule run 'schedule create {\"title\":\"Check\",\"prompt\":\"Check status\",\"rrule\":\"DTSTART:20990101T090000Z\\nRRULE:FREQ=DAILY;COUNT=1\",\"target\":\"this_session\"}'\nminidsh --enable core.schedule --enable tools.schedule run \"schedule list\"",
      observe: ["A receipt appears even when the target session never runs.", "Restarting after downtime delivers one occurrence, not a backlog.", "The minimum interval rejects a too-frequent rule."],
      tradeoff: "A subset of RRULE: no BYWEEKNO or BYYEARDAY, single-value BYHOUR/BYMINUTE/BYSECOND, and one storage backend.",
      industry: ["Durable scheduling", "Catch-up policy and the delivered/finished distinction are the design.", "追赶策略与「已投递/已完成」的区分才是设计重点。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "定时投递",
      thesis: "已投递不等于已完成；回执只能证明消息被持久化。",
      concept: "定时任务属于宿主而不是会话。它按 after、at、every、daily、weekly、cron 或 rrule 描述的时间，把一段提示投递到当前会话、复用会话或全新会话。",
      why: "定时器看起来很简单，直到你需要追赶语义、幂等性，以及一个关于「事情到底做没做」的诚实答案。",
      how: "任务原子写入 .minidsh/schedule.json（v2）。停机后只补投最近一次错过的时间点。每次投递写一条 schedule/inbox 回执，它只确认消息已持久化。历史保留 200 条、30 天，最小投递间隔 60 秒。",
      try: "minidsh --enable core.schedule --enable tools.schedule run 'schedule create {\"title\":\"Check\",\"prompt\":\"Check status\",\"rrule\":\"DTSTART:20990101T090000Z\\nRRULE:FREQ=DAILY;COUNT=1\",\"target\":\"this_session\"}'\nminidsh --enable core.schedule --enable tools.schedule run \"schedule list\"",
      observe: ["即使目标会话从未运行，回执也会出现。", "停机后重启只补投一次，而不是补一堆积压。", "过密的规则会被最小间隔拒绝。"],
      tradeoff: "RRULE 只是一个子集：没有 BYWEEKNO、BYYEARDAY，BYHOUR/BYMINUTE/BYSECOND 只接受单值，且只有一种存储后端。"
    }
  },
  {
    id: "goals", n: "25", part: "collab",
    files: ["goals.py"],
    en: {
      title: "Persistent goals and automatic rounds",
      thesis: "Long-horizon autonomy needs explicit state, a budget, and a human re-arm step.",
      concept: "A goal is an objective attached to a session with status, revision and round count. Optionally, an armed goal continues the same session when the agent goes idle.",
      why: "Keep going until done without state or budget is how loops run forever and how a model silently redefines done.",
      how: "Every update requires an exact goal id and revision, so concurrent edits fail instead of overwriting. Only a kind=goal user message consumes the round budget; exhaustion records a round-limit blocker. Restore, fork or restart always leaves the goal disarmed. Autonomous blocked requires three consecutive rounds and a concrete reason.",
      try: "minidsh --enable core.goals --enable tools.goal run \"goal create finish migration|4\"\nminidsh --enable core.goals goal <id>\nminidsh --enable core.goals goal <id> resume",
      observe: ["goal/change carries objective, status, revision and round count.", "Pause is user-only; the model cannot resume itself.", "A restart shows the goal disarmed, not armed."],
      tradeoff: "No completion evaluator, no subtree idle aggregation, no independent scheduler: the driver only continues an idle active goal.",
      industry: ["Long-horizon autonomy", "Optimistic concurrency plus a round budget is the minimum guardrail.", "乐观并发加回合预算，是最低限度的护栏。", "https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md"]
    },
    zh: {
      title: "持久目标与自动回合",
      thesis: "长程自主需要显式状态、预算，以及人类重新授权的步骤。",
      concept: "目标是一条挂在会话上的意图，带状态、revision 与回合计数。可选地，已武装的目标会在 Agent 空闲时继续同一会话。",
      why: "「一直干到完成为止」如果没有状态与预算，就是死循环的来源，也是模型悄悄改写「完成」定义的来源。",
      how: "每次更新都要求精确的 goal id 与 revision，因此并发编辑会失败而不是互相覆盖。只有 kind=goal 的用户消息才消耗回合预算，耗尽会记录 round-limit 阻塞。恢复、分支或重启后目标始终是未武装状态。自主 blocked 需要连续三个回合并给出具体原因。",
      try: "minidsh --enable core.goals --enable tools.goal run \"goal create finish migration|4\"\nminidsh --enable core.goals goal <id>\nminidsh --enable core.goals goal <id> resume",
      observe: ["goal/change 携带 objective、status、revision 与回合数。", "暂停只能由用户解除，模型无法自行恢复。", "重启后目标是未武装状态，而不是继续运行。"],
      tradeoff: "没有完成度评估器、没有子树空闲汇总、也没有独立调度器：驱动器只会让空闲的活动目标继续。"
    }
  },
  {
    id: "ptc", n: "26", part: "collab",
    files: ["ptc.py", "ptc_runner.js"],
    en: {
      title: "Programmatic tool calling",
      thesis: "Code is a better action space than tool JSON, and a much larger blast radius.",
      concept: "In PTC mode the model sees only run_code. Every other tool becomes await tools.name(args) inside a TypeScript program executed in Node.",
      why: "Ten tool turns can collapse into one code turn, which cuts latency and context churn. But the same mechanism lets one call loop, fan out, or write files in ways a single tool schema cannot express.",
      how: "per-call runs an async function body in a fresh process; persistent keeps a Node REPL with per-session top-level state. Calls still go through the same registry, so approval, cancellation and events apply. Read-only subcalls run in parallel up to ten, mutating calls are exclusive, and quotas cover pending calls, output size, timeout and subcalls per minute.",
      try: "minidsh --enable core.ptc --enable tools.run-code run 'run code {\"code\":\"const t = await tools.read({file_path:\\\"README.md\\\"}); return t.length\",\"description\":\"Measure\"}'",
      observe: ["Only run_code appears in the schema.", "Approval still fires for a write performed inside code.", "A timeout returns a classified error, not a traceback."],
      tradeoff: "No Acorn source transform, no full cooperative revoke or process-tree cleanup, no image projection. `sandbox_permissions` escalation is not implemented.",
      industry: ["Code as action space", "Quotas and isolation are part of the feature, not an afterthought.", "配额与隔离是功能的一部分，而不是事后补丁。", "https://github.com/Xwh630/ai-agent-handbook/blob/3ee41090a6bd12d7bcd851f2ff468e1e3c6991fc/en/chapters/25-harness-engineering.md"]
    },
    zh: {
      title: "程序化工具调用",
      thesis: "代码是比工具 JSON 更好的动作空间，同时也是大得多的爆炸半径。",
      concept: "PTC 模式里模型只看到 run_code，其他工具都变成 Node 中 TypeScript 程序里的 await tools.name(args)。",
      why: "十个工具回合可以折叠成一个代码回合，延迟与上下文抖动都大幅下降。但同一机制也让一次调用可以循环、扇出或写文件，这是单个工具 schema 表达不了的。",
      how: "per-call 在全新进程里执行 async 函数体；persistent 保留带会话级顶层状态的 Node REPL。调用仍走同一个注册表，因此审批、取消与事件照常生效。只读子调用最多并行十个，变更类调用独占，配额覆盖待处理数、输出大小、超时与每分钟子调用数。",
      try: "minidsh --enable core.ptc --enable tools.run-code run 'run code {\"code\":\"const t = await tools.read({file_path:\\\"README.md\\\"}); return t.length\",\"description\":\"Measure\"}'",
      observe: ["schema 里只剩 run_code。", "代码内执行的写入仍会触发审批。", "超时返回分类错误，而不是 traceback。"],
      tradeoff: "没有 Acorn 源码变换、没有完整的协作式撤销与进程树清理、没有图片投影；`sandbox_permissions` 提权也未实现。"
    }
  },
  {
    id: "presets", n: "27", part: "collab",
    files: ["presets.py"],
    en: {
      title: "Agent presets",
      thesis: "Capability scoping is a product feature, not an optimization.",
      concept: "A preset binds a session to a set of plugins and an optional persona: a prefix, a suffix, complete mode, and whether runtime context is included.",
      why: "A reviewer agent with a shell is not a reviewer. Scoping by session keeps the tool schema honest and the prompt small.",
      how: "The selection is persisted as preset/selected; a missing preset in history fails closed rather than falling back. Switching is allowed only before the first model turn. Forks and subagents inherit the parent preset. Because PTC uses the same scoped service context, removing a tool also removes its run_code binding.",
      try: "minidsh --enable core.agent-presets presets\nminidsh --enable core.agent-presets preset <id> minimal\nminidsh --enable core.agent-presets tools",
      observe: ["A minimal preset exposes no tools at all.", "Switching after the first turn is refused.", "A persona with includeRuntimeContext=false omits the environment snapshot."],
      tradeoff: "A name-to-plugin mapping, not an upstream YAML loader with hot reload or activation auditing.",
      industry: ["Least privilege", "Give each agent only the capabilities its job needs.", "只给每个 Agent 它工作所需的能力。", "https://agentskills.io/specification"]
    },
    zh: {
      title: "Agent 预设",
      thesis: "能力收窄是产品功能，不是性能优化。",
      concept: "预设把会话绑定到一组插件和一个可选 persona：前缀、后缀、complete 模式，以及是否附带 runtime context。",
      why: "一个带 Shell 的评审 Agent 就不是评审 Agent。按会话收窄能力，能让工具 schema 诚实、提示词更小。",
      how: "选择以 preset/selected 持久化；历史里缺失的预设在读取时失败关闭，而不是回退到别的组合。只允许在首个模型回合之前切换。分支与子代理继承父级预设。由于 PTC 使用同一个作用域服务上下文，删掉工具的同时也删掉了它的 run_code 绑定。",
      try: "minidsh --enable core.agent-presets presets\nminidsh --enable core.agent-presets preset <id> minimal\nminidsh --enable core.agent-presets tools",
      observe: ["minimal 预设完全不暴露工具。", "首个回合之后再切换会被拒绝。", "includeRuntimeContext=false 的 persona 不会附带环境快照。"],
      tradeoff: "只是名字到插件的映射，不是上游带热重载与激活审计的 YAML 加载器。"
    }
  },

  {
    id: "http-host", n: "28", part: "product",
    partNo: "PART VI",
    partTitle: { en: "Product", zh: "产品" },
    partDesc: { en: "HTTP host, workbench, distributions, and the real model adapter.", zh: "HTTP 宿主、工作台、发行包与真实模型适配器。" },
    files: ["server.py", "cli.py"],
    en: {
      title: "HTTP host, SSE and backpressure",
      thesis: "An HTTP harness is mostly lifecycle and backpressure; the prompt is the easy part.",
      concept: "A local HTTP host exposes runs, sessions, jobs, questions, schedule, MCP and skills. Runs stream over SSE with a run_id, and each session accepts one write turn at a time.",
      why: "Concurrent runs that share a session corrupt each other, and a slow browser tab must never be able to stall the agent. Both are solved by explicit identity and bounded queues.",
      how: "The host binds 127.0.0.1 by default and refuses a non-loopback bind without MINIDSH_TOKEN. Every SSE listener has a 256-event queue; overflow closes that listener and increments dropped_streams while the run continues. Each event carries the run id, so a client filters by identity instead of guessing. The broker keeps 2048 events for replay.",
      try: "minidsh serve --port 3080\ncurl -s http://127.0.0.1:3080/health\ncurl -N \"http://127.0.0.1:3080/v1/runs/stream?prompt=list\"",
      observe: ["A second concurrent run on the same session is rejected.", "A dropped listener does not affect the persisted result.", "health, plugins, tools and prompt all answer over HTTP."],
      tradeoff: "A learning API, not a multi-tenant gateway: no authentication beyond a bearer token, no rate limiting, no TLS, and no browser-supplied custom headers in the UI.",
      industry: ["Agent serving", "Identity correlation and bounded queues are the difference between a demo server and a usable one.", "身份关联与有界队列，决定了它是演示服务还是可用服务。", "https://opentelemetry.io/docs/specs/semconv/gen-ai/"]
    },
    zh: {
      title: "HTTP 宿主、SSE 与背压",
      thesis: "HTTP Harness 主要是生命周期与背压问题，提示词反而是最简单的部分。",
      concept: "本地 HTTP 宿主暴露运行、会话、任务、提问、定时、MCP 与技能。运行通过带 run_id 的 SSE 流式输出，同一会话一次只接受一个写回合。",
      why: "共享会话的并发运行会互相破坏；一个卡住的浏览器标签页绝不能拖住 Agent。两者都靠显式身份与有界队列解决。",
      how: "默认绑定 127.0.0.1；绑定非回环地址必须提供 MINIDSH_TOKEN，否则拒绝启动。每个 SSE 监听器有 256 条事件队列，溢出会关闭该监听器并累加 dropped_streams，运行本身继续。每个事件携带 run id，客户端按身份过滤而不是靠猜。broker 保留 2048 条事件用于回放。",
      try: "minidsh serve --port 3080\ncurl -s http://127.0.0.1:3080/health\ncurl -N \"http://127.0.0.1:3080/v1/runs/stream?prompt=list\"",
      observe: ["同一会话的第二个并发运行会被拒绝。", "监听器被丢弃不影响已持久化的结果。", "health、plugins、tools、prompt 都能通过 HTTP 访问。"],
      tradeoff: "这是学习用 API，不是多租户网关：除了 bearer token 没有认证、没有限流、没有 TLS，界面也不支持自定义请求头。"
    }
  },
  {
    id: "workbench", n: "29", part: "product",
    files: ["web/index.html"],
    en: {
      title: "The web workbench",
      thesis: "A thin client proves the API is complete; a private shortcut proves it is not.",
      concept: "One dependency-free HTML file served by the harness itself: session list, prompt composer, structured trace, live streaming text, pending questions, and compact/prune/fork actions.",
      why: "If the UI needs a private endpoint to look right, the public API is missing something. Keeping the client thin keeps the API honest.",
      how: "The page uses EventSource for live events and plain fetch for everything else. Events are categorised by prefix into model, tool, job and error, and payloads expand in place. Choosing a session loads its history read-only, and using it for the next run is a separate explicit action, so viewing never silently resumes.",
      try: "minidsh serve --port 3080\nopen http://127.0.0.1:3080\n# append ?lang=zh&theme=dark to deep-link language and theme",
      observe: ["Trace rows carry category colours and timestamped payloads.", "The language and theme switches persist in localStorage.", "The layout has no horizontal overflow down to 430px."],
      tradeoff: "No custom authorization header, no image upload widget, no virtualized list: a very long trace is rendered in full.",
      industry: ["Agent UX", "Observability is a user interface problem, not just a logging problem.", "可观测性也是界面问题，不只是日志问题。", "https://agents.md"]
    },
    zh: {
      title: "Web 工作台",
      thesis: "轻客户端能证明 API 是完整的；需要私有捷径就说明它不是。",
      concept: "一个零依赖的 HTML 文件由 Harness 自己提供：会话列表、提示词编辑、结构化轨迹、实时流式文本、待答问题，以及压缩/裁剪/分支操作。",
      why: "如果界面要靠私有接口才好看，说明公开 API 缺了东西。保持客户端轻薄，才能让 API 保持诚实。",
      how: "页面用 EventSource 接收实时事件，其余一律走普通 fetch。事件按前缀分为模型、工具、任务与错误四类，载荷可就地展开。选择会话只是只读加载历史，「用于下次运行」是另一个显式动作，因此查看不会悄悄恢复会话。",
      try: "minidsh serve --port 3080\nopen http://127.0.0.1:3080\n# 在地址后加 ?lang=zh&theme=dark 可深链语言与主题",
      observe: ["轨迹行带分类颜色与带时间戳的载荷。", "语言与主题切换保存在 localStorage。", "布局在 430px 宽度下没有横向溢出。"],
      tradeoff: "不支持自定义认证头、没有图片上传控件、没有虚拟滚动：很长的轨迹会一次性渲染。"
    }
  },
  {
    id: "distributions", n: "30", part: "product",
    files: ["packages/", "discovery.py", "examples/minidsh-clock"],
    en: {
      title: "Distributions and external plugins",
      thesis: "Optional heavy dependencies belong at the product edge, and installing is not the same as enabling.",
      concept: "The core ships as minidsh and minidsh-headless; minidsh-web adds the serve default; minidsh-desktop adds pywebview. External plugins are discovered through the minidsh.plugins entry point group.",
      why: "A harness that needs a browser toolkit to run a Bash command is a packaging failure. And a plugin that runs just because it was installed is a supply-chain incident waiting to happen.",
      how: "Core imports never touch pywebview; only the Desktop command reports a missing GUI dependency. External plugins are registered as placeholders while disabled, so their metadata is visible but the module is never imported. The entry point name must match plugin.name and must not shadow a built-in feature.",
      try: "python3 -m pip install -e . -e ./examples/minidsh-clock\nminidsh plugins\nminidsh --enable example.clock tools",
      observe: ["An installed plugin shows as disabled until explicitly enabled.", "Importing the CLI never imports pywebview.", "Uninstalling a package without removing its flag fails at startup."],
      tradeoff: "No hot reload, no versioned plugin API, no sandbox for plugin code: enabling a plugin grants it the host process's privileges.",
      industry: ["Extension model", "Discover lazily, enable explicitly, and keep the kernel free of optional SDKs.", "惰性发现、显式启用，内核不携带可选 SDK。", "https://agents.md"]
    },
    zh: {
      title: "发行包与外部插件",
      thesis: "可选的重依赖应放在产品边缘；装上不等于启用。",
      concept: "核心以 minidsh 与 minidsh-headless 发布；minidsh-web 增加 serve 默认命令；minidsh-desktop 增加 pywebview。外部插件通过 minidsh.plugins 入口点组发现。",
      why: "一个跑 Bash 命令都需要浏览器工具包的 Harness 是打包失败。而一个装上就自动运行的插件，则是一场迟早发生的供应链事故。",
      how: "核心导入永远不碰 pywebview，只有 Desktop 命令会报告缺失的 GUI 依赖。外部插件在禁用时注册为占位对象，元数据可见但模块永不导入。入口点名称必须与 plugin.name 一致，且不得遮蔽内置功能。",
      try: "python3 -m pip install -e . -e ./examples/minidsh-clock\nminidsh plugins\nminidsh --enable example.clock tools",
      observe: ["已安装但未启用的插件显示为 disabled。", "导入 CLI 永远不会导入 pywebview。", "卸载包但没删开关，会在启动时失败。"],
      tradeoff: "没有热重载、没有版本化插件 API、插件代码也没有沙箱：启用插件等于赋予它宿主进程的全部权限。"
    }
  },
  {
    id: "deepseek", n: "31", part: "product",
    files: ["deepseek.py", "retry.py", "images.py", "files.py"],
    en: {
      title: "The real model adapter",
      thesis: "Provider adapters are protocol accounting: block order, signatures, usage, and error taxonomy.",
      concept: "The DeepSeek adapter implements the Messages protocol over SSE: text, reasoning with replay signatures, tool calls with JSON Schema, images, usage, and stop reasons.",
      why: "Most first-turn-works, resume-breaks bugs live here. If block order or signature replay is wrong, a resumed session either loses its reasoning or is rejected by the API.",
      how: "Requests go to /v1/messages on the configured base URL with x-api-key and anthropic-version. Defaults are deepseek-flash, high reasoning effort, 256000 max tokens and a 300-second stream idle timeout. Retries cover transient classes with exponential backoff and jitter, emitting llm/retry. Images prefer the Files API in auto mode and fall back to inline base64. Redirects are never followed, so credentials cannot leak to another origin.",
      try: "export DEEPSEEK_API_KEY=...\nminidsh --disable llm.mock --enable llm.deepseek run \"read README.md\"\nminidsh --disable llm.mock --enable llm.deepseek models",
      observe: ["llm/retry appears on transient failures and resets the attempt draft.", "Images appear as llm/image-upload or llm/image-fallback.", "A redirect is refused rather than followed."],
      tradeoff: "A learning slice, not a compatibility guarantee: online acceptance has not been run in CI, and the adapter is not a byte-for-byte port of upstream.",
      industry: ["Model protocol", "Replay envelopes and error classes must survive resume and retry.", "回放信封与错误分类必须能扛过恢复和重试。", "https://modelcontextprotocol.io/specification/2026-07-28/changelog"]
    },
    zh: {
      title: "真实模型适配器",
      thesis: "提供方适配器本质上是协议记账：块顺序、签名、用量与错误分类。",
      concept: "DeepSeek 适配器在 SSE 上实现 Messages 协议：文本、带回放签名的思考、带 JSON Schema 的工具调用、图片、用量与停止原因。",
      why: "大多数「第一轮正常、恢复就坏」的 bug 都在这里。块顺序或签名回放一旦出错，恢复的会话要么丢掉思考内容，要么被 API 直接拒绝。",
      how: "请求发往配置根地址下的 /v1/messages，携带 x-api-key 与 anthropic-version。默认 deepseek-flash、high 思考强度、256000 max tokens、300 秒流空闲超时。重试覆盖瞬时错误类别，指数退避带抖动，并发出 llm/retry。图片在 auto 模式下优先 Files API，失败降级为内联 base64。永不跟随重定向，凭据不会泄露到其他源。",
      try: "export DEEPSEEK_API_KEY=...\nminidsh --disable llm.mock --enable llm.deepseek run \"read README.md\"\nminidsh --disable llm.mock --enable llm.deepseek models",
      observe: ["瞬时失败时出现 llm/retry，并重置本次尝试的草稿。", "图片以 llm/image-upload 或 llm/image-fallback 出现。", "重定向会被拒绝，而不是跟随。"],
      tradeoff: "这是学习切片，不是兼容性保证：CI 未做在线验收，适配器也不是上游的逐字节移植。"
    }
  }
];
