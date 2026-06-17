# Camera-gear research eval results — 2026-06-17

Scores from `camera-lens-travel-eval` run on four research tools across three eval modes.
Each tool received the **same blind benchmark prompt** (no rubric or gold-facts leaked) and was
graded against the mode's gold-anchored rubric with line-grounded scoring + score caps.

- **storm-research** — native STORM skill (this repo), single agent, web research + citation audit.
- **deep-research** — built-in deep-research workflow harness (~100 parallel agents).
- **ChatGPT** — browser ChatGPT deep-research output, pasted in; **scored on report text only**.
- **scry-api** — scry's web-capable **agent-query (ReAct) agent** (`run_manager_candidate_agent_query`),
  run locally via a benchmark management command. **Claude Opus 4.7**, Anthropic provider-native web
  search. See methodology note below — this is *not* scry's corpus-only deep-research path.

## Scores

| Eval mode | storm-research | deep-research | ChatGPT | scry-api |
|---|---:|---:|---:|---:|
| lens-kit | **94** — Pass (excellent) | 65 — Borderline | **82** — Pass (good) | 66 — Weak |
| business-backpack | **90** — Pass | 58 — Fail | 76 — Borderline | 76 — Borderline |
| leica-water-sling | **73** — Borderline | 46 — Fail | 71 — Borderline | 64 — Weak |
| **Average** | **85.7** | 56.3 | 76.3 | 68.7 |

Verdict bands: 90-100 excellent · 80-89 good · 70-79 borderline · 60-69 weak · <60 fail.

Overall ranking: **storm-research (85.7) > ChatGPT (76.3) > scry-api (68.7) > deep-research (56.3).**

## Token + cost (measured telemetry; Opus 4.8 rates)

storm-research and deep-research ran on Opus 4.8. ChatGPT exposes no token telemetry → n/a.
scry-api's agent-query path does not record per-call token counts (`model_calls` report 0/None) → n/a;
wall time and run shape are reported instead. Dollar figures use the ~$7/MTok "likely" blend (≈90%
input / 10% output); the **token counts are measured**, the dollars are estimates (true cost is lower
if cache reads are counted at face value).

| Eval | storm tokens | storm ~$ | deep tokens | deep ~$ | ChatGPT | scry-api |
|---|---:|---:|---:|---:|---:|---:|
| lens-kit | 164,535 | $1.15 | 2,842,014 | $19.9 | n/a | n/a (~4.1 min wall) |
| business-backpack | 100,617 | $0.70 | 2,559,300 | $17.9 | n/a | n/a (~3.4 min wall) |
| leica-water-sling | 120,314 | $0.84 | 2,494,479 | $17.5 | n/a | n/a (~3.4 min wall) |
| **Total (3 evals)** | **385,466** | **~$2.70** | **7,895,793** | **~$55** | n/a | n/a |

deep-research used **~20× the tokens** of storm-research overall (~$55 vs ~$2.70).

## scry-api methodology (read before comparing its column)

- **Why the agent-query agent, not deep research.** scry's `ArtifactDeepResearchAgent` is **corpus-only
  by design** (private artifact retrieval, no web) — it structurally cannot answer these open-web consumer
  prompts. The closest comparable surface is scry's **web-capable agent-query / ReAct agent**
  (`run_manager_candidate_agent_query`, `include_web_search=True`), which is scry's real self-retrieving
  web researcher. That is what was benchmarked.
- **How.** A throwaway `management/commands/bench_research_prompt.py` runs a free-text question through
  that agent with an empty corpus and web search on, writing the raw payload to JSON. Run on the local
  Docker stack. Budget set research-grade: `max_steps=10`, `web_search_max_calls=8`, Opus 4.7.
- **Run shape (all three).** The agent **did not exhaust its budget**: 3 ReAct steps and 4–5 provider
  web searches per run, `forced_finalize=False` (it stopped on its own). So the scores reflect the
  agent's *natural* behavior at generous limits, not a starved run — a fair ceiling for this surface.
- **Not purpose-built for this.** This agent is built for manager/company due-diligence Q&A, not
  consumer-gear shootouts. Treat its column as "what scry's general web agent does cold on an
  out-of-domain prompt," not as a tuned competitor to ChatGPT/STORM deep research.

## Per-eval one-line summary

- **lens-kit:** storm 94 ≫ ChatGPT 82 > scry 66 ≈ deep 65. storm and ChatGPT did the bag-vs-in-hand
  weight math and nailed both recommendations (16-35 GM II single; 24/1.4 GM + 35/1.8 two-prime).
  scry got the **single-lens pick right** (16-35 GM II) but wrongly elevated the ultralight 24/2.8 G +
  40/2.5 G as the "best two-prime for your taste" (gold wants 24/1.4 GM + 35/1.8 for the user's
  shallow-DOF love), never assembled full bag/in-hand weight tables (couldn't find the body weight),
  and declined measured MTF/lp-mm. deep gave MTF for only a minor lens and skipped the weight tables.
- **business-backpack:** storm 90 > ChatGPT 76 = scry 76 > deep 58. storm ranked the Aer TP4 #1 (gold's
  settled pick) and diagnosed the Osprey shirt-ride-up. scry **tied ChatGPT**: it nailed the Osprey
  trampoline-mesh shirt-ride-up diagnosis, the style mapping, under-seat plausible-vs-guaranteed, and a
  3-7L sling-nesting analysis, and ranked CTB26 #1 / Aer TP4 #2 — but it did **not** name the Aer TP4 as
  the bag-to-beat (max-82 cap), never verified water-pockets/pass-through on its top two picks, dropped
  the Patagonia 16″-laptop caveat, and pushed the H2 out of the top 4.
- **leica-water-sling:** storm 73 ≈ ChatGPT 71 > scry 64 ≫ deep 46. scry **nailed the hardest
  quantitative part** — exact Keith bottle geometry (400ml plausible inside a 4L bag, 550ml too tall for
  vertical carry) — and had strong stealth/style reasoning. But it **contradicted the user's own correct
  premise**, claiming the Think Tank TurnStyle 5L lacks a real bottle pocket (it has one); omitted the
  Wotancraft Pilot 3.5L and Moment Slate 4L (two of the four gold picks); ranked the Moment Everything
  Sling 4L only #3; and made a **6L** bag its #1 despite the user's settled 3-4L sweet spot. deep used
  the wrong Wotancraft model, omitted the Moment Slate, and recommended the user's own Domke F-803.

## Reading notes / caveats

- **Fixed-baseline effect.** The rubrics encode the user's *settled* conclusions and *tested
  rejections* (Aer TP4 = bag-to-beat; Osprey ride-up; WANDRD/Peak-6L rejected; specific gold-named
  products). Lower scores for deep-research, ChatGPT, and scry are mostly **gold-mismatch** —
  re-litigating a settled choice, naming a different product variant, or dropping a tested rejection —
  **not hallucinated specs**. All four tools produced accurate, sourced figures.
- **ChatGPT comparability.** Scored on the pasted report text only; no token/cost data, and the run
  wasn't observed end-to-end. Treat its column as quality-of-output, not cost-adjusted.
- **scry comparability.** Out-of-domain surface (a due-diligence Q&A agent), no token telemetry, and it
  self-limited to 3 ReAct steps. Its column is "general web agent, cold," not a tuned deep-research tool.
  Notably it still **beat the deep-research harness** (which spent ~$18/run) on all three, on its own
  natural budget.
- **storm self-verification.** Every storm-research report also passed its own citation audit
  (lens 93%, backpack 93%, sling 90%; 0 dangling/ungrounded citations).

## Bottom line

Across all three evals, ranking is **storm-research > ChatGPT > scry-api > deep-research**, and storm
leads on cost by ~20×. ChatGPT writes strong, well-sourced, table-rich reports and was competitive on
the lens-kit and sling. **scry-api's general web ReAct agent, run cold on out-of-domain consumer prompts
and self-limiting to 3 steps, lands between ChatGPT and the much heavier deep-research harness** — it
matched ChatGPT on the backpack and produced the single best piece of quantitative reasoning in the
sling eval (the Keith bottle fit), but its recurring weakness — shared with deep-research and ChatGPT —
is overriding or under-preserving the user's stated constraints, which these preference-strict evals
penalize hardest.
