# Output Template

Use this structure for every eval result.

```markdown
# Camera Lens Travel Eval Result

## Verdict

Score: NN/100  
Result: Pass | Borderline | Fail

One-sentence summary: ...

## Candidate line map

Only include this section if the input had no line numbers. Keep each line short.

- L001: ...
- L002: ...

## Line-selected scoring

| Criterion | Points | Evidence | Notes |
| --- | ---: | --- | --- |
| Problem framing | X/10 | L... | ... |
| Bag vs hand weight | X/20 | L... | ... |
| Recommendation hierarchy | X/25 | L... | ... |
| Full-frame / APS-C crop logic | X/15 | L... | ... |
| MTF/lpmm resolution logic | X/15 | L... | ... |
| a7R VI functionality | X/10 | L... | ... |
| Communication quality | X/5 | L... | ... |

Subtotal before caps: NN/100  
Caps applied: None | Max NN because ...

## Critical misses or hallucinations

- ...

## Corrected answer skeleton

A minimally strong answer would say:

1. ...
2. ...
3. ...

## Most important improvement

...
```

## Pass thresholds

- 90-100: Excellent. Correct, quantified, and decision-useful.
- 80-89: Good. Minor omissions, but core conclusion and tradeoffs are right.
- 70-79: Borderline. Some correct conclusions but important missing evidence.
- 60-69: Weak. May have a useful recommendation but contains major gaps.
- Below 60: Fail. Wrong gear logic, wrong format/crop assumptions, or unsupported conclusion.
