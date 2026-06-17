---
name: camera-lens-travel-eval
description: evaluate model answers about light high-quality travel lens choices for sony a7r vi or a7r v versus leica m10 kits. use when grading or line-selecting an lm output against this lens discussion, especially whether it distinguishes bag weight from mounted hand weight, ranks the sony 16-35mm f/2.8 gm ii and 24mm plus 35mm prime kit correctly, uses mtf/lpmm resolution evidence, explains aps-c crop traps, and factors in a7r vi capabilities.
---

# Camera Lens Travel Eval

Use this skill to grade a candidate LM answer to the travel-lens discussion about Sony a7R VI/a7R V versus Leica M10 kits. The evaluator should produce a line-selected, evidence-backed score rather than a general opinion.

## Inputs

Expect one of these inputs:

1. A candidate LM answer only.
2. The original prompt plus a candidate LM answer.
3. A file containing one or more candidate answers.

If the candidate answer has no line numbers, create stable line or sentence IDs before grading. Do not ask for clarification unless the candidate answer itself is missing.

## Required references

Load these files before grading:

- `references/gold-facts.md` for the accepted factual baseline and key conclusions.
- `references/rubric.md` for the 100-point scoring breakdown and score caps.
- `references/line-selection-protocol.md` for evidence selection rules.
- `references/expected-reasoning-traces.md` for the observable reasoning steps expected in a strong answer.
- `references/output-template.md` for the required grading format.

Use `references/test-cases.md` only when building or validating an eval harness.
Use `references/source-notes.md` only when checking source provenance or updating factual assumptions.

## Evaluation workflow

1. Normalize the prompt context. Treat "a7r6", "a7r vi", and "ILCE-7RM6" as the same body. Treat the phrase "16 to 30 528" as likely speech-to-text for "16-35mm f/2.8" when the surrounding context makes that clear.
2. Number the candidate answer. Prefer original line numbers if present. Otherwise split into short paragraphs or sentences and label them `L001`, `L002`, etc.
3. Select evidence lines. For every scored criterion, cite the candidate's exact line IDs or mark `MISSING`, `CONTRADICTED`, or `UNSUPPORTED`.
4. Score against the rubric. Apply score caps for critical errors before assigning the final score.
5. Provide a compact corrected answer. Include the minimum facts the answer should have contained, not a full rewrite.

## Output requirements

Return:

- A verdict: pass, borderline, or fail.
- A numeric score out of 100.
- A line-evidence table with selected lines and points awarded.
- Critical misses and hallucinations.
- A corrected answer skeleton.
- A one-sentence recommendation for how to improve the candidate answer.

## Grading stance

Be strict about factual reasoning, but do not require exact wording. Award full credit when the answer makes the right distinction and uses materially correct numbers even if it rounds small differences. Penalize confident recommendations that ignore kit weight math, crop mode, MTF/lpmm evidence, or the a7R VI capability advantage.
