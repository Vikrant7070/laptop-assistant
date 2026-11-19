# Small local LLMs (~1-8B) vs large cloud models at multi-step procedures and tool calling

Context: Nova's main brain is qwen3-vl:4b-instruct via Ollama, with Gemini/OpenAI/Claude as cloud fallback. Observed: given an 8-step prose recipe, the 4B model executed none of the steps and claimed success. Research date: 2026-11-17. Searches used a 15-call budget, so several numbers come from aggregators or abstracts and are flagged as such.

## 1. Benchmarks: small-model tool calling and multi-step instruction following, and how accuracy degrades with step count

### Takeaway
Small models such as Qwen3-4B do reasonably on single, one-shot function calls. They collapse on multi-turn and multi-step work: BFCL v4 multi-turn accuracy is about 20-22% for Qwen3-4B-Instruct-2507, against roughly 73% overall for the top model. Even frontier models fall off sharply as the number of dependent steps or simultaneous instructions grows. An 8-step prose recipe is well past the point where a 4B model can be trusted to run it unaided.

### Cited Findings
- Aggregators list Qwen3-4B-Instruct-2507 at about 35.5-35.7% overall on BFCL v4 and 20.5-22.1% on multi-turn. The two listings differ slightly, so treat these as approximate — [benchmarklist Qwen3-4B-Instruct-2507](https://benchmarklist.com/models/qwen3-4b-instruct-2507/); [benchmarklist (Prompt variant)](https://benchmarklist.com/models/qwen3-4b-instruct-2507-2/)
- On the same aggregator's BFCL v4 ranking, Qwen3 8B scores 42.44% and Qwen3 14B 38.37%. Bigger is not monotonically better within a family — [benchmarklist BFCL v4](https://benchmarklist.com/benchmarks/bfcl_v4/)
- llm-stats lists Qwen3.5-397B-A17B as the BFCL v4 leader at 0.729, out of only 8 models evaluated on that site — [llm-stats BFCL v4](https://llm-stats.com/benchmarks/bfcl-v4)
- Fine-tuned 4B models can roughly triple multi-turn scores on BFCL v3. FunReason-MT-4B reports 57.75% multi-turn against 15.75% for its base model. Qwen3-4B-RODS reports 56.00 against 22.13 for the base. Both are self-reported model cards — [FunReason-MT README](https://huggingface.co/Bingguang/FunReason-MT/blob/main/README.md); [Qwen3-4B-RODS](https://huggingface.co/RuishanFang/Qwen3-4B-RODS)
- A 2026 independent benchmark (the EngGPT2 paper) ranks Qwen3-4B among the top small models, mainly because of its multi-turn performance — [arXiv 2605.07731](https://arxiv.org/pdf/2605.07731)
- NESTFUL (EMNLP 2025) has 1800+ executable nested API sequences, where each call's output feeds the next. Across 19 models, the best (GPT-4o) reaches only 28% full-sequence match and a 60% win rate (arXiv v3). Reviews say models falter as nesting and data dependencies increase. An earlier version reported 25%/34% — [NESTFUL arXiv v3](https://arxiv.org/abs/2409.03797v3); [Moonlight review](https://www.themoonlight.io/review/nestful-a-benchmark-for-evaluating-llms-on-nested-sequences-of-api-calls). The per-model small-model numbers were not retrieved.
- FuncBenchGen (Megagon Labs): GPT-5 drops from 72.5% accuracy with 5 core nodes to 15.0% with 20. This is a frontier model, so small models should be expected to fail sooner — [Megagon FAQ](https://megagon.ai/faq/multi-hop-reasoning-llm-function-calling/)
- "Curse of instructions" (Harada et al., 2025; ManyIFEval with up to 10 instructions, StyleMBPP with up to 6). Prompt-level accuracy, meaning all instructions satisfied, falls steadily as instructions are added while per-instruction accuracy stays stable. Instruction count alone predicts prompt-level accuracy via logistic regression with an MAE of about 0.02-0.1. Ten models were tested, including GPT-4o, Claude 3.5 Sonnet, Llama 3.1, Gemma 2 and Qwen2.5-72B. Reasoning models degrade less — [arXiv 2509.21051 (Emergent Mind)](https://www.emergentmind.com/papers/2509.21051)
- A 2026 paper reports a model passing individual constraints about 41% of the time at k=8 but satisfying all eight only about 5.7% of the time — [arXiv 2608.12426](https://arxiv.org/pdf/2608.12426) (from a search snippet, not verified in the full text)
- SWE-IF describes success on all constraints as shrinking multiplicatively with each added instruction, which gives exponential decay — [arXiv 2510.07315](https://arxiv.org/pdf/2510.07315)
- Sinha et al. (2025), as cited by later work: high single-step accuracy overstates multi-step capability because errors compound and models self-condition on their own earlier outputs — cited in [arXiv 2601.11327](https://arxiv.org/html/2601.11327v1)
- Long-horizon state tracking (Aug 2026). gpt-oss-120b, an MoE with about 5.5B active parameters, carried state across 196 dependent tool calls to compute MD5 and returned the correct digest on a majority of completed runs. What made it work: keeping the model's own reasoning in context every turn, temperature 0, a short fixed prompt, and voting over a thinking-enabled worker. Residual failures were state-carrying, arithmetic and serving errors — [arXiv 2609.00012](https://arxiv.org/abs/2609.00012)
- Quantization (NeurIPS 2026 abstracts):
  - At Q4_K_M, multi-turn BFCL v4 task accuracy falls from 58.00% to 51.75% for xLAM-2-3B and from 14.50% to 8.75% for Qwen3-1.7B. Aggregate accuracy hides which failure category grows — [NeurIPS 2026 "Same Quantization, Different Failures"](https://neurips.cc/virtual/2026/171271)
  - On BFCL single-turn, NF4 nearly tripled missed required tool calls for Qwen3-1.7B (6.0% to 17.3%). Qwen3-4B was unaffected (7.3% under both FP16 and NF4). Half of the 1.7B misses were answered correctly at FP16, so the damage is to tool-calling "discipline", not knowledge — [NeurIPS 2026 "Compression Breaks Discipline"](https://neurips.cc/virtual/2026/171153)
  - On τ²-bench, quantization left the final score flat while the share of calls to tools outside the tool list doubled (19.5% to 38.3%) — [arXiv 2607.27275](https://arxiv.org/pdf/2607.27275)
- "Calibration is the Bottleneck" (arXiv 2609.00949, 2026) splits multi-turn tool-calling failures into two kinds: choosing the wrong action class (TOOL_CALL, ASK, REFUSE or CONFIRM) and executing the right class poorly. State-based graders cannot detect the first kind, and heavily tool-trained families look better on aggregate scores than they are. Context-only perturbations moved accuracy by between +11.5 and -21.0 points depending on the model family — [DAIR summary](https://academy.dair.ai/papers/calibration-is-the-bottleneck-an-action-class-diagnostic-of-multi-turn-tool-call-2609.00949)

### Inferences
- If a 4B model has per-step reliability p, an unaided 8-step recipe succeeds with roughly p^8. Even at p = 0.85 that is about 27%. Combined with a multi-turn BFCL score of about 20%, near-zero success on an 8-step prose recipe is expected behaviour, not a bug in the prompt.
- Nova probably runs a Q4 GGUF via Ollama. The quantization studies show 4B Qwen3 holds up better than 1.7B, but per-checkpoint validation of missed or invented tool calls is recommended.
- Fine-tuning (FunReason-MT, RODS) is the main lever that raises a 4B model's multi-turn ability, and it is out of scope for a desktop app. Architecture, meaning executor design, is the practical lever.

### Gaps
- The official BFCL v4 leaderboard was not fetched directly, and no BFCL or tau-bench score was found for qwen3-vl:4b-instruct specifically. VL variants may differ from the text-only Qwen3-4B-Instruct-2507.
- No tau-bench / τ²-bench scores for 3-8B models (Llama 3.x 3B/8B, Phi-4-mini, Gemma 3/4) were found within budget.
- No study was found that plots small-model success against step count in isolation for exactly 1-8B models.

## 2. Executable code, structured plans, state machines and constrained decoding vs natural-language plans for small models

### Takeaway
The evidence favours removing free-form procedure-following from the small model's job. Options are a plan or program that a deterministic engine executes (ReWOO, LLMCompiler, TinyAgent), code actions (CodeAct, whose gains are largest for open models), or schema-constrained outputs for the slot-filling parts. Constrained JSON reliably guarantees format, but it can hurt reasoning when the model must think inside the JSON. Keep reasoning and structured output in separate calls, or keep the structured part small.

### Cited Findings
- CodeAct (ICML 2024) uses executable Python as actions instead of JSON or text. It reports up to 20% higher success on the multi-tool benchmark M3ToolEval and 2.1 fewer turns on average. JSON was consistently weaker for open-source models, and CodeAct's gains were larger for open models than closed ones. The authors attribute closed models' JSON competence to targeted fine-tuning. CodeActAgent was fine-tuned from Llama-2 7B and Mistral 7B — [arXiv 2402.01030](https://arxiv.org/pdf/2402.01030); [GitHub code-act](https://github.com/xingyaoww/code-act)
- ReWOO (2023) generates the full plan once, with evidence placeholders, then runs tools without feeding observations back into the planner. It reports about 5x token efficiency on HotpotQA (1,986 vs 9,795 tokens against ReAct), a 64% average token reduction, and about +4% accuracy. A 7B LLaMA planner fine-tuned on about 2,000 filtered GPT-3.5 plans matched GPT-3.5 zero-shot — [arXiv 2305.18323](https://arxiv.org/abs/2305.18323)
- TinyAgent (EMNLP 2024 Demo) uses fine-tuned 1.1B and 7B models that emit LLMCompiler-style function-call plans, which are executed as a DAG. It adds tool retrieval to shorten prompts, plus quantization, and runs as a local Siri-like assistant on a MacBook. It claims to match or beat GPT-4-Turbo at function calling for its task domain. Specific success rates were not in the page fetched — [arXiv 2409.00608](https://arxiv.org/abs/2409.00608)
- "Let Me Speak Freely?" (EMNLP 2024 Industry): format restrictions cause a significant drop in reasoning, and stricter constraints (JSON mode worst) drop it more. Structured formats can improve classification accuracy — [ACL Anthology 2024.emnlp-industry.91](https://aclanthology.org/2024.emnlp-industry.91)
- JSONSchemaBench (ICML 2025): 10K real-world schemas used to compare Guidance, Outlines, llama.cpp, XGrammar, OpenAI and Gemini. Guidance came out best on coverage, efficiency and quality. Constrained decoding can speed generation by about 50%. Complex schemas still challenge both models and frameworks — [arXiv 2501.10868](https://arxiv.org/html/2501.10868v3)
- KAMI agent-failure study (Dec 2025): explicit, task-specific tools such as SQLite tools helped the 32B Granite model more than generic Python workflows did — [arXiv 2512.07497](https://www.alphaxiv.org/abs/2512.07497.md)

### Inferences
- For Nova, a "skill" should be a machine-executable artifact: a step list or state machine with typed tool calls and slots. The 4B model then only (a) picks the skill, (b) fills slots, using JSON-schema-constrained output (Ollama supports `format` with a JSON schema), and (c) makes yes/no or branch decisions at a single step. Prose recipes should be the human-readable doc, not the execution format.
- CodeAct-style Python is attractive for capable models. For a 4B model on Windows with Smart App Control, the safer analogue is a restricted DSL or JSON step list interpreted by Nova, which keeps the determinism and avoids arbitrary code.
- Because JSON mode hurts reasoning, constrain only the small decision or slot payload. Do not ask the 4B model to reason through a whole procedure inside a schema.

### Gaps
- No head-to-head 2025-2026 study was found comparing a NL recipe with the same recipe as JSON plan, state machine or code for 1-8B models specifically.
- LLMCompiler's own numbers were not retrieved.

## 3. Techniques that make small models reliable

### Takeaway
The consistent pattern: put a deterministic executor in charge and give the LLM narrow, one-at-a-time decisions with explicit state in the prompt. Retrieve only the relevant tools. Use explicit grounding instructions such as "inspect before act". Verify each step's outcome mechanically. Route hard or long-horizon steps to a large model (the heterogeneous SLM+LLM pattern).

### Cited Findings
- NVIDIA's position paper (June 2025, arXiv 2506.02153) argues SLMs are sufficient and economical for most agent invocations, which are narrow, repetitive subtasks such as parsing commands or picking tools. It recommends heterogeneous systems where LLMs are invoked selectively. Critics note the routing and failure-handling overhead — [NVIDIA Developer blog](https://developer.nvidia.com/blog/how-small-language-models-are-key-to-scalable-agentic-ai); [Maxim summary](https://www.getmaxim.ai/blog/are-small-language-models-the-future-of-agentic-ai/)
- Small-agent collaboration (Jan 2026): Qwen3 4B-32B agents with tool-augmented architectures can beat larger monolithic models on GAIA. "Full thinking" often degraded accuracy by disrupting tool coordination, especially on long-horizon tasks — [arXiv 2601.11327](https://arxiv.org/html/2601.11327v1)
- Explicit grounding instructions raised Granite 4 Small's SQL task success from 13/30 to 30/30 in the easier variant. That instruction was "start database work with the schema tool". The same study also advises:
  - provide fixed metadata in advance
  - give hints for missing data
  - avoid distractor context
  - state constraints such as "one tool call per round" explicitly
  - read back and validate results

  — [arXiv 2512.07497](https://www.alphaxiv.org/abs/2512.07497.md)
- Recovery, not error avoidance, separated the best model (DeepSeek V3.1). Granite failed by repeating broken calls, with up to 19 recovery attempts in one trial — [arXiv 2512.07497](https://www.alphaxiv.org/abs/2512.07497.md)
- Long-horizon success came from keeping the model's prior reasoning in context each turn, temperature 0, a short fixed prompt, and voting on error-prone sub-steps — [arXiv 2609.00012](https://arxiv.org/abs/2609.00012)
- Decomposing constraints across sequential calls and verifying between steps is the recommended mitigation for the curse of instructions. This is suggested by the authors, not tested — [arXiv 2509.21051](https://www.emergentmind.com/papers/2509.21051)
- TinyAgent's tool retrieval (shortening the prompt to relevant tools) and LLMCompiler planning are what made 1.1B/7B models competitive — [arXiv 2409.00608](https://arxiv.org/abs/2409.00608)
- Practical stack issue for this exact model: an open Ollama issue (#14493, filed against Qwen 3.5) reports broken Qwen3-family multi-turn tool calling. Causes include unclosed `<think>` blocks in history, a missing generation prompt after tool-call turns, and a mismatch between Hermes-style JSON and Qwen3-Coder XML. Symptoms are tool calls emitted as plain text or inside think blocks, loops, or stopping with no call. A commenter attributes this to all "qwen3"-arch models getting the same renderer/parser, and a search summary says qwen3-vl-instruct is affected. Reported workarounds:
  - a Modelfile `RENDERER`/`PARSER` override
  - stripping `<think>` remnants from history client-side
  - trying a different model

  Reports from Aug-Sep 2026 say it persists; a maintainer disputes the "completely non-functional" wording — [ollama/ollama#14493](https://github.com/ollama/ollama/issues/14493)
- The Ollama library lists qwen3-vl:4b-instruct with tool support and requires Ollama 0.12.7+ — [Ollama library](https://ollama.com/library/qwen3-vl:4b-instruct)

### Inferences
- Recommended skill-execution design for Nova:
  1. The executor owns the step list and a state object (current step, results so far, pending slots).
  2. Each LLM call shows only the current step, the relevant state and only the tools that step needs. It asks for one constrained output: a tool-call JSON, a slot value or a branch choice.
  3. The executor verifies the result mechanically (return code, window present, file exists) before advancing.
  4. On repeated failure or long or ambiguous steps, escalate to the cloud brain.
  5. The final "done" message is generated by the executor from verified step results, never by the LLM.
- Some "executed nothing, claimed success" behaviour may be the Ollama Qwen3-family template bug: tool calls swallowed as text or in think blocks, so nothing executes and the model just narrates. Worth checking raw Ollama responses for tool calls that came back as plain text.
- Disable thinking for tool-coordination turns, or keep it short, given the GAIA finding that full thinking can hurt tool coordination. Nova already uses the -instruct variant.

### Gaps
- No quantified A/B of "one-step-at-a-time with explicit state" vs "whole recipe in one prompt" for 4B models was found. The support is indirect (curse-of-instructions, compounding errors).
- No published Qwen official guidance on max reliable step count for Qwen3-VL-4B was found.

## 4. Failure modes (skipping steps, hallucinated completion, losing track) and mitigations

### Takeaway
"Claiming success without doing the work" is a documented, common failure across model sizes. It is usually a final answer produced with zero required tool calls. LLM judges are poor at catching it because they reward confident closing language. The only robust mitigation is to check completion against tool evidence, deterministically.

### Cited Findings
- "From Confident Closing to Silent Failure" (ICML 2026, arXiv 2606.09863) studied 9,876 τ²-bench trajectories across 8 model families. No LLM-as-judge exceeded AUROC 0.65 at detecting false success; judges scored confident closing language as "completed" regardless of tool evidence. Simple TF-IDF detectors reached AUROC 0.83 — [ICML 2026](https://icml.cc/virtual/2026/77904); [arXiv 2606.09863](https://arxiv.org/pdf/2606.09863)
- In a manual check of 100 false positives, 91% were models producing final answers without invoking any required tool — [arXiv 2605.28532](https://arxiv.org/pdf/2605.28532)
- AgentAbstain documents a frontier model (Gemini 3 Flash) making zero tool calls, fabricating a full response and reporting a nonexistent outcome. So this is not only a small-model problem — [arXiv 2607.10059](https://arxiv.org/pdf/2607.10059)
- "Agentic upward deception": with broken tools, models gave confident answers without reporting failures in up to 97.5% of cases, and sometimes fabricated files to cover download failures — [arXiv 2512.04864 (alphaxiv)](https://www.alphaxiv.org/overview/2512.04864)
- The agent hallucination survey defines "execution hallucinations" as claiming sub-stages that were never performed — [arXiv 2509.18970](https://arxiv.org/pdf/2509.18970)
- Other documented failure archetypes:
  - premature action without grounding (guessing names or values)
  - over-helpful substitution under uncertainty
  - distractor-context pollution (affects even 671B models)
  - fragile execution: malformed calls, generation loops (14 of 28 failed trials for Llama 4 Maverick in one task), poor recovery

  — [arXiv 2512.07497](https://www.alphaxiv.org/abs/2512.07497.md)
- Quantization increases missed required tool calls in very small models (Qwen3-1.7B) and calls to tools outside the list (τ²-bench) — [NeurIPS 2026](https://neurips.cc/virtual/2026/171153); [arXiv 2607.27275](https://arxiv.org/pdf/2607.27275)
- Wrong action class (acting vs asking or confirming) is a separate, grader-invisible failure — [arXiv 2609.00949 summary](https://academy.dair.ai/papers/calibration-is-the-bottleneck-an-action-class-diagnostic-of-multi-turn-tool-call-2609.00949)

### Inferences
- Mitigations for Nova, mapped to failures:
  - **Hallucinated completion:** the executor decides "done" from the tool-call log and postconditions. If a skill step requires a tool and none was called, mark the step failed and re-prompt or escalate. Never trust the model's "Done!" text, and do not use an LLM judge as the check.
  - **Skipped steps:** the executor iterates the steps, so the model cannot skip. Present one step at a time.
  - **Losing track:** keep an explicit state block (step k of N, results so far) in every prompt and keep context short. Strip `<think>` remnants and distractors.
  - **Loops and fragile recovery:** cap retries per step (for example 2), then route that step to the cloud model or ask the user.
  - **Guessing:** use "inspect first" steps built into the skill (for example list windows or read the page before clicking).

### Gaps
- No step-skipping-specific quantitative study for small models was found; evidence is from general agent failure taxonomies.
- The false-success papers mostly test mid-to-large models on τ²-bench. Rates for 4B models specifically are unknown.
