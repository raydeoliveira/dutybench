# DutyBench

**Did a firmware update increase battery drain, or did the sensor wake up during one recording?** DutyBench compares current draw in matching operating phases. Use it from a terminal, CI job or local MCP client. It reads saved measurements on your computer and takes no action on the device.

[Download the free evaluation](https://github.com/raydeoliveira/dutybench/releases/tag/v0.1.0) and extract the ZIP. Open a terminal in the extracted directory. Python 3.11 or later is the only runtime requirement. The examples run without a meter, account or package installation.

## Try two firmware comparisons

```sh
python3 dutybench.py compare examples/sensor-off.json examples/sensor-cycle.json
python3 dutybench.py compare examples/idle-baseline.json examples/idle-candidate.json
```

| Example | What you should see | Why |
|---|---|---|
| Sensor asleep versus one scheduled reading | **inconclusive**. Raw average current rose from 1.260 to 14.487 mA, or about 1,050%. | The firmware was identical. Only one of the two 295-second windows contained a scheduled measurement. |
| Same idle workload before and after a build change | **regression** at the default 5% budget. Three baseline windows averaged 0.861 mA. Two candidate windows averaged 1.170 mA, up 35.9%. | With the example's assumed absolute error of ±0.010 mA and full coverage, even the lowest candidate current exceeds the allowed baseline. |

The current readings in both examples come from a public SimpleAir audit using a Joulescope JS220. The first pair preserves the missing coverage and uncertainty information. The second pair adds **illustrative** error bounds and coverage so you can exercise a release gate. For your own gate, use bounds appropriate to your own measurements. [Original measurements and build conditions](https://gist.github.com/juanqui/298cfce644affc80458989effcd3a55e/b6ecc2feab590ed681626e9a3c7463a3d42104b8)

## Compare your own runs

1. Record both builds on the same board and meter. Keep the supply voltage and workload fixed. Identify repeated windows from the same operating phase, such as settling, idle or radio transmit. Use timestamps or an event marker you trust.
2. Start with an example capture JSON. Each window has an identifying label, phase, duration in seconds and mean current in **mA**. Include measured coverage and an absolute current uncertainty bound where known. Leave unknown evidence absent. Record any important environmental differences in the named conditions.
3. Compare the two files. Change the allowed increase with `--budget-pct`. For CI, add `--gate` and keep the JSON report with the build artifacts.

```sh
python3 dutybench.py compare baseline.json candidate.json --budget-pct 5 --gate --json
python3 dutybench.py --describe
```

| With `--gate` | Exit code |
|---|---:|
| All measured phases are within budget | 0 |
| The overall verdict is regression | 1 |
| Invalid input, arguments or unreadable file | 2 |
| The comparison is inconclusive | 3 |

Without `--gate`, every valid report exits 0. Read `data.status` for the verdict and `data.phases` for the evidence behind it. `--dry-run` performs the same read-only evaluation.

### Import Joulescope UI Statistics Record CSV

Use a **Relative time** statistics export with the header `#time,current,voltage,power,charge,energy`. Give the importer the recording period and the start and end of each labeled window, in seconds. The CSV current column is in amperes. The capture JSON uses milliamps. The example CSV and window definitions below are synthetic and demonstrate the format. [Joulescope's exporter](https://github.com/jetperch/pyjoulescope_ui/blob/16099f74a3b789/joulescope_ui/widgets/statistics_record/statistics_record.py)

```sh
python3 dutybench_import.py examples/statistics.csv examples/windows.json > capture.json
```

The importer measures how many exported statistics intervals are present. A finite CSV average cannot establish whether the device lost individual samples inside that interval. Raw binary JLS is outside this release's supported formats.

### Use from an AI agent

For a desktop client with MCP bundle support, choose the `.mcpb` download on the [release page](https://github.com/raydeoliveira/dutybench/releases/tag/v0.1.0). The client needs Python 3.11 or newer available on its path.

Configure a local stdio MCP server with command `python3` and arguments `["/absolute/path/to/dutybench.py", "mcp"]`. Replace the example path with the location of the extracted file. The server exposes one read-only tool, `dutybench_compare`. Give it two **inline** capture objects named `baseline` and `candidate`, and an optional `budget_pct`. Its structured response includes the verdict, phase evidence and reasons for an inconclusive result.

An example request to your agent is “Compare the baseline and candidate idle captures against a 5% current budget. Tell me which windows support the decision.” The files remain local to DutyBench. Your chosen MCP client determines what it sends to its model provider.

## Interpreting the verdict

DutyBench needs repeat observations, comparable conditions and enough separation between measured bounds to call a regression. It expands the observed range of repeat currents by the absolute error supplied for each reading. That range is a conservative engineering bound. Missing uncertainty, incomplete coverage or different phase mixes can make the overall verdict inconclusive. Check individual phases to see what evidence remains usable.

The tool evaluates mean current over the labeled workload. It cannot infer a radio wake-up, certify meter calibration or predict battery life from a partial recording. For a timing change or a new wake schedule, measure complete cycles and supply the schedule context separately.

## Evaluation and feedback

You may download and run an unmodified copy free of charge to evaluate it personally or within your organization. The software remains proprietary. To uninstall the downloaded evaluation, remove the extracted directory. [License](LICENSE)

If a comparison changed a release decision or abstained unexpectedly, a useful issue includes the command, the verdict you expected and a small anonymized pair of windows. [Report a case](https://github.com/raydeoliveira/dutybench/issues)
