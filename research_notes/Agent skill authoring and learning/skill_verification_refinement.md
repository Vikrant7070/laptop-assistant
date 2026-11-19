# Verifying, Versioning and Refining Agent Skills from Failures and User Corrections

Context: Nova is a desktop voice assistant with a self-maintained skill library. Today its skills can't be patched incrementally, outcomes aren't tracked, the screen isn't verified, and the model sometimes claims success for steps it never did. Notes current as of Nov 2026. Some figures come from secondary summaries; those are flagged.

## Verification: how do agents check that a skill actually worked, and catch hallucinated success?

### Takeaway
Self-reports are unreliable, so trusted systems verify outside the actor. In order of reliability: programmatic postconditions and state checks, then a separate critic/judge model looking at fresh observations, then the model reflecting on its own. LLM/VLM judges lean toward false "success". Pairing them with hard state checks and a two-step "predict what success looks like, then compare" pattern reduces that.

### Cited Findings
- Anthropic's computer-use docs say outright: "Claude sometimes acts as if its actions succeeded without explicitly checking their results." The recommended prompt: "After each step, take a screenshot and carefully evaluate if you have achieved the right outcome. State in one sentence what the screenshot shows and whether the step succeeded. If it didn't, try again. Only when you confirm a step was executed correctly should you move on to the next one." The docs also say batches typically end with a screenshot, and that you should require this in the system prompt if you want it every time. — [Anthropic computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- The same docs advise asking a human to confirm "decisions that might result in meaningful real-world consequences and any tasks requiring affirmative consent" (cookies, financial transactions, ToS). They also warn that instructions inside screenshots or web pages can override the user's instructions, i.e. prompt injection. Classifiers scan tool results and steer the model to check whether an instruction really came from the user. — [Anthropic computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- OpenAI's computer-use (CUA) API returns `pending_safety_checks` (e.g. code `malicious_instructions`) on a computer call. The developer must surface these to a human and send them back as `acknowledged_safety_checks` before the agent continues. This is a hard human gate built into the protocol. — [Azure OpenAI computer-use docs (mirror of OpenAI's)](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/computer-use); [OpenAI community thread](https://community.openai.com/t/computer-tool-has-unacknowledged-safety-check/1147615)
- Voyager (Minecraft) uses a second GPT-4 instance as a "critic" to judge whether a task was completed before a skill enters the library. Removing self-verification caused a 73% drop in discovered items, making it the most important feedback type in the ablation. — [Voyager, arXiv 2305.16291](https://arxiv.org/pdf/2305.16291)
- ASI (Agent Skill Induction, CMU, 2025) writes skills as executable programs that come with test trajectories. A skill joins the action space only after verification. It reported +23.5% success over the static baseline on WebArena and +11.3% over the text-skill counterpart (AWM), mainly from "programmatic verification" at induction, plus 10.7–15.3% fewer steps. — [ASI, arXiv 2504.06821](https://arxiv.org/abs/2504.06821)
- SkillWeaver (OSU, 2025) runs a "skill honing" stage that tests and debugs synthesized Playwright-function APIs using environment feedback and a reward model. One example: a runtime error exposed an ambiguous selector, which was then fixed. Reported gains: 31.8% relative on WebArena and 39.8% on real sites. APIs written by strong agents improved weaker agents by up to 54.3%. Some summaries give different figures, so check the paper. — [SkillWeaver, arXiv 2504.07079](https://arxiv.org/abs/2504.07079); [project page](https://osu-nlp-group.github.io/SkillWeaver)
- Judges over-credit success:
  - In OS-Harm (on OSWorld), the LLM judge often marks a task "successful" as soon as it sees any relevant action. Task-completion agreement with humans was 82%, with 50% precision on one category. — [OS-Harm, arXiv 2506.14866](https://arxiv.org/pdf/2506.14866)
  - MLLM verifiers on OSWorld/VisualWebArena show "agreement bias": high false-positive rates and failure detection as low as ~50%. The proposed fix is Self-Grounded Verification, which first generates what success should look like and then checks the trajectory against it. — [Self-Grounded Verification, arXiv 2507.11662](https://arxiv.org/html/2507.11662v2)
  - OpenComputer: an LLM judge matched human verdicts on 95/120 tasks vs 113/120 for a hard-coded programmatic verifier (checklist-item agreement 92.2% vs 97.3%). The authors attribute much of the gap to the judge seeing only a narrow window of the screen. — [OpenComputer, arXiv 2605.19769](https://arxiv.org/pdf/2605.19769)
  - In iOSWorld (phone agents), a trajectory-level judge agreed with humans 89% of the time (κ=0.77). A per-step judge was more lenient: 188 false-positive criteria vs 79. — [iOSWorld, arXiv 2606.09764](https://arxiv.org/pdf/2606.09764)
- Intrinsic self-correction with no external feedback does not reliably improve reasoning and sometimes makes it worse. Models struggle to judge their own answers. The authors call for external feedback. — [Huang et al., ICLR 2024, arXiv 2310.01798](https://arxiv.org/abs/2310.01798)
- Microsoft UFO²: the AppAgent state machine has explicit FINISH / FAIL (recoverable) / SCREENSHOT (re-annotate) states, and subtask results are posted to a Blackboard for the HostAgent. It also combines GUI actions with native app APIs. I found no documented dedicated post-action verifier. — [UFO² AppAgent state docs](https://microsoft.github.io/UFO/ufo2/app_agent/state); [UFO² paper, arXiv 2504.14603](https://arxiv.org/abs/2504.14603)
- Practitioner failure mode (secondary source): if the success signal is a toast that disappears after about 2 seconds, an agent that never captures it keeps re-submitting. Verification needs durable state, not transient UI. — [blog roundup, low authority](https://dev.to/custodiaadmin/your-browser-automation-agent-is-blind-to-failures-2hk8)

### Inferences
- For Nova: every skill step should declare a postcondition that the harness checks, not the model. Examples: window title or process exists (UIA/pywinauto), file exists or changed, clipboard content, UIA control value, URL. Use a VLM screenshot check only when no structured check exists, and run it as a separate critic call that sees a fresh screenshot and the expected-state description. The actor's own claim should never count.
- Mark a step "done" only when a tool result says so. If the model's final message claims steps that have no matching tool-call record, the harness can flag that as hallucinated success. This is a cheap, deterministic check against the transcript.
- Make the critic default to "not done" when evidence is missing (asymmetric threshold), because judges skew toward false positives.

### Gaps
- No controlled study found that measures how much per-step screenshot verification reduces hallucinated-success rates for Claude or OpenAI CUA specifically.
- OpenAI's own CUA guide wasn't retrieved directly. Field names come from the Azure mirror and SDK docs.

## Refinement: how are skills improved from failures and user corrections?

### Takeaway
Refinement loops work when they are grounded in external signals: environment errors, test results, critic verdicts, or user edits. The loop distills lessons into small, discrete items (reflections, insights, bullets, preference statements). Systems that edit items in place with add/update/delete-style operations hold up better than ones that regenerate the whole procedure.

### Cited Findings
- Reflexion stores verbal self-reflections in episodic memory across trials, with no weight updates. Results: AlfWorld 130/134 tasks (ReAct+Reflexion), HumanEval 91% pass@1 vs 80% reported for GPT-4. It works best when the evaluator gives a crisp signal; AlfWorld used a heuristic hallucination/inefficiency detector. Secondary sources disagree on baseline figures. — [Reflexion, arXiv 2303.11366](https://arxiv.org/pdf/2303.11366); [review](https://beancount.io/bean-labs/research-logs/2026/04/25/reflexion-language-agents-verbal-reinforcement-learning)
- ExpeL extracts natural-language insights from both successes and failures and maintains them with operations ADD, EDIT, UPVOTE and DOWNVOTE. Insights whose importance count falls to zero are removed. Insights helped most on HotpotQA and retrieving successful trajectories helped most on ALFWorld. — [ExpeL, AAAI 2024, arXiv 2308.10144](https://arxiv.org/abs/2308.10144). The vote/removal mechanism is from the paper; the snippet confirms add/edit/vote.
- Voyager refines iteratively. Generated skill code is run, and environment feedback, execution errors and the critic's verdict go back into the next generation round (up to a few rounds). Only critic-verified code is added to the skill library, indexed by an embedding of its description. — [Voyager, arXiv 2305.16291](https://arxiv.org/pdf/2305.16291)
- ACE (Agentic Context Engineering, ICLR 2026) has three roles: Generator, Reflector (distills lessons) and Curator (emits small "delta" bullets). The deltas are merged into an itemized playbook by deterministic non-LLM logic, and a "grow-and-refine" step merges or prunes items by semantic similarity. It reports ~+10.6% on agent benchmarks and 86.9% lower adaptation latency vs GEPA. It names "context collapse": in one AppWorld case, a monolithic rewrite shrank an ~18k-token context to 122 tokens and accuracy fell from 66.7% to 57.1%, below the unadapted baseline. — [ACE, arXiv 2510.04618](https://www.emergentmind.com/papers/2510.04618); [InfoQ](https://infoq.com/news/2025/10/agentic-context-eng)
- Mem0: for each candidate fact, the LLM retrieves the top-s similar memories and picks ADD / UPDATE / DELETE / NOOP via a tool call. Reported LOCOMO LLM-as-judge score is ~66.9% vs 52.9% for OpenAI memory (the "+26%" headline). Some baseline numbers, such as Zep's, were later disputed. — [Mem0, arXiv 2504.19413](https://arxiv.org/abs/2504.19413); [explainer](https://terencecho.github.io/research-explained/mem0/)
- A-MEM (NeurIPS 2025): when a note is added, its nearest neighbours are retrieved and the LLM decides whether to update their context, keywords or tags ("memory evolution"). In the ablation, evolution gave the larger gain on multi-hop tasks. — [A-MEM, arXiv 2502.12110](https://neurips.cc/virtual/2025/poster/119020); [review](https://themoonlight.io/review/a-mem-agentic-memory-for-llm-agents)
- Letta (MemGPT) memory blocks are edited by tools:
  - `memory_insert` (text at a line; -1 means append)
  - `memory_replace` (exact-match old→new string; fails if the string is not unique)
  - `memory_rethink` (full rewrite of the block)
  - `memory_finish_edits`
  
  A community note (informal) says insert is the safest under concurrency and rethink is last-writer-wins. — [Letta base tools docs](https://docs.letta.com/guides/agents/base-tools); [Letta forum](https://forum.letta.com/t/skill-md-for-letta/99)
- LangMem procedural memory stores learned procedures as updated system-prompt instructions. `create_prompt_optimizer(kind=...)` offers `prompt_memory` (single pass, 1 LLM call), `metaprompt` (reflection, 1–5 calls) and `gradient` (separate critique and proposal steps). Input is trajectories plus optional feedback; output is a new prompt. — [LangMem launch post](https://blog.langchain.com/langmem-sdk-launch); [LangMem concepts](https://langchain-ai.github.io/langmem/concepts/conceptual_guide)
- Learning from user corrections: PRELUDE/CIPHER (Microsoft, NeurIPS 2024) has an LLM infer a natural-language preference from each user edit and store it by context. For a new request it retrieves the preferences from the most similar past contexts and aggregates them into the prompt. It achieved the lowest edit-distance cost vs baselines on summarization and email tasks, and the learned preferences are readable and editable by users. — [PRELUDE/CIPHER, arXiv 2404.15269](https://arxiv.org/abs/2404.15269)
- Anthropic's Agent Skills guidance: "Monitor how Claude uses your skill in real scenarios and iterate based on observations", including unexpected trajectories and overreliance on certain context. It also suggests asking Claude to capture successful approaches and common mistakes as reusable skill context. — [Anthropic engineering, Oct 2025](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)

### Inferences
- For Nova:
  - When the user says "no, that's wrong" or "do it this way", store it as a correction event tied to the skill and the step: what happened, what the user said, the resulting patch.
  - Have a Reflector distill a short lesson from that event, ExpeL/CIPHER style.
  - Apply the lesson as a targeted patch to the skill step, not as a regenerated skill.
  - Failure-driven repair should be triggered by verified failures (postcondition failed, error box, user correction), not by the model's own sense that something went wrong.

### Gaps
- I found no published desktop-assistant study (Windows) that measures skill repair from spoken user corrections specifically.

## Versioning and governance: how are skill libraries kept healthy, safe and tested?

### Takeaway
2026 work identifies "library drift": unmanaged growth of self-written skills degrades performance until skills can hurt more than having none. The proposed remedies are outcome-tracked utility per skill, eviction/retirement with minimum-evidence guards, size caps, gated admission (tests or verifier) and human review. Reliable outcome labels are a prerequisite, because a lenient judge silently disables retirement.

### Cited Findings
- Ratchet (arXiv 2605.22148, May 2026):
  - LLM-written skills added +0.0 pp over a no-skill control, while human-written skills added +16.2 pp.
  - Ratchet's hygiene recipe (evict skills by measured contribution, cap library width C, constrain synthesis) raised held-out pass@1 by +0.328 on a hard MBPP+ slice.
  - End-task score is a weak alarm for drift: it moves by at most a fifth of the governed lift.
  - Under its eviction rule, a judge that scores failures as passes at rate ≥ (1−τ)/2 retires no skills at any sample size.
  
  — [Ratchet, arXiv 2605.22148](https://arxiv.org/abs/2605.22148)
- Library Drift (arXiv 2605.19576) links unbounded accumulation without outcome-based retirement to worse retrieval and stagnating performance. The survey says versioning, conflict detection and deprecation are largely ignored in existing skill-library systems. — [Library Drift, arXiv 2605.19576](https://www.alphaxiv.org/abs/2605.19576) (via search summary)
- A pruning rule from arXiv 2608.07955 prunes a skill only when all three hold: error rate is above a threshold, it has been inactive too long, and there is enough failure evidence. A minimum-support guard prevents pruning on a few noisy failures. — [arXiv 2608.07955](https://arxiv.org/pdf/2608.07955) (via search summary)
- Results on retirement and pruning:
  - SkillForge (arXiv 2610.09832) evaluates, retires and rewrites skills during RL. Its 95-skill library reached 78.4% on WebShop vs 72.7% for SkillRL, but the ablation does not separate retirement from the size cap.
  - D2Skill (arXiv 2603.28716) uses utility-guided retrieval and pruning: +10–20 points over skill-free baselines on ALFWorld and WebShop.
  
  — [SkillForge](https://www.alphaxiv.org/abs/2610.09832); [D2Skill](https://arxiv.org/html/2603.28716v1) (via search summary)
- Retrieval degrades with library size (secondhand newsletter, underlying paper unconfirmed): as the candidate pool grew from 5 to 100 skills, the share of opened skills that were correct fell from ~30% to ~3%. — [The Intelligent Commit](https://buttondown.com/theintelligentcommit/archive/the-agent-stopped-picking-the-right-skill-and-finished-anyway/)
- Admission gates in research systems: Voyager (critic), ASI (test trajectories) and SkillWeaver (honing with tests). — sources above.
- Anthropic's skill-creator workflow (secondary descriptions) adds an eval harness: test prompts with assertions, and comparison of old vs new skill versions in parallel. Anthropic reportedly saw better triggering on 5 of 6 public document skills. — [ainews summary](https://www.ainews.com/p/anthropic-introduces-built-in-evaluation-and-benchmarking-for-claude-agent-skills-to-improve-enterpr) (secondary; verify)
- Safety: Anthropic and OpenAI both require human confirmation for consequential actions, and OpenAI's protocol enforces acknowledgement of safety checks before continuing. This implies permission gates must live in the harness or tool layer, not in skill text the model can rewrite. — [Anthropic docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool); [Azure OpenAI docs](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/computer-use)

### Inferences
- Suggested Nova skill record:
  - `id`, `version`, `parent_version`, `created_by` (user/model), `status` (draft/active/deprecated), `approved_by_user`
  - per-step postconditions
  - stats: `runs`, `successes`, `verified_successes`, `last_error`, `last_used`, `corrections[]`
  - append-only history of patches, so any version can be rolled back
- Suggested lifecycle:
  - Model-written skills start as draft and become active only after one verified run, a dry run or user confirmation by voice.
  - Deprecate when the verified success rate falls below a threshold over at least N runs, e.g. N≥5 (minimum-support guard).
  - Never let skill content carry permission grants. Each step still runs through the normal tool-permission checks, so a skill can't bypass them.
- Dry runs: Nova could "explain the plan" (list steps and postconditions) without executing, or execute against non-destructive targets. No source measured this for desktop assistants.

### Gaps
- Several 2026 papers (Library Drift, SkillForge, D2Skill, 2608.07955) were seen only through search summaries; their numbers are unverified against full text.
- No primary Anthropic doc on skill-creator evals was retrieved.

## Incremental edit formats: patch operations vs full rewrite, and what LLMs do reliably

### Takeaway
Full rewrites risk information loss ("context collapse") and laziness, where the model elides content. Itemized or patch edits, such as exact search/replace, line insert or delta bullets merged by code, preserve content and are auditable. Search/replace is the best default for strong models. Exact-match uniqueness checks (as in Letta's memory_replace) catch bad patches deterministically.

### Cited Findings
- Aider: GPT-4 Turbo scored 20% with SEARCH/REPLACE blocks and 61% with a simplified unified-diff format; tasks with "lazy" placeholder comments dropped from 12 to 4. June GPT-4 went from 26% to 59%. Earlier Aider benchmarks found whole-file edits most reliable for GPT-3.5/GPT-4-era models, but slow and costly for large files. Function-call (JSON) edit formats did worse than whole-file. — [Aider unified diffs](https://aider.chat/docs/unified-diffs.html); [Aider benchmarks](https://aider.chat/docs/benchmarks.html); [Aider edit formats](https://aider.chat/docs/more/edit-formats.html)
- Diff-XYZ (arXiv 2510.12487): search/replace performs best for stronger models, while udiff variants are competitive. Smaller open models stay weak regardless of format, so the best format depends on the model. — [Diff-XYZ, arXiv 2510.12487](https://arxiv.org/html/2510.12487v2)
- ACE's context collapse case (18k→122 tokens; 66.7%→57.1%) is direct evidence against monolithic LLM rewrites of procedural memory. Its fix: the LLM emits only delta items and deterministic code merges them. — [ACE](https://www.emergentmind.com/papers/2510.04618)
- Letta's `memory_replace` requires an exact, unique match and errors otherwise. It also keeps `memory_rethink` for intentional full rewrites. — [Letta docs](https://docs.letta.com/guides/agents/base-tools)
- Mem0 and ExpeL show LLMs can reliably choose among a small, closed set of operations (ADD/UPDATE/DELETE/NOOP; ADD/EDIT/UPVOTE/DOWNVOTE) through tool calls. — [Mem0](https://arxiv.org/abs/2504.19413); [ExpeL](https://arxiv.org/abs/2308.10144)

### Inferences
- For Nova: represent a skill as a list of steps with stable IDs and offer these patch operations as tool calls:
  - `insert_step(after_id, step)`
  - `replace_step(step_id, new_step)` or `replace_text(step_id, old, new)` with a uniqueness check
  - `delete_step(step_id)`
  - `update_postcondition(step_id, ...)`
  - `set_note(skill, text)`
- Validate each patch: schema check, the step must reference existing tools, no permission fields. Write it as a new version, and keep full rewrite as an explicit, rarer operation that needs confirmation.
- Smaller local fallback models (6 GB GPU) are likely weaker at diff formats. Structured JSON operations on step IDs avoid line-number and diff syntax and suit them better. This is an inference; per Aider, older-model JSON edits underperformed for code, but step-level operations are much simpler than code diffs.

### Gaps
- I found no benchmark comparing patch-operation formats specifically for natural-language procedures/skills, as opposed to code. The evidence comes from code editing (Aider, Diff-XYZ) and context engineering (ACE).
