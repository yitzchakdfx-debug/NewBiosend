# biosend_test_10pct.tst
# Power Supply EM-5406-00F / EM-5406-00 Load Test
# Based on: "Automatic Power Supply Test – Specification.pdf"
# PartNum: EM-5406-00
#
# VARIANT: all voltage limits use ±10% of 24VDC.
#   24V × 0.90 = 21.6V (lower limit)
#   24V × 1.10 = 26.4V (upper limit)

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
PromptYesNo Is the Green LED on the UUT panel ON and STEADY?

# ─────────────────────────────────────────────────────────
# TEST 2 – Polarity Check
# Spec §1.1.7.1 + Appendix A §2.1.3.2
#
# Measures output voltage with load OFF.
# Checks that polarity is correct (positive) and within ±10% of 24VDC.
# 24V × 0.90 = 21.6V (lower limit)
# 24V × 1.10 = 26.4V (upper limit)
# Waits 3 seconds for voltage to stabilise before measuring.
# ─────────────────────────────────────────────────────────

:Polarity Check Setup
Hidden
Critical
loadoff

:Polarity Check
Critical
Limits 21.6 26.4
Unit V
Delay 3000
measvoltage

# ─────────────────────────────────────────────────────────
# TEST 3 – Low Load Stability Test – 50W
# Spec §1.1.7.2 + Appendix A §2.1.3.3
#
# Load is set to CR mode at 5.76 Ω → draws ~100W from the UUT.
# Waits 1 minute for the UUT to stabilise under load.
# Then measures voltage and checks it stays within ±10% of 24VDC.
# 24V × 0.90 = 21.6V (lower limit)
# 24V × 1.10 = 26.4V (upper limit)
# ─────────────────────────────────────────────────────────

:Low Load 50W Setup
Hidden
Critical
setmode CR
setresistance 12.5
loadon

:Low Load Stability Test - 50W
Critical
Limits 21.6 26.4
Unit V
Delay 60000
measvoltage

# ─────────────────────────────────────────────────────────
# TEST 4 – Burn-In / Continuous Load Test – 300W
# Spec §1.1.7.3 + Appendix A §2.1.3.4
#
# Load set to CR mode at 1.8 Ohm → draws ~300W from the UUT (at the sagged
# output voltage). The resistance is set here, in the script, and is the source
# of truth; the .env LOAD_RESISTANCE_300W_OHM value is only a fallback used if
# this Setup step is not run.
# Three measurement points: 5 min, 15 min, 30 min.
# Each point records Voltage, Current, and Power.
# Only Voltage has pass/fail limits (21.6–26.4V, ±10% of 24VDC).
# Current and Power are recorded for documentation only — no limits.
# Delay values are cumulative: 5min, then 10min more, then 15min more.
#
# The Voltage step at each point carries "Delay <ms> 21.6 26.4": it waits the
# interval WHILE checking the voltage every 100 ms. If the voltage leaves
# 21.6–26.4V at any moment during the wait, that exact reading is recorded and
# the step FAILS (showing the value vs the limits). If the voltage holds, the
# final measvoltage reading is recorded instead.
# ─────────────────────────────────────────────────────────

:Burn-In 300W Setup
Hidden
Critical
setmode CR
setresistance 1.7
loadon

# --- Measurement point: 5 minutes ---

:Voltage Measurement [V] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Critical
Limits 21.6 26.4
Unit V
Delay 300000 21.6 26.4
measvoltage

:Current Measurement [A] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit A
meascurrent

:Power Measurement [W] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit W
measpower

# --- Measurement point: 15 minutes (10 more minutes after the 5min point) ---

:Voltage Measurement [V] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Critical
Limits 21.6 26.4
Unit V
Delay 600000 21.6 26.4
measvoltage

:Current Measurement [A] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit A
meascurrent

:Power Measurement [W] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit W
measpower

# --- Measurement point: 30 minutes (15 more minutes after the 15min point) ---

:Voltage Measurement [V] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Critical
Limits 21.6 26.4
Unit V
Delay 900000 21.6 26.4
measvoltage

:Current Measurement [A] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit A
meascurrent

:Power Measurement [W] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit W
measpower

:Cleanup
Hidden
Always
loadoff
