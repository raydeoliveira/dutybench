---
name: dutybench
description: Compare measured firmware current draw across labeled operating phases in DutyBench. Use when reviewing baseline and candidate measurements, importing a Joulescope statistics CSV, or checking whether a firmware change exceeded a current budget.
---

# Compare firmware current with DutyBench

Use the extracted [DutyBench evaluation ZIP](https://github.com/raydeoliveira/dutybench/releases/tag/v0.1.0) and Python 3.11 or later. Run commands from the extracted directory. The skill supplies instructions. The ZIP contains the CLI and examples.

```sh
python3 dutybench.py compare examples/sensor-off.json examples/sensor-cycle.json --json
```

Read `data.status` first. This same-firmware example returns `inconclusive`. One 295-second window includes a scheduled sensor reading. The pooled mean rises from 1.260 to 14.487 mA, and the source omits coverage and uncertainty. With `examples/idle-baseline.json` and `examples/idle-candidate.json`, the matched idle example returns `regression` at the default 5% budget using explicitly illustrative full coverage and ±0.010 mA bounds.

For the user's files, compare the same board, meter, supply voltage and workload. Use the operator's phase labels, observed coverage and absolute uncertainty. Preserve missing evidence. For a relative-time Joulescope UI Statistics Record CSV, the importer also needs the recording period and labeled window boundaries. [Import instructions](https://github.com/raydeoliveira/dutybench#import-joulescope-ui-statistics-record-csv)

For CI, run `python3 dutybench.py compare baseline.json candidate.json --budget-pct 5 --gate --json`. Exit 0 means within budget, 1 means regression, 2 means invalid input and 3 means inconclusive. When reporting a verdict, give the phase, both captures' window IDs, current bounds and any reasons the evidence was inconclusive. Mean current in a partial recording cannot establish battery life.

A configured MCP client can call `dutybench_compare` with inline `baseline`, `candidate` and optional `budget_pct`. Pass capture objects as arguments. [Local MCP setup](https://github.com/raydeoliveira/dutybench#use-from-an-ai-agent)
