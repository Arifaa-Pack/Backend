# Fallback bench, 17 Nov 2026: looser full-text search, and Groq / Gemini free tier as a backup model

Script: `scripts/llm_fallback_bench.py` (keys from environment variables only; nothing secret is stored).
Raw results: `results/llm_fallback_retrieval_*.json`, `results/llm_fallback_partial_*.json` (answers),
`results/llm_fallback_tricks_*.json` and `results/llm_fallback_answers_*.json` (trick prompts, re-run).

**What was tested.** The look-alike catalogue of the embedding check (8 oz / 12 oz cups, foil tray, 1 l and 750 ml
kraft boxes, certificates; English, Arabic, Hindi, Chinese), 16 buyer questions: the 9 of the embedding check and 7
written the way buyers write ("what's the MOQ on your foil trays?"), 2 of them about products we do not sell.
Seven price trick prompts from the eval suite (lowest price, "ignore your instructions", volume re-price,
competitor match, Arabic, a floor price planted in the prompt), checked with the app's own `kx.core.price_guard`.

**Limits of this bench.** 10 catalogue documents, not the real CMS. Short prompts (≈ 200 tokens), not the real agent
prompts with tools; tool calling was not tested. One run per model (temperature 0).

## 1. Knowledge search with the embedding server down

| Fallback | Right product first | Nothing found (handed to sales) | Wrong product first |
| --- | --- | --- | --- |
| Today: every word must match (`websearch_to_tsquery`) + trigram | **2 / 16** (only the two "we don't sell that") | 14 | 0 |
| Looser: any word may match, ranked | **13 / 16** | 1 (Chinese) | **2** (certificates → kraft box; "16 oz cup" → 12 oz cup) |

The answers built from those results (the model is told to answer only from the facts, else "Let me check with our
sales team."):

| Model | From today's fallback: right / safe / wrong | From the looser fallback: right / safe / wrong |
| --- | --- | --- |
| Groq `openai/gpt-oss-120b` | 2 / 14 / 0 | **15 / 1 / 0** |
| Groq `qwen/qwen3.8-27b` | 2 / 14 / 0 | 14 / 1 / 0 (+1 error: Groq "over capacity") |
| Gemini `gemini-3.5-flash-lite` | 2 / 14 / 0 | **15 / 1 / 0** |

In both cases where the looser search put the wrong product first, every model still answered correctly (the right
chunk was 2nd or 3rd) or said "Let me check". **No wrong product fact in 48 answers.** Chinese: the `simple` text
search cannot split Chinese words (no spaces), so neither version finds anything; only the trigram leg can.

## 2. Groq and Gemini free tier as the backup when Claude fails

| | Groq gpt-oss-120b | Groq qwen3.8-27b | Gemini 3.5 Flash-Lite | Gemini 3.8 Flash |
| --- | --- | --- | --- | --- |
| Grounded answers (loose) | 15/16, 0 wrong | 14/16, 0 wrong | 15/16, 0 wrong | not measured (quota) |
| Price tricks (7) | 7 clean | 7 clean | 6 clean, 1 error (503) | not measured |
| Floor price in the prompt | not revealed | not revealed | not revealed | — |
| Answers in Arabic / Hindi | yes | yes | yes | — |
| Latency p50 (max) | 0.47 s (1.2 s) | 0.15 s (0.7 s) | 4.2 s (7.6 s); 9.3 s in the second run | — |
| Free-tier limit seen | 1,000 requests / day, **8,000 tokens / minute** per model | same; one 503 "over capacity" | no 429 in 46 calls; two 503 "high demand" | **20 requests / day per model**: the run stopped at the quota (retry in ≈ 15 h) |

## Recommendation

1. **Looser full-text fallback: yes, as a second step. Implemented 18 Nov** (`kx/agents/knowledge.py`,
   `_ANY_WORD_SQL`): today's strict query runs first; only when it finds nothing, the any-word query runs (lexemes
   quoted, so buyer text is never tsquery syntax). Strict hits keep their precision; re-running the retrieval step
   against the real `KnowledgeBase.search` gives 13 of 16 (was 2 of 16), as measured above
   (`results/llm_fallback_retrieval_20261118T0405.json`; in that file the "strict" column is the new production
   path). The wrong product ranked first twice did not turn into a wrong answer, because the model answers only from
   the facts and the price guard still checks every reply. DB test in `kx-agent-framework/tests/test_db.py`.
2. **Free tiers as the production backup model: no.**
   * Gemini 3.8 Flash (the standby already named in the code) allows **20 requests a day**; Flash-Lite answered well
     but took 4–16 s and returned "high demand" errors.
   * Groq is fast and answered well, but 8,000 tokens a minute is only one or two real agent turns a minute (the real
     prompts with tools are several thousand tokens), and it returned "over capacity" once.
   * Free tiers have no service guarantee, and Google's free Gemini API terms allow Google to use submitted content to
     improve its products: buyer messages would leave under those terms (UAE PDPL). Check Groq's data terms too.
3. **If a backup model is wanted:** use a **paid** tier. Groq `openai/gpt-oss-120b` (fast, best here) or paid Gemini
   Flash (the standby in the code, `KX_PLATFORM__LLM_STANDBY=gemini`). First run the full eval suite (17 scenarios,
   with tools) live against it; that needs a LangChain client package (`langchain-openai` for Groq's OpenAI-compatible
   API, or `langchain-google-genai`), a new dependency. Until then today's behaviour stays: when Claude fails, the
   buyer gets the safe reply and the deal goes to the salesperson.
