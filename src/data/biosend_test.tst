# biosend_test.tst
# Power Supply EM-5406-00F / EM-5406-00 Load Test
# Based on: "Automatic Power Supply Test – Specification.pdf"
# PartNum: EM-5406-00

# InputTarget: 24
# InputTol: 10

# ─────────────────────────────────────────────────────────
# TEST 1 – LED Indication Test (manual)
# Spec §1.1.6 + Appendix A §2.1.3.1
#
# The operator switches on the UUT and physically checks
# that the green LED on the front panel is ON and STEADY.
# Yes = 1.0 → within Limits 1.0 1.0 → PASS
# No  = 0.0 → below  Limits 1.0 1.0 → FAIL → Critical stops all subsequent tests
# ─────────────────────────────────────────────────────────

:LED Indication Test
Critical
Limits 1.0 1.0
Unit pass
Report LED Indication Test
PromptYesNo Is the Green LED on the UUT panel ON and STEADY?

# ─────────────────────────────────────────────────────────
# TEST 2 – Polarity Check
# Spec Rev.1.1 §1.1.7.1 + Appendix A §2.1.3.2
#
# Measures output voltage with load OFF.
# Pass criteria (Rev 1.1): output voltage polarity is positive (+24 VDC).
# The ±10% window from Rev 1.0 was REMOVED — this is a wiring check, not an
# accuracy check. Voltage accuracy is verified by Tests 3 and 4 at ±5%.
#
# THIS TEST HAS NO STEPS HERE — it is performed entirely by the ENGINE.
#
# `TestRunnerThread._check_polarity` runs automatically, immediately after the
# LED step above passes and before any load is applied, which is the order
# §1.1.4.2 and Appendix A require (LED = Test 1, Polarity = Test 2). It switches
# the load off, waits 3 s to settle, reads the voltage, and requires it to be
# positive AND above a noise floor (10 % of the input target) so a dead UUT
# reading 0.00 V cannot pass. It emits the single "Polarity Check" row for both
# the PDF and the CAMSTAR XML.
#
# Do not add `:Polarity Check` or `:Polarity Check Setup` steps here. A scripted
# measurement step used to exist and produced a SECOND, duplicate row with a
# laxer limit (>= 0 V, which passes a dead unit), while Appendix B expects
# exactly one <Test> named "Polarity Check". A separate setup step is also
# unnecessary now: the engine performs its own load-off and settle, and a
# scripted one would run at the wrong point in the sequence.
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# TEST 3 – Low Load Stability Test – 100W
# Spec Rev.1.1 §1.1.7.2 + Appendix A §2.1.3.3
#
# Load is set to CR mode at 5.76 Ω → P = V²/R = 24²/5.76 = 100 W.
# Waits 1 minute for the UUT to stabilise under load.
# Pass criteria (Rev 1.1): output voltage remains within 22.8 V – 25.2 V
# THROUGHOUT the test — not only at the end. The "Delay <ms> 22.8 25.2" form
# polls the voltage during the wait and fails the step on the first excursion.
# 24V × 0.95 = 22.8V (lower limit)
# 24V × 1.05 = 25.2V (upper limit)
# ─────────────────────────────────────────────────────────

:Low Load 100W Setup
Hidden
Critical
setmode CR
setresistance 5.76
loadon

# Appendix A §2.1.3.3 requires Voltage, Current, Power AND Resistance at t1,
# so the single voltage step of Rev 1.0 is split into four. Only Voltage
# carries limits; the rest are recorded for documentation. All four share one
# Group so the PDF shows them under a single heading, and all four map to the
# same <Test> node with no Measurement index — that is what renders them as
# unnumbered tags inside a singular <MeasuredOutput>.

:Voltage Measurement [V] - 100W - 1min
Group Low Load 100W - 1 Minute
Critical
Limits 22.8 25.2
Unit V
Report Low Load Stability Test
Quantity Voltage
TimePoint 1 min
Delay 60000 22.8 25.2
measvoltage

:Current Measurement [A] - 100W - 1min
Group Low Load 100W - 1 Minute
Unit A
Report Low Load Stability Test
Quantity Current
meascurrent

:Power Measurement [W] - 100W - 1min
Group Low Load 100W - 1 Minute
Unit W
Report Low Load Stability Test
Quantity Power
measpower

:Resistance Measurement [ohm] - 100W - 1min
Group Low Load 100W - 1 Minute
Unit ohm
Report Low Load Stability Test
Quantity Resistance
getresistance

# ─────────────────────────────────────────────────────────
# TEST 4 – Burn-In / Continuous Load Test – 300W
# Spec Rev.1.1 §1.1.7.3 + Appendix A §2.1.3.4
#
# Load set to CR mode at 2.0 Ohm (spec Note 1) → P = 24²/2.0 = 288 W, rising
# toward 300 W as the load draws. The resistance is set here, in the script,
# and is the source of truth; the .env LOAD_RESISTANCE_300W_OHM value is only
# a fallback used if this Setup step is not run.
# Three measurement points: t1 = 5 min, t2 = 15 min, t3 = 30 min.
# Each point records Voltage, Current, Power and Resistance.
# Only Voltage has pass/fail limits (22.8–25.2V, ±5% of 24VDC).
# Current, Power and Resistance are recorded for documentation only.
# Delay values are cumulative: 5min, then 10min more, then 15min more.
#
# The Voltage step at each point carries "Delay <ms> 22.8 25.2": it waits the
# interval WHILE polling the voltage (see _MonitorOutOfRange in test_engine.py;
# the poll interval is the engine's 300 ms chunk). If the voltage leaves
# 22.8–25.2V at any moment during the wait, that exact reading is recorded and
# the step FAILS (showing the value vs the limits). If the voltage holds, the
# final measvoltage reading is recorded instead.
# ─────────────────────────────────────────────────────────

:Burn-In 300W Setup
Hidden
Critical
setmode CR
setresistance 2.0
loadon

# --- Measurement point: 5 minutes ---

:Voltage Measurement [V] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Critical
Limits 22.8 25.2
Unit V
Report Burn-In / Continuous Load Test
Quantity Voltage
Measurement 1
TimePoint 5 min
Delay 300000 22.8 25.2
measvoltage

:Current Measurement [A] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit A
Report Burn-In / Continuous Load Test
Quantity Current
Measurement 1
meascurrent

:Power Measurement [W] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit W
Report Burn-In / Continuous Load Test
Quantity Power
Measurement 1
measpower

:Resistance Measurement [ohm] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit ohm
Report Burn-In / Continuous Load Test
Quantity Resistance
Measurement 1
getresistance

# --- Measurement point: 15 minutes (10 more minutes after the 5min point) ---

:Voltage Measurement [V] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Critical
Limits 22.8 25.2
Unit V
Report Burn-In / Continuous Load Test
Quantity Voltage
Measurement 2
TimePoint 15 min
Delay 600000 22.8 25.2
measvoltage

:Current Measurement [A] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit A
Report Burn-In / Continuous Load Test
Quantity Current
Measurement 2
meascurrent

:Power Measurement [W] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit W
Report Burn-In / Continuous Load Test
Quantity Power
Measurement 2
measpower

:Resistance Measurement [ohm] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit ohm
Report Burn-In / Continuous Load Test
Quantity Resistance
Measurement 2
getresistance

# --- Measurement point: 30 minutes (15 more minutes after the 15min point) ---

:Voltage Measurement [V] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Critical
Limits 22.8 25.2
Unit V
Report Burn-In / Continuous Load Test
Quantity Voltage
Measurement 3
TimePoint 30 min
Delay 900000 22.8 25.2
measvoltage

:Current Measurement [A] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit A
Report Burn-In / Continuous Load Test
Quantity Current
Measurement 3
meascurrent

:Power Measurement [W] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit W
Report Burn-In / Continuous Load Test
Quantity Power
Measurement 3
measpower

:Resistance Measurement [ohm] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit ohm
Report Burn-In / Continuous Load Test
Quantity Resistance
Measurement 3
getresistance

:Cleanup
Hidden
Always
loadoff
