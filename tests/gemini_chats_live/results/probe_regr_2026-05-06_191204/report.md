# Regression-isolation probe — 2026-05-06_191204

Model: `gemini-2.5-flash-lite` · Iterations per question: 5

## Pass rates per question

| QID | PASS | FAIL | WEAK | rate | verdicts in order |
|---|---:|---:|---:|---:|---|
| Q07 | 1 | 0 | 4 | 20% | `WEAK PASS WEAK WEAK WEAK` |
| Q08 | 3 | 1 | 1 | 60% | `PASS PASS FAIL PASS WEAK` |
| Q17 | 4 | 1 | 0 | 80% | `PASS PASS PASS PASS FAIL` |

## Hypothesis distinguishability

- **All PASS or all FAIL** for a question → **H2** (prompt/tool config issue, not sampling)
- **Mixed PASS/FAIL** for a question → **H1** (sampling nondeterminism)
- **All FAIL with wrong tool in trace** → **H3** (tool-choice confusion)

## Tool trace summary

0 tool calls during the probe window.
