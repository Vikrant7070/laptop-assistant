# Skill / Intent Routing Without False Positives (for a voice assistant with loose triggers + fuzzy embeddings)

Research date: 2026-11-17. Problem examples: "where you find out?" fires web-research; "check the latest anime" fires a fullscreen-Crunchyroll setup skill.

## 1. How Alexa, Google (Dialogflow), Siri/App Intents and Rasa handle matching, thresholds, fallback, slot filling, and disambiguation

### Takeaway
Every production voice/NLU platform treats "no match" as a first-class outcome. They use a scored classifier with a confidence threshold, a dedicated fallback/out-of-scope intent trained on negative examples, and a confirm-or-rephrase loop when confidence is low. They never fire the nearest intent just because it is the nearest.

### Cited Findings
- **Dialogflow ES:** every candidate intent gets a score from 0.0 to 1.0. The top intent is returned only if its score meets the "ML Classification Threshold". Otherwise a fallback intent is matched, or nothing is matched if no fallback exists. Fallback intents have the lowest matching priority. — [Dialogflow intent matching](https://docs.cloud.google.com/dialogflow/es/docs/intents-matching)
- **Dialogflow ES:** rule-based grammar matching and ML matching run at the same time and the better result wins. Grammar matching is accurate with few or many training phrases. ML matching is "inaccurate with few training phrases". — [Dialogflow intent matching](https://docs.cloud.google.com/dialogflow/es/docs/intents-matching)
- **Dialogflow negative examples:** training phrases are added to the fallback intent so that look-alike inputs do not match real intents. The docs' own example: "buy a book about rooms" must not match "book a room". — [Dialogflow default intents](https://docs.cloud.google.com/dialogflow/es/docs/intents-default)
- **Alexa AMAZON.FallbackIntent:** an out-of-domain model is built automatically from the skill's own intents and samples, and regenerated on every model build. Sensitivity can be set to LOW, MEDIUM or HIGH (en-XX and de-DE only). Amazon advises adding sample utterances to FallbackIntent when a few phrases misroute, and raising sensitivity when many do. — [Alexa built-in intent tips](https://developer.amazon.com/en-US/docs/alexa/interaction-model-design/tips-for-using-built-in-intents-for-your-skill.html)
- **Alexa:** Amazon says "creating a custom intent for a fallback function can produce undesirable results and over-sensitivity" and recommends the built-in intent instead. Adding sample utterances to FallbackIntent "does not affect the statistical model". — [Alexa built-in intent tips](https://developer.amazon.com/en-US/docs/alexa/interaction-model-design/tips-for-using-built-in-intents-for-your-skill.html)
- **Alexa:** an Intent Confidence Dashboard exists for finding low-confidence and misrouted utterances. — [Alexa blog, Apr 2020](https://developer.amazon.com/en-US/blogs/alexa/alexa-skills-kit/2020/04/use-the-intent-confidence-dashboard-to-improve-skill-accuracy)
- **Rasa FallbackClassifier:** predicts `nlu_fallback` when every intent falls below a threshold (the docs' example uses `threshold: 0.7`).
  - Two-stage fallback: on low confidence the bot asks the user to confirm the guessed intent. If they deny it, the bot asks them to rephrase. If the rephrase is still low-confidence and is denied again, an ultimate fallback runs (for example, human handoff).
  - A separate `out_of_scope` intent is trained on examples such as "I want to order food".
  - RulePolicy defaults: `core_fallback_threshold: 0.4`.
  - Source: [Rasa fallback docs (legacy OSS)](https://legacy-docs-oss.rasa.com/docs/rasa/fallback-handoff)
- **Rasa in practice:** a user on the default 2.8.1 pipeline (`threshold: 0.3`, `ambiguity_threshold: 0.1`) reported gibberish like "asdf" being classified as real intents with high probability. Softmax confidence on its own is overconfident on out-of-domain input. — [Rasa forum](https://forum.rasa.com/t/rasa-classifies-random-input-as-intents-with-high-probability/46331)
- **Apple App Intents:** `requestConfirmation()` is called from `perform()` so the user confirms before a side effect. `IntentParameter.requestDisambiguation(among:dialog:)` makes Siri read out the options and the user picks one. With App Schemas, Siri may confirm side-effecting intents such as deletions automatically, and may disambiguate entities before `perform()` runs. This is an Apple Frameworks Engineer answer from June 2026, and the Siri AI preview APIs may change. — [Apple forums thread 832267](https://developer.apple.com/forums/thread/832267); [requestDisambiguation docs](https://developer.apple.com/tutorials/data/documentation/appintents/intentparameter/requestdisambiguation(among:dialog:).md); [requestConfirmation docs](https://developer.apple.com/tutorials/data/documentation/appintents/appintent/requestconfirmation().md)
- **CLINC150 (Larson et al., EMNLP 2019):** this is the standard out-of-scope benchmark. It has 150 intents across 10 domains. The test split is 4,500 in-scope and 1,000 out-of-scope queries, and training has only 100 out-of-scope examples (250 in the "OOS+" variant). — [TFDS clinc_oos](https://www.tensorflow.org/datasets/catalog/clinc_oos); [paper](https://www.aclweb.org/anthology/D19-1131)

### Inferences
- The pattern is the same on every platform: score, then threshold, then an explicit fallback class trained on near-miss negatives, then confirm or rephrase. Nova's current design (trigger substring or nearest embedding fires the skill) has no reject option. That is the root cause of both example misfires.
- "Check the latest anime" vs. "set up Crunchyroll fullscreen" is the Dialogflow "book a room" case exactly. The fix is negative examples plus a slot requirement (the skill should need an explicit "set up / fullscreen / Crunchyroll" signal), not a better embedding.
- Alexa's warning about custom catch-all intents suggests fallback should be a separate mechanism (threshold, margin, or a trained out-of-domain class), not just one more intent competing on equal footing.

### Gaps
- I found no official default for Dialogflow's ML classification threshold. It is commonly cited as 0.3, but that is unverified here.
- I found no official default for Rasa FallbackClassifier `threshold` or `ambiguity_threshold`. Configs commonly show 0.3 or 0.7 and 0.1.
- I did not verify Larson et al.'s numeric results: the PDF could not be text-extracted. From memory only, the paper found out-of-scope recall far below in-scope accuracy for all classifiers.
- I found no numeric description of Alexa's sensitivity levels.
- Google Assistant Conversational Actions were sunset in 2023 (from memory, unverified here), so I did not research them. Dialogflow is Google's surviving reference.

## 2. How LLM agent systems select skills/tools

### Takeaway
Modern agent stacks keep only short name and description metadata in context. They retrieve a small candidate set (BM25, regex or embeddings) and let the LLM make the final choice. Retrieval-then-LLM-choose beats both "dump every tool in the prompt" and "nearest embedding wins". Description quality ("what it does + when to use it") is the main lever.

### Cited Findings
- **Anthropic Agent Skills:** only `name` and `description` of each skill are preloaded into the system prompt. `SKILL.md` is read only when the skill becomes relevant. Claude "uses it to choose the right Skill from potentially 100+ available Skills". — [Anthropic skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- **Anthropic description guidance:**
  - The description must say what the skill does AND when to use it, with specific key terms and triggers.
  - Write it in third person. A point-of-view mismatch "can cause discovery problems".
  - Maximum 1,024 characters.
  - Vague descriptions ("Helps with documents") are anti-patterns.
  - Build evaluations first and check that "the Skill activates when expected".
  - Test on every model you will use, because weaker models need more guidance.
  - Source: [Anthropic skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- **Anthropic Tool Search Tool (Nov 2025):**
  - Tools marked `defer_loading: true` are found on demand through regex or BM25 search over names and descriptions. Custom embedding search is also supported.
  - MCP-eval accuracy went from 49% to 74% for Opus 4 and from 79.5% to 88.1% for Opus 4.5.
  - Tokens dropped from about 77K to about 8.7K, an 85% reduction.
  - Recommended above about 10 tools or 10K tokens of definitions. Keep the 3–5 most-used tools always loaded.
  - Separately, 1–5 "tool use examples" per tool are recommended only where usage is ambiguous.
  - Source: [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- **RAG-MCP (Gan & Sun, arXiv 2505.03275, May 2025):** semantic retrieval narrows a large MCP index before the LLM sees it. Selection accuracy rose to 43.13% from 13.62%, and prompt tokens fell to about 1,084 from about 2,134. A keyword-retrieval variant reached about 18.2%. Retrieval reportedly degrades with thousands of tools. These figures come mostly from secondary summaries, and the paper is listed as not yet independently reproduced. — [arXiv 2505.03275](https://arxiv.org/abs/2505.03275v1); [summary](https://www.themoonlight.io/review/rag-mcp-mitigating-prompt-bloat-in-llm-tool-selection-via-retrieval-augmented-generation); [SOTAVerified](https://sotaverified.org/papers/rag-mcp-mitigating-prompt-bloat-in-llm-tool)
- **ToolLLM (ICLR 2024):**
  - A trained neural API retriever recommends APIs from 16,000+ for each instruction and beats baselines on NDCG across I1, I2 and I3.
  - ToolLLaMA with this retriever beats Gorilla+BM25 on out-of-distribution APIBench.
  - Gorilla has zero-shot and retrieval-aware training modes; in retrieval-aware mode the retrieved API docs go into the prompt.
  - A reported NDCG@1 of about 83.5 comes from a third-party aggregator and is unconfirmed.
  - Source: [ToolLLM arXiv 2307.16789](https://arxiv.org/pdf/2307.16789); [chatpaper summary](https://chatpaper.com/paper/5860)
- **Hybrid router (Arora, Jain, Merugu, EMNLP 2024 Industry):**
  - A fast SetFit (fine-tuned sentence-transformer) classifier estimates its own uncertainty with Monte Carlo dropout. Only uncertain queries go to the LLM.
  - With negative data augmentation it lands "within 2% of native LLM accuracy with 50% less latency".
  - LLM out-of-scope detection depends heavily on how broadly intent labels are scoped and on how many labels exist.
  - An approach using the LLM's internal representations improved OOS accuracy and F1 by more than 5% for Mistral-7B.
  - Source: [arXiv 2410.01627](https://arxiv.org/abs/2410.01627)
- **Dialogflow** runs grammar (lexical) and ML matching in parallel and takes the better result. That is a production precedent for hybrid lexical+semantic matching. — [Dialogflow intent matching](https://docs.cloud.google.com/dialogflow/es/docs/intents-matching)

### Inferences
- The recommended architecture is cascaded:
  1. A cheap retriever (BM25 plus embeddings) produces the top-k candidates. Its job is recall, not decision.
  2. An LLM, or a small trained classifier, decides among {candidates, plain chat, ask, none}.
- Embedding similarity should never be the final decision. Short, vague voice queries like "where you find out?" sit near many skills in embedding space, and cosine scores are not calibrated probabilities.
- Label scope matters (per 2410.01627). Broad skill descriptions like "research things" attract out-of-scope queries, so descriptions should be narrow and action-specific.

### Gaps
- I did not fetch Toolformer (Schick et al., 2023). It learns when to call APIs through self-supervised filtering, but is not directly a selection or retrieval method.
- I found no published head-to-head of "embedding threshold only" vs. "LLM-as-router" false-positive rates on small skill sets (fewer than 50 skills).
- Rerankers (cross-encoders) for tool retrieval were not specifically sourced.

## 3. Evidence on false positives, calibration, negative examples, and confirmation for side-effecting actions

### Takeaway
LLMs, including small ones, over-call tools badly when they should not call any: 21–67% for open models and 22–41% for GPT-4-class. Training or prompting with explicit negative ("do not call") examples cuts this sharply. Model size does not fix it. For side-effecting actions, every major platform inserts an explicit confirmation step.

### Cited Findings
- **When2Call (NVIDIA, NAACL 2025)** frames each query as four choices: direct answer, tool call, follow-up question, or "unable to answer". — [arXiv 2504.18851](https://arxiv.org/html/2504.18851v1)
- **Tool hallucination rates** (calling a tool when the right one is not available):
  - Llama 3.1 8B: 67%
  - Llama 3.2 3B: 52%
  - Llama 3.2 1B: 43%
  - xLAM 1B: 40%
  - Qwen 2.5 3B/7B/72B: about 21–23%
  - xLAM 8x22B: 9%
  - GPT-4o: 26%, GPT-4o-mini: 41%, GPT-4-Turbo: 22%
  - Source: [When2Call](https://arxiv.org/html/2504.18851v1)
- **Size does not help:** Qwen 2.5 When2Call F1 is 29.8 (3B), 32.0 (7B) and 32.8 (72B). Llama 3.2 1B (21.7) beats the 3B (17.9). Models rarely choose "unable to answer", and they often hallucinate a tool call with invented parameters instead of asking a follow-up. — [When2Call](https://arxiv.org/html/2504.18851v1)
- **Negative training works at 4B:**
  - A Mistral-NeMo-Minitron 4B baseline had 16% tool hallucination.
  - SFT on When2Call data brought it to 4.3%, but the model became overly conservative (BFCL AST dropped 6.2 points).
  - RPO preference training with negative examples brought it to 1.9%, recovered most of the AST loss, and raised BFCL irrelevance detection from 41.1% to 77.4%.
  - The 8B results were similar: 19% down to 1.2%.
  - Source: [When2Call](https://arxiv.org/html/2504.18851v1)
- **Negative examples in classic NLU:** Dialogflow puts negative training phrases in the fallback intent, and Alexa adds sample utterances to FallbackIntent. Both are near-miss negatives aimed at specific confusions. — [Dialogflow](https://docs.cloud.google.com/dialogflow/es/docs/intents-default); [Alexa](https://developer.amazon.com/en-US/docs/alexa/interaction-model-design/tips-for-using-built-in-intents-for-your-skill.html)
- **Hybrid routing** with negative data augmentation helped out-of-scope handling. — [arXiv 2410.01627](https://arxiv.org/abs/2410.01627)
- **Confirmation patterns:**
  - Rasa's two-stage fallback asks "did you mean X?" and then asks the user to rephrase. — [Rasa](https://legacy-docs-oss.rasa.com/docs/rasa/fallback-handoff)
  - Apple uses `requestConfirmation()` for side effects and `requestDisambiguation` for choosing among options, and Siri auto-confirms deletions under App Schemas. — [Apple forums](https://developer.apple.com/forums/thread/832267)
- **Softmax confidence is overconfident out of domain:** a Rasa user saw gibberish get high intent confidence. — [Rasa forum](https://forum.rasa.com/t/rasa-classifies-random-input-as-intents-with-high-probability/46331)

### Inferences
- Asking costs one conversational turn. Acting wrongly on a side-effecting skill (fullscreen, app install, browser takeover) costs disruption plus an undo. The usual approach, matching Rasa and Apple, is to tier by risk:
  - read-only or cheap skills act at moderate confidence;
  - disruptive or side-effecting skills require high confidence plus an explicit slot or keyword, or a confirmation.
- For a 4B model, prompting alone is unlikely to bring over-calling near zero, because untuned small models show 40–67% hallucination. A deterministic gate before the LLM, plus few-shot "do not use" examples, is needed. Fine-tuning with negatives is the strongest option if feasible.
- Too many negatives make the model over-conservative (When2Call's SFT finding). Track both false-positive rate AND missed-trigger rate on an eval set.

### Gaps
- I found no published calibration curves (ECE) specifically for embedding-similarity skill routing.
- I found no user-study numbers on how annoying confirmation prompts are for voice assistants (cost of asking vs. acting), so this remains a design judgment.
- Anthropic's docs do not explicitly recommend "Do not use when..." clauses in descriptions. They emphasise specific when-to-use triggers. Negative clauses are a community practice that I did not verify against an official source.

## 4. Practical recommendations for a small local model (~4B) setup

### Takeaway
Use a cascade: an exact or explicit command grammar, then hybrid retrieval of the top-3 candidates, then a strict, small LLM judgment with "none" as a real option. Margin and threshold gates, required slots, risk-tiered confirmation, and a labelled eval set of near-miss negatives sit around it.

### Cited Findings (basis for the recommendations)
- Grammar or rule matching is accurate with few phrases, and ML matching is weak with few phrases. — [Dialogflow](https://docs.cloud.google.com/dialogflow/es/docs/intents-matching)
- An uncertainty-gated cascade (fast classifier first, LLM only for uncertain queries) cut latency by about 50% with less than 2% accuracy loss. — [arXiv 2410.01627](https://arxiv.org/abs/2410.01627)
- Retrieve-then-choose improves selection accuracy and cuts tokens. — [Anthropic Tool Search](https://www.anthropic.com/engineering/advanced-tool-use); [RAG-MCP](https://arxiv.org/abs/2505.03275v1)
- Small models over-call tools, and negative examples or preference training fix much of it. — [When2Call](https://arxiv.org/html/2504.18851v1)
- Descriptions should be specific, say "what + when", and be checked with trigger evals. — [Anthropic best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

### Inferences (concrete design for Nova)
1. **Tier 0, explicit grammar.** Explicit commands ("set up Crunchyroll fullscreen", "research X on the web") match by regex or anchored patterns that need a verb plus an object. Loose substring triggers ("find out", "latest") are not allowed to fire a skill by themselves.
2. **Tier 1, hybrid retrieval for candidates only.** Combine BM25 over the description and examples with embedding cosine (for example, reciprocal-rank fusion) and keep the top-3. The thresholds need two parts:
   - an absolute floor, and
   - a margin: top-1 minus top-2 must exceed some delta, or the query counts as ambiguous.

   Embedding results below the floor go straight to normal chat. Calibrate the floor on real logs, not by intuition.
3. **Tier 2, a 4B LLM as judge, not generator.** Give it only the 3 candidates plus the option NONE / just chat, and ask for strict JSON output (`{"skill": ..., "confidence": "high|low", "missing": [...]}`). Include few-shot negatives, for example:
   - "where you find out?" maps to NONE (a conversational follow-up);
   - "check the latest anime" maps to the web search or answer skill, not crunchyroll-setup.

   Use constrained or grammar decoding if the runtime supports it.
4. **Required slots or preconditions per skill.** Each skill declares required entities or keywords (crunchyroll-setup needs a "set up / fullscreen / open Crunchyroll" intent verb, and web-research needs a researchable topic). If a slot is missing, do not fire. Ask, or fall back to chat.
5. **Risk-tiered confirmation, like Rasa and Apple.**
   - Read-only skills act at a high LLM confidence.
   - Disruptive skills (fullscreen, app launch, installs, browser automation) confirm with "Did you mean set up Crunchyroll fullscreen?" unless they were invoked by the Tier 0 grammar.
   - After a denial, ask the user to rephrase, as in Rasa's two-stage fallback.
6. **Description hygiene.** Write descriptions in third person, narrow, "Does X. Use when the user explicitly asks to Y." Add a short "Not for: ..." list. That list is community practice, not official Anthropic guidance, but it matches Dialogflow-style negative examples. Avoid broad scopes, since label breadth hurts out-of-scope detection.
7. **Eval set and metrics.** Keep a labelled file of positives, near-miss negatives, and pure chit-chat for every skill. Report precision, recall and false-trigger rate per skill on every change. Watch for over-conservatism, the trade-off When2Call measured.
8. **Learn from corrections.** When the user cancels or denies a skill, log the utterance as a negative example for that skill. This is the Alexa/Dialogflow pattern of adding misrouted phrases to fallback.

### Gaps
- I found no source with specific cosine-threshold values for common local embedding models (for example, bge or nomic). These must be tuned empirically on Nova's logs.
- I found no benchmark of ~4B models (for example, Qwen3-4B or Gemma-3-4B) as a constrained skill router over 3 candidates. When2Call's Qwen 2.5 3B (about 21% hallucination) and Mistral-Minitron 4B (16%, down to 1.9% with RPO) are the closest evidence.
