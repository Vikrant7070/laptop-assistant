# Skill file formats and authoring best practices for LLM agents

Research date: 2026-11-17. Context: redesigning a Windows voice assistant's skill files (Markdown + frontmatter: name, description, when-triggers, kind routine|recipe; routines = tool-call lines executed by code, recipes = prose the LLM follows).

## 1. Anthropic Agent Skills (SKILL.md): structure, frontmatter, progressive disclosure, descriptions, degrees of freedom, limits, evals, anti-patterns

### Takeaway
SKILL.md is now a cross-vendor open standard (agentskills.io): a folder with YAML frontmatter (`name`, `description` required) + Markdown body + optional `scripts/`, `references/`, `assets/`. It loads in three stages (metadata, then body, then files), and the `description` alone decides whether a skill triggers. Anthropic's rules are: be concise, match freedom to how fragile the task is, use scripts for deterministic steps, add validate-fix loops, keep references one level deep, keep the body under 500 lines, and write evals before writing the skill.

### Cited Findings
**Format and spec**
- Anthropic Engineering post "Equipping agents for the real world with Agent Skills" was published 16 Oct 2025. A skill is a directory with SKILL.md whose frontmatter `name` and `description` are loaded into the system prompt at startup. An 18 Dec 2025 update says Skills were published as an open standard (agentskills.io) — [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- The open spec defines the layout `skill-name/SKILL.md` plus optional `scripts/` (executable code), `references/` (docs loaded on demand) and `assets/` (templates, data) — [agentskills.io spec](https://agentskills.io/specification)
- Spec frontmatter:
  - `name` (required): 1–64 chars; lowercase a-z, 0-9 and hyphens only; no leading, trailing or consecutive hyphens; must match the directory name.
  - `description` (required): 1–1024 chars; says what the skill does and when to use it, with specific keywords.
  - Optional fields: `license`, `compatibility` (≤500 chars, environment requirements), `metadata` (string→string map, e.g. author/version), and `allowed-tools` (space-separated pre-approved tools, marked "Experimental").
  - The body has "no format restrictions". Recommended contents: step-by-step instructions, input/output examples, edge cases. — [agentskills.io spec](https://agentskills.io/specification)
- The spec gives token budgets for progressive disclosure: metadata ~100 tokens per skill (loaded for all skills at startup), instructions <5000 tokens recommended (loaded on activation), resources loaded only when required. It says to keep SKILL.md under 500 lines and file references one level deep. Validator: `skills-ref validate ./my-skill` — [agentskills.io spec](https://agentskills.io/specification)
- Anthropic platform rules: `name` max 64 chars, no XML tags, cannot contain reserved words "anthropic" or "claude". `description` is non-empty, max 1,024 chars, no XML tags — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

**Claude Code extensions (useful for "when-triggers", slots and side effects)**
- Claude Code adds these frontmatter fields: `when_to_use` (extra trigger phrases, appended to the description), `argument-hint`, `arguments` (named positional args for `$name` substitution), `disable-model-invocation` (for side-effecting workflows), `user-invocable: false` (background knowledge only), `allowed-tools`/`disallowed-tools`, `model`, `effort`, `context: fork` (run in a subagent), `paths` (glob gating), `hooks`, and `shell` (bash/powershell) — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- Argument placeholders: `$ARGUMENTS`, `$0`/`$1`, and named `$name`. If no placeholder consumes the args, `ARGUMENTS: <value>` is appended. Dynamic context ``!`cmd` `` runs a shell command before the skill reaches the model. A non-zero exit (≥2) aborts the whole invocation, and each command has a 2-min timeout — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- Limits:
  - `description` + `when_to_use` are truncated at 1,536 chars combined in the listing ("Put the key use case first").
  - Once loaded, the body stays in context across turns.
  - After compaction each invoked skill keeps its first 5,000 tokens, within a shared 25,000-token budget.
  - Outside Claude Code (API/claude.ai upload) only the spec fields are allowed; other keys cause a hard error.
  - [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- Claude Code tips:
  - Set `disable-model-invocation: true` for deploy, commit or send-message skills so the model doesn't trigger them on its own.
  - "Put the most important instructions first and state what to do rather than narrating why."
  - Test triggering in fresh sessions with and without the skill, because leftover context can mask gaps.
  - [Claude Code skills docs](https://code.claude.com/docs/en/skills)

**Descriptions**
- Always write descriptions in third person ("Processes Excel files…", not "I can help…"/"You can use…") because the description is injected into the system prompt. It should state both what the skill does and its specific triggers, with key terms. The model picks from "potentially 100+" skills using it. Good: "Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when the user mentions PDFs, forms, or document extraction." Bad: "Helps with documents" — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Naming: gerund form preferred (`processing-pdfs`). Avoid vague names (`helper`, `utils`), generic names (`documents`) and inconsistent patterns — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

**Conciseness and degrees of freedom**
- "Default assumption: Claude is already very smart". Only add context the model lacks, and ask of each paragraph whether it "justif[ies] its token cost" — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Degrees of freedom:
  - High freedom (prose): when multiple approaches are valid or context decides.
  - Medium (pseudocode or parameterized scripts): when a preferred pattern exists.
  - Low (exact script, "Do not modify the command"): when operations are fragile, consistency is critical, or the sequence is fixed.
  - Analogy: a narrow bridge with cliffs calls for exact guardrails; an open field calls for general direction.
  - [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Test with every model you will run the skill on. Haiku may need more guidance, and Opus may be over-explained to — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

**Workflows, checklists, feedback loops**
- Break complex tasks into sequential steps. For very complex ones, provide a copyable checklist (`- [ ] Step 1: …`) that the model checks off as it goes — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Feedback loop pattern: "Run validator → fix errors → repeat", with explicit gates such as "Only proceed when validation passes" and "If verification fails, return to Step 2". The validator can be a script or a reference doc (e.g. a style guide) — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Plan-validate-execute: write a structured plan file (e.g. `changes.json`), validate it with a script, then execute and verify. Use it for batch, destructive or high-stakes operations. Make validator errors specific, e.g. "Field 'signature_date' not found. Available fields: …" — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Conditional workflow pattern: "Creating new content? → Follow 'Creation workflow'…". Move large workflows into separate files — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Template pattern (strict "ALWAYS use this exact template" vs a flexible default) and examples pattern (input/output pairs) — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

**Scripts vs instructions**
- Pre-made scripts are more reliable than generated code, save tokens (only their output enters context), and give consistent results. State whether the model should run a script ("Run `analyze_form.py`") or read it as a reference — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices); [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- "Solve, don't defer": scripts should handle errors themselves rather than fail and leave the problem to the model. Justify constants instead of leaving "voodoo constants" (e.g. a commented `REQUEST_TIMEOUT = 30`) — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Refer to MCP tools by fully qualified name (`ServerName:tool_name`) to avoid "tool not found" errors. Don't assume packages are installed — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

**Anti-patterns**
- Deeply nested references: the model may `head -100` a second-level file and get incomplete information.
- Reference files over 100 lines without a table of contents.
- Time-sensitive statements ("before August 2025 use…"). Put these in an "Old patterns" section instead.
- Inconsistent terminology (mixing "field", "box" and "control").
- Offering too many options. Give one default plus an escape hatch.
- Windows backslash paths.
- Vague descriptions.
- [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

**Evaluation-driven authoring**
- Build evaluations BEFORE writing extensive documentation:
  1. Run the model on representative tasks without the skill.
  2. Document its failures.
  3. Write three scenarios that test those failures.
  4. Measure a baseline.
  5. Write minimal instructions that pass.
  6. Iterate.
  - Eval JSON shape: `{skills, query, files, expected_behavior[]}`. There is no built-in runner — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- "Claude A / Claude B" loop: one instance writes and refines the skill, a fresh instance uses it on real tasks, and you bring observed failures back. Example: "forgot to filter test accounts even though the Skill mentions it" was fixed by making the rule more prominent with "MUST". Also watch how the model navigates the skill: unexpected read order, missed links, files it never reads — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Anthropic Engineering: start from evaluation; split long SKILL.md files; monitor real usage and refine name and description; have the model write its own successes and mistakes back into the skill — [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- Security: skills can introduce vulnerabilities or exfiltrate data. Install only trusted skills and audit their scripts, dependencies and network instructions — [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)

### Inferences
- The assistant's `when` field corresponds to Claude Code's `when_to_use`. Best practice is to keep the triggers inside or appended to a third-person `description`, put the key use case first, and respect a combined character cap (≈1,024–1,536).
- The routine/recipe split maps onto Anthropic's degrees of freedom: routines are "low freedom" (exact, code-executed), recipes are "high/medium freedom" prose. Anthropic's guidance supports keeping fragile multi-step OS automation as code-executed routines.
- Side-effecting skills (send, delete, purchase) deserve a field like `disable-model-invocation` or a confirmation gate, so they cannot be auto-triggered from loose voice matches.

### Gaps
- Anthropic publishes no empirical numbers on trigger accuracy versus description length or style. The guidance is qualitative.
- I didn't check whether the agentskills.io spec has changed since this fetch. It had no version number or date.

## 2. OpenAI, Microsoft Semantic Kernel, MCP, LangChain and workflow tools: how each represents a reusable procedure

### Takeaway
The industry has converged on two forms:
1. SKILL.md-style folders (description-triggered and progressively loaded), now adopted by OpenAI Codex/ChatGPT.
2. Typed function/tool schemas called natively (Semantic Kernel replaced its prompt-based planners with function calling; MCP separates model-controlled tools from user-controlled prompt templates with typed arguments).

AGENTS.md is the always-loaded, unstructured counterpart: context, not procedures.

### Cited Findings
- **OpenAI Codex / ChatGPT skills**:
  - Same SKILL.md format (`name` and `description` required; optional `scripts/`, `references/`, `assets/`, `agents/`).
  - Optional `agents/openai.yaml` with `interface` (display_name, short_description, icons, default_prompt), `policy.allow_implicit_invocation` (default true; when false, only explicit `$skill` invocation works), and `dependencies.tools` (MCP servers).
  - [OpenAI build-skills docs](https://learn.chatgpt.com/docs/build-skills) (redirected from developers.openai.com/codex/skills)
- Codex skill-list budget: name + description + path for all skills, capped at 2% of the context window (or 8,000 chars if unknown). Descriptions are shortened first, and skills may be omitted when there are many. `skills.max_context_tokens` can raise the cap to 10,000 tokens — [OpenAI build-skills docs](https://learn.chatgpt.com/docs/build-skills)
- OpenAI authoring advice:
  - "Keep each skill focused on one job."
  - Prefer instructions over scripts unless you need determinism or external tooling.
  - Write imperative steps with explicit inputs and outputs.
  - Front-load key use cases and trigger words in the description.
  - Test prompts against the description.
  - [OpenAI build-skills docs](https://learn.chatgpt.com/docs/build-skills)
- Codex skill locations: repo `.agents/skills`, user `$HOME/.agents/skills`, admin `/etc/codex/skills`, plus system skills (skill-creator, skill-installer). Same-name skills are not merged — [OpenAI build-skills docs](https://learn.chatgpt.com/docs/build-skills). A third-party guide notes conflicting older paths (`~/.codex/skills`) — [search summary of developers.openai.com/codex/skills](https://developers.openai.com/codex/skills.md)
- **AGENTS.md**:
  - Plain Markdown with no required fields: a "README for agents" (project overview, build/test commands, code style, testing, security).
  - Agents read the nearest file in the directory tree, and the user's chat prompt overrides it.
  - Supported by Codex, Jules, Gemini CLI, Cursor, Copilot, VS Code, Windsurf, Aider, Zed and Junie; used by 60k+ projects; now stewarded by the Agentic AI Foundation (Linux Foundation).
  - [agents.md](https://agents.md/)
- **OpenAI "A practical guide to building agents"** (≈34-page PDF, 2025):
  - Build "routines" from existing operating procedures, support scripts or policy docs, turned into "LLM-friendly routines". In customer service, a routine roughly maps to one knowledge-base article.
  - Break dense resources into smaller, clearer steps, and add branches for common variations, e.g. what to do when required info is missing.
  - [OpenAI guide PDF](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf); wording via [search summaries, e.g. maginative](https://www.maginative.com/article/how-to-build-ai-agents-a-detailed-practical-guide-from-openai/)
  - Guardrail layers summarized: relevance/safety classifiers, tool safeguards (rate limits, allowlists, approval gates), logging, and human review for high-risk actions — [ibl.ai summary](https://ibl.ai/blog/openai-a-practical-guide-to-building-agents.md)
- **Microsoft Semantic Kernel**:
  - Reusable capabilities are "plugins" of functions with descriptions.
  - The Stepwise and Handlebars planners (prompt-generated plans) "have been deprecated and removed" in .NET, Python and Java.
  - Function calling in an automatic loop is now the planning mechanism: call, check the result, decide the next step.
  - Page updated 2026-05-26.
  - [Microsoft Learn: Planning](https://learn.microsoft.com/en-us/semantic-kernel/concepts/planning)
- **MCP**:
  - Prompts are "user-controlled" templates (often exposed as slash commands), defined with `name`, optional `title`, `description` and `arguments[]` (`name`, `description`, `required`).
  - Fetched via `prompts/get` with arguments and returned as role-tagged messages (text, image, audio or embedded resource).
  - Servers SHOULD validate arguments and return -32602 for a missing required arg.
  - Contrast: tools are model-invoked.
  - Spec 2025-06-18. [MCP spec: Prompts](https://modelcontextprotocol.io/specification/2025-06-18/server/prompts)
- **Anthropic "Writing effective tools for agents"** (11 Sep 2025):
  - Consolidate multi-step workflows into single tools (`schedule_event` instead of list_users + list_events + create_event).
  - Namespace tools (`asana_projects_search`).
  - Return human-readable fields, and offer `response_format` concise/detailed (concise used ~1/3 of the tokens).
  - Return actionable error messages.
  - Use unambiguous parameter names (`user_id`, not `user`).
  - Evaluate on realistic multi-step tasks with verifiable outcomes, tracking accuracy, tool-call counts, tokens and errors.
  - [Anthropic Engineering](https://www.anthropic.com/engineering/writing-tools-for-agents)

### Inferences
- The assistant's "routine" is closest to a consolidated tool or workflow script. The Anthropic tools post suggests that frequently used routines should become a single named tool where possible, rather than a sequence of fine-grained calls the LLM must chain.
- MCP's prompt-argument schema (name/description/required) is a good, minimal model for "slots" in skill frontmatter, with validation before execution.
- An explicit-only invocation flag (OpenAI `allow_implicit_invocation: false`, Claude `disable-model-invocation`) is a cross-vendor pattern worth copying for risky voice-triggered skills.

### Gaps
- I did not fetch primary docs for LangChain/LangGraph tools, Apple Shortcuts (App Intents) or Power Automate in this pass, so no cited specifics are available for them. Unverified background: LangChain tools are functions with name, description and args schema (often Pydantic); LangGraph represents procedures as explicit state graphs; Shortcuts and Power Automate are deterministic action graphs with typed parameters. These need verification before citing.
- The exact wording of the OpenAI practical guide's "Configuring instructions" section could not be extracted (the PDF was binary in the fetch). The points above rely on search snippets and secondary summaries.
- I found no OpenAI Agents SDK primitive named "skills". The Agents SDK uses agents + tools + handoffs + guardrails; I did not verify this against current docs.

## 3. Concrete authoring rules: steps, success criteria, verification, failure handling, parameters, pre/postconditions

### Takeaway
The sources agree on:
- Imperative, numbered steps with explicit inputs and outputs.
- One job per skill.
- One default path plus an escape hatch.
- Explicit verification gates ("only proceed when…"), with loops back to an earlier step on failure.
- Specific, actionable error text.
- Typed, required/optional parameters validated before execution.
- Explicit branches for missing information or edge cases.
- The most important rules first, in stronger wording when they get missed.

### Cited Findings
- Write imperative steps with explicit inputs and outputs, one job per skill — [OpenAI build-skills docs](https://learn.chatgpt.com/docs/build-skills)
- Use copyable checklists and step-level gates: "Fix any validation errors before continuing", "If verification fails, return to Step 2" — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Put the most important instructions first and state what to do, not why — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- Rules that get missed should be made more prominent and stronger ("MUST filter") — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Add branches for common variations, including what to do when required info is missing — [OpenAI guide via maginative summary](https://www.maginative.com/article/how-to-build-ai-agents-a-detailed-practical-guide-from-openai/)
- Use specific error messages that list the valid alternatives — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices); [Anthropic tools post](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Parameters/slots:
  - Claude Code `arguments:` + `$name` + `argument-hint` — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
  - MCP `arguments[{name, description, required}]` with server-side validation — [MCP spec](https://modelcontextprotocol.io/specification/2025-06-18/server/prompts)
- Preconditions and environment requirements: spec `compatibility` field; "don't assume tools are installed" — [agentskills.io spec](https://agentskills.io/specification); [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Dynamic precondition context: Claude Code ``!`cmd` `` injects live state before the model sees the skill, and a failing command aborts the skill — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- Use consistent terminology and concrete examples (input/output pairs) — [Claude docs: best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

### Inferences
- A suggested routine/recipe step grammar for the assistant:
  - Number each step.
  - Write one imperative action per step, naming the tool.
  - Add an "expect:" postcondition to each step.
  - Add an "on fail:" branch to each step (retry, alternative, or ask the user).
  - Add a final "done when:" success criterion.
- No single source prescribes formal pre/postcondition fields for skills. They appear only implicitly, as validation gates. Formal fields are a design choice rather than an industry standard.

### Gaps
- No primary source gave quantitative evidence for specific step phrasings (e.g. numbered vs bulleted steps, MUST vs should). The advice is practitioner guidance.

## 4. Evidence: structured/executable formats vs free prose for reliability

### Takeaway
Academic benchmarks consistently find that LLMs following natural-language SOPs are unreliable (30–50% pass for strong models on SOPBench). Moving control flow into structure — code, workflow graphs, rule engines, or restricted per-step action sets — gives roughly 20–30-point gains. This supports executing routines in code and leaving the LLM only language understanding and slot filling.

### Cited Findings
- SOPBench (arXiv 2503.08669; v1 11 Mar 2025, v2 17 Jun 2025):
  - 167 tools across 7 customer-service domains, 900+ verified cases, 18 models.
  - Each SOP is implemented both as natural language (given to the agent) and as executable code (the oracle).
  - Top models such as GPT-4o and Claude-3.7-Sonnet find the task challenging. Other powerful models score 30–50% pass, o4-mini-high is best, and 7–8B models are significantly worse.
  - Agents are "easily jailbroken" into ignoring SOPs.
  - [arXiv: SOPBench](https://arxiv.org/abs/2503.08669)
- "Don't Prompt, Graph It: Reliable SOP Compliance via Workflow Graphs" (AutoSOP; NeurIPS 2026 workshop poster; Ghosh et al.):
  - Prompting with SOP documents directly causes rule confusion, hallucination and inconsistency.
  - AutoSOP compiles the SOP offline into a workflow graph. A deterministic rule engine picks steps and branches, and the LLM only handles language and step inputs.
  - Results: RuleArena 81.3% vs 61.0% for the best baseline (+20.3; the page headline says "up to 30 points", which the shown numbers don't support). FlowBench session success +28.7 (single-scenario) and +20.0 (cross-scenario).
  - The authors also claim prompt-injection resistance.
  - [NeurIPS 2026 listing](https://neurips.cc/virtual/2026/160091)
- SOP-Agent (arXiv 2501.09316) represents the SOP as a decision graph and restricts the agent to the function calls valid at the current step, which "improves decision robustness" — [search result via emergentmind/themoonlight summaries](https://www.themoonlight.io/en/review/sop-agent-empower-general-purpose-ai-agent-with-domain-specific-sops)
- EMNLP 2025 Industry paper: a multi-agent framework converts unstructured SOPs into structured plans and executable code templates, and reports improved consistency — [ACL Anthology PDF](https://aclanthology.org/anthology-files/pdf/emnlp/2025.emnlp-industry.163.pdf) (from search snippet; numbers not verified)
- Vendor position: Anthropic says code is deterministic and repeatable, and prefers scripts for fragile or consistency-critical steps — [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills); [Claude docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Counterpoint: OpenAI recommends instructions over scripts unless determinism or external tooling is needed — [OpenAI build-skills docs](https://learn.chatgpt.com/docs/build-skills)
- Semantic Kernel dropped prompt-generated structured plans (Handlebars/Stepwise) in favour of iterative function calling, which suggests rigid LLM-authored plans were less useful than step-by-step calls with feedback — [Microsoft Learn](https://learn.microsoft.com/en-us/semantic-kernel/concepts/planning)

### Inferences
- The best-supported hybrid:
  - Code owns sequencing, branching and verification (routines, or recipes compiled into a step graph).
  - The LLM fills slots, interprets ambiguous results and handles exceptions.
  - At each step, expose only that step's relevant tools (the SOP-Agent idea).
- The Semantic Kernel experience shows a nuance: structure should be authored and verified (human- or compiler-made), not generated on the fly by the LLM as a rigid plan.
- Small local models (relevant for a 6 GB GPU fallback) did significantly worse on prose SOPs. That argues even more strongly for code-executed routines when the local brain is active.

### Gaps
- I did not verify SOP-Agent or EMNLP 2025 numbers from the primary PDFs; they come from search snippets.
- I found no head-to-head study specifically on desktop/OS automation (as opposed to customer-service SOPs) comparing prose recipes with executable routines.
- AutoSOP is a workshop poster; peer-review depth and its baselines are unclear from the listing.
