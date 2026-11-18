# Agent Skill Libraries: How LLM Agents Learn and Build Reusable Skills from Experience

Research date: 2026-11-17. Scope: research systems where agents write their own reusable skills or workflows, how those skills are represented, induced, verified and composed, the gains reported, record-and-replay and demonstration approaches, and failure modes. Purpose: inform skill creation from Nova's own successful runs. Nova is a Windows desktop voice assistant.

Note on verification: I fetched the arXiv abstract or HTML page for Voyager, ExpeL, Synapse, AWM, ASI, SkillWeaver, AppAgentX, UFO2, OS-Copilot, AgentRR, GPA, 2609.04869, 2609.17653 and the 2607.10113 survey in this session. Claims about JARVIS-1, Cradle, AppAgent and UFO v1 come from background knowledge of those papers and were not re-fetched. Those claims are marked [not re-verified].

## 1. Core systems: skill representation, induction, verification, composition, gains

### Takeaway
The field has moved from free-text "insights" (ExpeL, 2023), through retrieved raw trajectories (Synapse, 2023) and abstract natural-language workflows (AWM, 2024), to verified executable programs (Voyager 2023, then SkillWeaver and ASI in 2025). By 2026 there is a further shift to file-based SKILL.md packages that evolve online with gated edits and version control. The most reliable gains come from skills induced only from successful runs (judged by an LLM or the environment) and re-executed before they are added. On WebArena, verified programmatic skills beat text workflows: ASI 40.4% vs AWM 36.3% vs vanilla 32.7% with Claude-3.5-Sonnet.

### Cited Findings

**Voyager (May 2023; Minecraft; code-as-skills)**
- Voyager keeps an "ever-growing skill library of executable code" for storing and retrieving complex behaviors. The authors describe the skills as temporally extended, interpretable and compositional, which lets abilities compound and reduces catastrophic forgetting. — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291)
- Its iterative prompting refines programs using environment feedback, execution errors and self-verification. A skill enters the library only after the program works. — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291)
- Compared with prior SOTA, Voyager gets 3.3x more unique items, travels 2.3x longer distances, and unlocks tech-tree milestones up to 15.3x faster. Submitted 25 May 2023. — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291)

**ExpeL (Aug 2023, AAAI-24; NL insights plus stored trajectories)**
- ExpeL gathers experiences on training tasks and extracts natural-language knowledge without updating weights. At inference it recalls those insights and similar past experiences. Performance "consistently" improves as experience accumulates, and the paper reports transfer-learning experiments. — [arXiv 2308.10144](https://arxiv.org/abs/2308.10144)
- The benchmarks (HotpotQA, ALFWorld, WebShop) and absolute numbers were not on the abstract page I fetched (see Gaps).

**Synapse (Jun 2023, ICLR 2024; trajectory-as-exemplar)**
- Synapse has three parts. State abstraction strips task-irrelevant details from raw states. Trajectory-as-exemplar prompting supplies full abstracted state-action trajectories. An exemplar memory retrieves trajectories by embedding similarity. — [arXiv 2306.07863](https://arxiv.org/abs/2306.07863)
- On MiniWoB++ it reaches 99.2% average success over 64 tasks using demonstrations from only 48 tasks, a 10% relative improvement. It was the first in-context method to solve book-flight. On Mind2Web it improves average step success by 56% relative to the previous SOTA prompting approach. — [arXiv 2306.07863](https://arxiv.org/abs/2306.07863)

**Agent Workflow Memory, AWM (Sep 2024; abstract NL workflows)**
- AWM induces commonly reused routines ("workflows") and selectively supplies them to guide later actions. The offline mode induces from training examples. The online mode induces on the fly from test queries, using the agent's own judged-successful runs. — [arXiv 2409.07429](https://arxiv.org/abs/2409.07429)
- It reports relative success-rate gains of 24.6% on Mind2Web and 51.1% on WebArena, with fewer steps on WebArena. As the train-test gap widens across tasks, websites and domains, online AWM improves absolute performance by 8.9 to 14.0 points. Submitted 11 Sep 2024. — [arXiv 2409.07429](https://arxiv.org/abs/2409.07429)

**Agent Skill Induction, ASI (Apr 2025, revised Aug 2025; programmatic skills, online)**
- Skills are Python functions with Args/Returns/Examples docstrings, induced online on WebArena. Example: `search_product(name)` chains click and fill. The inducer prompt limits skills to 2-5 steps, simple arguments and no try/except. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- Induction uses only episodes an LLM evaluator judges correct. Steps that caused execution errors are removed, and each thought is condensed to one sentence (87.9 down to 13.4 tokens per step). — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- Verification has four steps:
  1. Rewrite the trajectory so sub-sequences become calls to the new skills.
  2. Truncate primitive actions after the last skill call, which prevents spurious passes.
  3. Re-run the query from that prefix.
  4. Accept only if the run is judged correct, calls at least one new skill, and every skill call changes the environment.
  — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- WebArena results with Claude-3.5-Sonnet (Table 1):

  | Method | Success rate | Steps |
  |---|---|---|
  | Vanilla | 32.7% | 5.6 |
  | AWM (text workflows) | 36.3% | 5.9 |
  | ASI (programs) | 40.4% | 5.0 |

  The abstract states +23.5% success vs the static baseline, +11.3% vs the text-skill counterpart, and 10.7-15.3% fewer steps. — [arXiv 2504.06821](https://arxiv.org/abs/2504.06821); [HTML](https://arxiv.org/html/2504.06821v2)
- On scaled-up tasks, ASI beats vanilla by 38.9% and AWM by 20.7% (relative). Per-site ASI success is shopping 100, admin 91, Reddit 55, GitLab 55, map 100. Vanilla gets 41.7, 58.0, 33.3, 33.3 and 40.0. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- Cross-website transfer (shopping / Reddit / map, success rate):

  | Method | Shopping | Reddit | Map |
  |---|---|---|---|
  | Vanilla | 80 | 40 | 63.3 |
  | AWM | 80 | 56.7 | 100 |
  | ASI | 90 | 76.7 | 93.3 |

  ASI needs fewer steps on all three sites. The success-rate gap vs AWM is not significant (p=0.167); only the step reduction is. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- Reuse: at least one skill was reused in 42.5% of cases, and 7.6% of tasks produced new skills. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)

**SkillWeaver (Apr 2025; self-discovered Playwright APIs via exploration)**
- SkillWeaver runs in three stages:
  1. **Propose.** An LLM curriculum looks at screenshots, URLs and accessibility trees and proposes short skills the agent does not yet have. They fall into three types: procedural, navigational and information-seeking. Safety instructions discourage actions such as creating accounts or contacting users.
  2. **Synthesize.** The agent practices each skill, and an LLM reward model judges success. Successful trajectories become Python Playwright functions with a signature and a docstring that includes a usage log and the required page state. Static checks trigger regeneration on common mistakes.
  3. **Hone.** APIs that take only the page argument run as unit tests. For others, the LLM generates test inputs. Failing APIs are debugged.
  — [arXiv 2504.07079 HTML](https://arxiv.org/html/2504.07079v1)
- WebArena with GPT-4o: 22.6% to 29.8% average. Per site: Reddit 37.7 to 50.0, Shopping 19.8 to 27.2, CMS 18.7 to 25.8, Map 27.5 to 33.9, GitLab 17.8 to 22.2. GPT-4o-mini: 9.2% to 14.1%. On real-world sites (an Online-Mind2Web subset): 40.2% to 56.2%, with Flights 11.7 to 29.4 and Cars unchanged at 11.1. The abstract reports +31.8% and +39.8% relative. — [arXiv 2504.07079](https://arxiv.org/abs/2504.07079); [HTML](https://arxiv.org/html/2504.07079v1)
- APIs synthesized by GPT-4o raised GPT-4o-mini by up to 54.3% on WebArena (abstract), and by about 40-133% across sites in the text. With these APIs, GPT-4o-mini beat the GPT-4o baseline on Map, Shopping and Reddit. The paper's relative figures are inconsistent across sections; the absolute numbers are more reliable. — [arXiv 2504.07079 HTML](https://arxiv.org/html/2504.07079v1)
- Synthesized APIs roughly matched human-written APIs on sites with low or medium API support, but did worse on high-support sites (GitLab, Maps). — [arXiv 2504.07079 HTML](https://arxiv.org/html/2504.07079v1)

**OS-Copilot / FRIDAY (Feb 2024; OS-wide self-improving agent)**
- FRIDAY is a self-improving OS agent that reuses "accumulated skills from previous tasks" to generalize to unseen apps. It scores 35% higher than prior methods on GAIA. The authors report quantitative evidence of self-learned control of Excel and PowerPoint with minimal supervision. Submitted 12 Feb 2024. — [arXiv 2402.07456](https://arxiv.org/abs/2402.07456)
- [not re-verified] FRIDAY's tools are Python tool code generated by a tool generator, kept in a tool repository, and retrieved for later tasks. — [arXiv 2402.07456](https://arxiv.org/abs/2402.07456)

**AppAgent (Dec 2023) and AppAgentX (Mar 2025; Android)**
- [not re-verified] AppAgent explores an app, either autonomously or by watching a human demonstration, and writes NL documentation of what each UI element does. That documentation is retrieved at task time. — [arXiv 2312.13771](https://arxiv.org/abs/2312.13771)
- AppAgentX records task-execution history in memory, finds repeated action sequences, and "evolves high-level actions that act as shortcuts" replacing low-level operations. It claims gains in both efficiency and accuracy (the abstract gives no figures). Submitted 4 Mar 2025, v3 15 Apr 2025. — [arXiv 2503.02268](https://arxiv.org/abs/2503.02268)

**UFO (Feb 2024) and UFO2 (Apr 2025; Windows)**
- UFO2 is a Windows "Desktop AgentOS". A HostAgent decomposes tasks, and per-app AppAgents act through a unified GUI-API action layer. Control detection combines Windows UI Automation with vision parsing. Speculative multi-action planning cuts LLM calls per step. Evaluated on more than 20 Windows apps; the abstract gives no figures. Submitted 20 Apr 2025. — [arXiv 2504.14603](https://arxiv.org/abs/2504.14603)
- [not re-verified] UFO/UFO2 docs describe a knowledge substrate: RAG over app help documents, user demonstrations recorded with Windows Step Recorder, and the agent's own past successful execution logs ("self-experience"). This is the closest Windows-native precedent for Nova. — [UFO GitHub/docs](https://github.com/microsoft/UFO)

**JARVIS-1 (Nov 2023) and Cradle (Mar 2024)** [not re-verified]
- JARVIS-1 (Minecraft) stores successful plans with their multimodal situation in a multimodal memory and retrieves them for new tasks. Its performance improves through lifelong self-improvement. — [arXiv 2311.05997](https://arxiv.org/abs/2311.05997)
- Cradle (general computer control through screenshots and keyboard/mouse, demonstrated in RDR2 and software apps) has a skill-curation module. It generates code-form skills, such as keyboard and mouse sequences, from observed tutorials or prompts and stores them for reuse. — [arXiv 2403.03186](https://arxiv.org/abs/2403.03186)

**2025-2026 successors**
- **Online skill evolution for computer-use agents (Hu, Liang, Zhu; 4 Sep 2026)**
  - Representation: skills are SKILL.md files. Only name and description go into the executor's catalog, and the full text is loaded on demand via `get_skill(name, reasoning)`. Skills are guidance only and are not executable.
  - Pipeline: an Extractor turns trajectory, screenshots, the official evaluator score and skill telemetry into facts. A Proposer chooses create, edit, delete, no-op or unresolved. A Builder writes a full replacement file.
  - Gates: a rule-based Coordinator requires 5 observations before creating a skill. It downgrades duplicates and oversized edits to no-op.
  - Versioning: each change is a Git commit tied to the task that caused it.
  - Results: OSWorld, post-warm-up, full vs empty library — GIMP 80.2 vs 74.4, VLC 64.0 vs 45.4, Writer 63.9 vs 52.0 (but Writer already led by 9.1pp in warm-up), Thunderbird 74.7 vs 62.3. The final GIMP library had 27 skills. Each condition was run once, so results are descriptive only.
  — [arXiv 2609.04869](https://arxiv.org/html/2609.04869v1)
- **EvoSkill-GUI, "Reflect, Revise, Reuse" (15 Sep 2026; training-free)**
  - Loop: the executor revises during a rollout; an isolated critic diagnoses failed trajectories; the executor edits skill files through a restricted tool interface.
  - Skill package contents: retrieval metadata, executable plans, backup localization, failure-recovery rules, accessibility utilities and failure cases.
  - Max gains: MobileWorld +16.2%, AndroidWorld +6.0%, OSWorld +10.5%. The evolved libraries carry over to related tasks.
  — [arXiv 2609.17653](https://arxiv.org/abs/2609.17653)
- Other 2025-2026 entries found by search but not read in detail:
  - SEAgent (Aug 2025): a self-evolving computer-use agent that learns through trial and error on an auto-generated curriculum. — [arXiv 2508.04700](https://arxiv.org/abs/2508.04700)
  - SAGE (Dec 2025): RL with a skill library and a skill-integrated reward. — [arXiv 2512.17102](https://arxiv.org/abs/2512.17102)
  - EvoSkills: multi-file skill packages with co-evolutionary verification. — [arXiv 2604.01687](https://arxiv.org/html/2604.01687v1)
  - SkillOS: learned skill curation. — [arXiv 2605.06614](https://arxiv.org/pdf/2605.06614)
  - SkillCoach: self-evolving rubrics for skill use. — [arXiv 2607.01874](https://arxiv.org/pdf/2607.01874)
- **Survey: "Dynamic Agent Skills" (Yubo Li, 11 Jul 2026, TMLR)**
  - Audits 124 papers from 2023-2026 and treats skill libraries as lifecycle-managed, verified, evolving artifact stores.
  - Eight lifecycle stages: evidence acquisition, proposal, verification/admission, storage, retrieval/composition, maintenance, distillation/portability, governance.
  - Skill forms it names: code functions, NL instructions, SKILL.md packages, workflow graphs, learned adapters.
  - Findings: admission and repair matter repeatedly; stale entries need repair or pruning; flat retrieval degrades as libraries grow; current benchmarks under-report library trajectories, usage-utility gaps and safety.
  — [arXiv 2607.10113](https://arxiv.org/abs/2607.10113)

### Inferences
- Induction triggers fall into three families:
  - Judged-successful own trajectories: AWM online, ASI, the 2609.04869 system, Nova's case.
  - Active exploration on a self-proposed curriculum: Voyager, SkillWeaver, SEAgent.
  - Human demonstration: AppAgent, UFO, the PbD systems in section 2.
  Nova's request ("create skills from its own successful runs") matches the first family most closely. ASI's pipeline (clean trajectory, then induce a parameterized function, then rewrite and re-execute, then admit) is the most directly transferable recipe.
- Composition in practice is shallow. Skills call primitives, and the agent chains skills at plan time; Voyager skills can call other skills. No system shows deep, reliable skill-of-skill hierarchies on GUI benchmarks.
- For Windows, UFO2's hybrid GUI-API layer suggests that a Nova skill should prefer API, COM or CLI steps where possible and fall back to UIA, then to vision.

### Gaps
- ExpeL's absolute benchmark numbers, and JARVIS-1 and Cradle quantitative results, were not fetched in this session.
- UFO2's absolute success rates, and the exact mechanics of its demonstration and self-experience RAG, were not confirmed from the abstract.
- AppAgentX's quantitative results and shortcut representation are not in its abstract.
- I found no peer-reviewed head-to-head of these systems on one Windows desktop benchmark (e.g. Windows Agent Arena).

## 2. Programming-by-demonstration and record-and-replay; programmatic vs text skills

### Takeaway
Recent PbD work converges on one pattern: record a trace, use an LLM at build time to turn it into a parameterized template with named variables, and replay it deterministically, falling back to the LLM when replay breaks. The best controlled evidence that executable skills beat text workflows is ASI (40.4% vs 36.3% on WebArena, plus fewer steps). The advantage is modest, and in cross-site transfer it is significant only for efficiency, not success.

### Cited Findings
- AgentRR (23 May 2025) applies classic record-and-replay to LLM agents.
  - It records the interaction trace and the decision process, then summarizes them into a structured "experience" through "multi-level experience abstraction".
  - A "check function" acts as a trust anchor for completeness and safety during replay.
  - Modes: user-recorded demonstrations, large-small model collaboration, privacy-aware execution.
  - The abstract gives no numbers.
  — [arXiv 2505.17716](https://arxiv.org/abs/2505.17716)
- GPA, "GUI Process Automation from Demonstrations" (Salesforce, 2 Apr 2026), replays a process from a single demo. Sequential Monte Carlo localization handles rescaling and detection uncertainty, which the authors present as a fix for classic RPA fragility. Execution is fully local and deterministic. In a pilot it reportedly had higher success than Gemini 3 Pro with CUA tools on long-horizon tasks and ran about 10x faster (no exact figures on the abstract). — [arXiv 2604.01676](https://arxiv.org/abs/2604.01676); [Salesforce blog](https://www.salesforce.com/blog/gpa-gui-process-automation/)
- Alloy (Oct 2025) turns a user's browser demonstration into editable, reusable agent workflows. In a user study against manual workflow building and a prompt-only agent, it was better at capturing intent and generalizing to structurally similar variants. — [arXiv 2510.10049](https://arxiv.org/html/2510.10049v1)
- ShowUI-Aloha records untrimmed human desktop use, turns it into structured traces, and plans from them. It reports 60.1% strict success on 361 tasks (from search snippet only, not fetched). — [arXiv 2601.07181](https://api.emergentmind.com/papers/2601.07181)
- Log2Plan pairs GUI automation with task mining from logs. — [arXiv 2509.22137](https://arxiv.org/html/2509.22137v1)
- OpenAI Codex "Record & Replay" turns a recorded macOS workflow into an editable Agent Skill. My only sources are third-party blog posts, so this is unverified against OpenAI docs. — [blog, Jun 2026](https://codex.danielvaughan.com/2026/06/19/codex-record-and-replay-demonstrated-workflows-reusable-agent-skills-macos/)
- Programs vs text, head to head: ASI 40.4% vs AWM 36.3% success, 5.0 vs 5.9 steps (WebArena, Claude-3.5-Sonnet). There is a caveat: verification is much stricter for programs. Only 15.6% of ASI turns produced a passing program, against 31.4% of AWM turns that added a text skill. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- The 2026 file-based systems, which store non-executable SKILL.md guidance, still report solid gains: up to +18.6pp on OSWorld VLC and up to +10.5% on OSWorld for EvoSkill-GUI. Text or procedural skills therefore remain viable for GUI desktop tasks, where grounding is the bottleneck. — [arXiv 2609.04869](https://arxiv.org/html/2609.04869v1); [arXiv 2609.17653](https://arxiv.org/abs/2609.17653)

### Inferences
- A hybrid design is probably best for Nova. Store each skill as a parameterized step program, using UIA, API or keyboard steps and slot variables such as `{recipient}` and `{file}`, together with an NL description and preconditions. Replay deterministically when the anchors match, and hand control back to the LLM at the failing step instead of aborting. EvoSkill-GUI's "backup localization" and "failure-recovery rules" fields fit this.
- Nova's existing app-first and UIA tooling makes program-form skills more feasible than for pixel-only agents. That matters because ASI's advantage depends on actions being reliably scriptable.

### Gaps
- I found no controlled study of record-and-replay macros vs induced LLM skills on a Windows desktop benchmark.
- GPA's and AgentRR's quantitative details were not on their abstract pages.

## 3. Failure modes: library bloat, overly specific skills, brittle replay, wrong generalization

### Takeaway
The documented failures are:
- skills hard-coded to page-specific IDs or constants;
- skills that break when another app or site implements the same function differently;
- the agent picking the wrong skill or passing bad arguments;
- edit churn on skills that never start working;
- retrieval that is not the same as use;
- flat retrieval degrading as the library grows.
The mitigations are strict admission (re-execution plus a judge), minimum-evidence thresholds, duplicate and edit-size gates, versioning with provenance, and on-demand loading.

### Cited Findings
- Over-specific skills: some ASI skills are tailored to page-specific IDs and constants, which limits reuse. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- Wrong generalization or incompatibility: a dropdown-based sort skill fails on a site where sorting opens a sidebar. ASI handles this by updating incompatible skills. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- Low admission yield: only 15.6% of ASI turns yield a passing program, so strict verification trades library growth for quality. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)
- Skill use errors in SkillWeaver:
  - The agent fails to choose the correct API.
  - It passes wrong parameters, e.g. a malformed search keyword that returns empty results.
  - Weaker models (GPT-4o-mini) are unreliable at API calling.
  - Some long-horizon skills exceed agent ability.
  - Visual-reasoning final steps were not helped: the Cars site was unchanged.
  - Exploring live sites is risky, so the authors needed guardrails and manual validation.
  — [arXiv 2504.07079 HTML](https://arxiv.org/html/2504.07079v1)
- Revision churn: `gimp-add-alpha-channel` received repeated accepted edits but succeeded in only 2 of 35 iterations (5.7%). The bottleneck was grounding, not the skill text. — [arXiv 2609.04869](https://arxiv.org/html/2609.04869v1)
- Retrieval is not adherence: 82.4% of GIMP rollouts called `get_skill`, but 43.3% of those calls fetched a skill from a different task, and telemetry cannot show whether the skill helped. — [arXiv 2609.04869](https://arxiv.org/html/2609.04869v1)
- Gains were non-monotonic, and the system had no automatic consolidation or rollback; serial evolution is order-dependent. — [arXiv 2609.04869](https://arxiv.org/html/2609.04869v1)
- Bloat controls in that system: a 5-observation creation threshold, duplicate creations downgraded to no-op, an edit-size limit, and progressive disclosure so the full library never enters context. — [arXiv 2609.04869](https://arxiv.org/html/2609.04869v1)
- Lifecycle view: stale entries need repair or pruning, flat retrieval degrades with library growth, and verifier quality materially affects outcomes. — [arXiv 2607.10113](https://arxiv.org/abs/2607.10113)
- Brittle replay: AgentRR notes that overly concrete experiences hurt reuse when the application changes (search summary). GPA targets RPA fragility under rescaling and detection uncertainty. — [arXiv 2505.17716](https://arxiv.org/abs/2505.17716); [arXiv 2604.01676](https://arxiv.org/abs/2604.01676)
- Spurious verification: ASI truncates trailing primitive actions before re-running. Otherwise a final primitive step (e.g. "send message") could make a run pass without the skill doing the work. — [arXiv 2504.06821 HTML](https://arxiv.org/html/2504.06821v2)

### Inferences (design implications for Nova)
1. **Induce only from verified successes.** Use explicit user confirmation ("that worked") or a checkable end state, not the LLM's self-report alone.
2. **Clean before inducing.** Drop failed or erroring steps, then generalize literals into named parameters. Reject skills that still contain window handles, coordinates or session-specific IDs.
3. **Admit only after re-execution.** Re-run the task with the skill in a dry or safe mode, and require that every skill step changes state. Keep side-effectful steps (send, delete, pay) behind confirmation even inside skills, consistent with Nova's existing confirmation rules.
4. **Gate growth.**
   - Require 2 or more successful observations before promoting a skill (the 2026 system uses 5).
   - Deduplicate by name and description similarity.
   - Track per-skill use and success counts, and prune or flag skills with low success. That avoids churn on skills like the GIMP alpha-channel case.
5. **Store on disk, load on demand.** Keep a one-line catalog in the prompt and the full body on disk, with a Git-style version history and provenance.
6. **Make replay fall back, not fail.** Deterministic replay should hand control back to the LLM at the failing step, not abort.

### Gaps
- I found no quantitative study of library-size scaling, e.g. retrieval accuracy against number of skills, among the sources fetched. The survey asserts the degradation but gives no numbers on its abstract page.
- I found no published Windows-specific skill-library failure analysis.
