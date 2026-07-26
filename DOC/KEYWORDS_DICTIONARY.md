# KEYWORDS DICTIONARY - The `.tst` Script Vocabulary

The complete reference for the script language consumed by
[`ScriptManager.load_script`](../src/logic/script_manager.py) and executed by
[`TestRunnerThread`](../src/logic/test_engine.py).

## General syntax rules

- One statement per line. Trailing whitespace is ignored.
- Comments start with `#` and run to end of line. Inline comments are allowed.
- Blank lines and lines before the first `:` header are ignored (file preamble).
- **Keyword names are case-insensitive** (`Critical` == `CRITICAL` == `critical`).
- Test names (everything after the `:`), unit strings, and command arguments
  preserve their original case.
- `Limits` and `Tolerance` (`Target ... Tol ...`) are **mutually exclusive** within
  a single step.

## Header keywords (script preamble)

Header keywords are parsed before the first `:<Test Name>` block and are used as
metadata that can drive the UI.

| Keyword | Syntax | Description |
| --- | --- | --- |
| `PartNum` | `PartNum: <value>` (or `# PartNum: <value>`) | Sets the default part number for the loaded script. The UI auto-populates the part-number field if the operator has not manually edited it. |

## Core Structural Keywords

| Keyword     | Syntax                       | Description                                                                                                  |
| ----------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `:`         | `:<Name>`                    | **Test Header**: Starts a new test block.                                                                    |
| `Critical`  | `Critical`                   | **Abort on Fail**: Stops the entire sequence if this step fails.                                             |
| `Limits`    | `Limits <min> <max>`         | **Fixed Range**: Sets the PASS/FAIL boundaries.                                                              |
| `Tolerance` | `Target <val> Tol <%>`       | **Dynamic Range**: Calculates limits from a target and a percentage (e.g. `Target 5.0 Tol 10`).             |
| `Unit`      | `Unit <str>`                 | **Units**: Sets the unit string for reporting (`V`, `A`, etc.).                                              |
| `Delay`     | `Delay <ms>`                 | **Wait**: Pauses execution for X milliseconds.                                                               |
| `Retry`     | `Retry <num>`                | **Auto-Repeat**: Re-runs the step up to X times if it fails before declaring a final FAIL.                   |
| `Prompt`    | `Prompt <msg>`               | **User Action**: Pauses and shows a popup message. Waits for user "OK" to continue.                          |
| `Log`       | `Log <msg>`                  | **Trace Note**: Prints a custom message directly to the Hardware Trace log.                                  |
| `Hidden`    | `Hidden`                     | **Invisible Step**: The step executes but is omitted from the results table and the report. Used for Setup blocks. |
| `Group`     | `Group <name>`               | **Report Grouping**: Collects related steps under one heading in the PDF report.                             |
| `Always`    | `Always`                     | **Teardown**: The step runs even after a critical abort or an operator stop. Used to guarantee `loadoff`.     |

`Critical`, `Limits`, `Tolerance`, `Unit`, `Retry`, `Hidden`, `Group`, and
`Always` are step-level configuration: they apply to the most recent `:`
header. `Delay`, `Prompt`, and `Log` are *per-line commands* and may appear
anywhere inside a step, in sequence with hardware commands.

`Delay` also accepts a monitoring form — `Delay <ms> <min> <max>` — which polls
the output voltage while it waits and fails the step immediately on the first
reading outside `<min>..<max>`, recording that reading as the step's value.
This is what implements the spec's "continuous monitoring throughout the entire
test duration" requirement; a plain `Delay <ms>` only samples once, at the end.

## Report-mapping keywords (CAMSTAR XML)

These place a step's measurement inside the nested XML report defined in
spec Rev.1.1 Appendix B. They are step-level. A script that uses none of them
produces the older flat XML instead, so existing archived versions keep
working unchanged.

| Keyword       | Syntax                        | Description                                                                                     |
| ------------- | ----------------------------- | ------------------------------------------------------------------------------------------------ |
| `Report`      | `Report <TestName>`           | Which `<Test>` node this step feeds. Several steps may share one name — that is how one logical test collects several measurements. Required before any other keyword in this table. |
| `Quantity`    | `Quantity <name>`             | Which typed sub-element: `Voltage`, `Current`, `Power`, or `Resistance`. Omit for a step that contributes only a Pass/Fail `<Result>` (e.g. the LED check). The unit of measure is fixed by the schema, **not** taken from `Unit`. |
| `Measurement` | `Measurement <n>`             | Places the step in `<MeasuredOutputs><Measurement{n}>` with its tags suffixed `n` (`<Voltage1>`). **Omit** to place it in a singular `<MeasuredOutput>` with unsuffixed tags. Indices must start at 1 with no gaps. |
| `TimePoint`   | `TimePoint <value> <uom>`     | Emits `<Time>` for the measurement block, e.g. `TimePoint 5 min`. Only needed on one step per block. |

Rejected at parse time: a `Quantity`/`Measurement`/`TimePoint` without a
`Report`; an unknown quantity; two steps mapping to the same
test+index+quantity; non-consecutive `Measurement` indices; and a test that
mixes numbered and unnumbered measurements.

Steps with no `Report` directive are absent from the XML report. That is
deliberate for the engine's own fixture checks (Input Connection Check,
Polarity Check, Slot Activation), which verify the test station rather than
the UUT.

## Hardware commands (mock backend)

The set of hardware commands recognized by `MockHardware.execute_command`:

| Command       | Kind          | Syntax                       | Notes                                                                                          |
| ------------- | ------------- | ---------------------------- | ---------------------------------------------------------------------------------------------- |
| `setvoltage`  | side-effect   | `setvoltage <volts>`         | Sets a rail to the requested voltage. Returns no measurement.                                  |
| `relay`       | side-effect   | `relay <id> <on\|off>`       | Switches a relay. Returns no measurement.                                                      |
| `setlogic`    | side-effect   | `setlogic <line> <on\|off>`  | Drives a logic/digital output line; applies voltage to switch a device on or off. `<line>` is an integer id; `<on\|off>` is text only, case-insensitive (like `Yes\|No` for `PromptYesNo`). Returns no measurement. |
| `getid`       | side-effect   | `getid`                      | Mock identification ping. Returns no measurement.                                              |
| `readchannel` | measurement   | `readchannel <channel>`      | Reads channel `<channel>`. The **last** measurement value executed in a step is what gets compared against `Limits` / `Tolerance`. |

## Hardware commands (Prodigit 3316G electronic load)

Used by the production `biosend_test.tst`. See `drivers/prodigit_visa.py`; each
maps to a SCPI template that is itself overridable from `.env`.

| Command         | Kind        | Syntax                    | Notes                                                                          |
| --------------- | ----------- | ------------------------- | -------------------------------------------------------------------------------- |
| `setmode`       | side-effect | `setmode <CC\|CR\|CV\|CP>` | Selects the load's operating mode. All spec tests use `CR`.                    |
| `setresistance` | side-effect | `setresistance <ohm>`     | Sets the CR setpoint, then reads it back and retries once if it differs by more than 5%. Raises if it will not take. |
| `loadon`        | side-effect | `loadon`                  | Connects the load to the UUT.                                                  |
| `loadoff`       | side-effect | `loadoff`                 | Disconnects the load. Belongs in an `Always` step so it runs after an abort.   |
| `measvoltage`   | measurement | `measvoltage`             | Measured output voltage `MEAS:VOLT?`.                                          |
| `meascurrent`   | measurement | `meascurrent`             | Measured output current `MEAS:CURR?`.                                          |
| `measpower`     | measurement | `measpower`               | Measured output power `MEAS:POW?`.                                             |
| `getresistance` | measurement | `getresistance`           | Reads back the CR **setpoint** (`CR:HIGH?`) — the programmed resistance, not a derived V/I. Reported so the record shows the load ran at the intended value. |

The set of measurement commands is defined by the driver's
`measurement_commands` property (a `frozenset[str]`, currently `{"readchannel"}`
for `MockHardware`). Adding a new measurement command means appending it to
that frozenset, implementing it in the driver's `execute_command`, and adding a
row here. `setlogic` is a side-effect command and is **not** in
`measurement_commands`.

Any unknown command name raises
`drivers.base_driver.UnknownCommandError("Unknown hardware command: ...")`
inside the runner; the step is marked FAIL and the trace shows the offending
command.

## Worked example

```text
# Power-up a UUT, prompt the operator, take a tolerance-checked measurement,
# and retry it once if the supply has not settled.

:Power Supply Init
Critical
setvoltage 5.0
Delay 200

:Operator Confirm
Prompt Insert UUT and click OK to continue
Log Operator confirmed UUT insertion

:5V Output Voltage
Target 5.0 Tol 5
Unit V
Retry 1
readchannel 0

:Cleanup
setvoltage 0.0
```

Behavior:

1. `Power Supply Init` is critical: any failure aborts the run.
2. `Operator Confirm` parks the runner on `Prompt`; the GUI shows a
   `QMessageBox` and the runner only continues after the operator clicks
   OK. The `Log` line then appears in the Hardware Trace in a distinct
   style.
3. `5V Output Voltage` derives its limits from `Target 5.0 Tol 5` (i.e.
   min 4.75, max 5.25). If `readchannel 0` returns out-of-range, the
   runner re-runs the step once before declaring a final FAIL.
4. `Cleanup` runs unconditionally as a non-measured pass row.

## Validation summary

The parser raises a `ScriptParseError(line_no, line, msg)` for any of:

- A `:` header with an empty name.
- A keyword (`Critical`, `Limits`, `Tolerance`, `Unit`, `Retry`) appearing
  before any `:` header.
- A command appearing before any `:` header.
- `Critical` with extra arguments.
- `Limits` without exactly two numeric arguments, or with `min > max`.
- `Target ... Tol ...` with the wrong shape, with non-numeric `<val>` /
  `<pct>`, with `<pct>` negative, or when the step already has `Limits`.
- `Limits` when the step already has `Target/Tol`.
- `Unit` without a unit string.
- `Retry` without exactly one non-negative integer argument.

`TestRunnerThread` catches `ScriptParseError` and emits the error to the
trace log; it does not throw out of the QThread.
