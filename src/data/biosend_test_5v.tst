# biosend_test_5v.tst
# 5V Power Supply — Low-Load Sanity Test
# Same structure as biosend_test.tst but scaled down for safe bench testing.
#
# Voltage target : 5VDC
# Low load  (~5W)  : CR mode, R = 5.00 Ohm  (5V^2 / 5W  = 5.00 Ohm)
# High load (~10W) : CR mode, R = 2.50 Ohm  (5V^2 / 10W = 2.50 Ohm)
# PartNum: TEST-5V
# InputTarget: 5
# InputTol: 10

# ─────────────────────────────────────────────────────────
# TEST 1 – LED Indication Test (manual)
# ─────────────────────────────────────────────────────────

:LED Indication Test
Critical
Limits 1.0 1.0
Unit pass
PromptYesNo Is the Green LED on the UUT panel ON and STEADY?

# ─────────────────────────────────────────────────────────
# TEST 2 – Polarity Check
# Load OFF. Checks polarity is positive and within 5VDC +-10%.
# 5V x 0.90 = 4.5V (lower limit)
# 5V x 1.10 = 5.5V (upper limit)
# ─────────────────────────────────────────────────────────

:Polarity Check Setup
Hidden
Critical
loadoff

:Polarity Check
Critical
Limits 4.5 5.5
Unit V
Delay 3000
measvoltage

# ─────────────────────────────────────────────────────────
# TEST 3 – Low Load Stability Test – 5W
# CR mode at 5.00 Ohm -> draws ~5W from the UUT.
# Waits 10 seconds. Checks voltage stays within 5VDC +-5%.
# 5V x 0.95 = 4.75V (lower limit)
# 5V x 1.05 = 5.25V (upper limit)
# ─────────────────────────────────────────────────────────

:Low Load 5W Setup
Hidden
Critical
setmode CR
setresistance 5.00
loadon

:Low Load Stability Test - 5W
Critical
Limits 4.75 5.25
Unit V
Delay 10000
measvoltage

# ─────────────────────────────────────────────────────────
# TEST 4 – Continuous Load Test – 10W
# CR mode at 2.50 Ohm -> draws ~10W from the UUT.
# Three measurement points at 10s, 20s, 30s.
# Only Voltage has pass/fail limits. Current and Power are for documentation only.
#
# The Voltage step at each point carries "Delay <ms> 4.75 5.25":
# it waits the interval WHILE checking the voltage every 100 ms. If the
# voltage leaves 4.75-5.25 V at any moment during the wait, that exact
# reading is recorded and the step FAILS (showing the value vs the limits).
# If the voltage holds, the final measvoltage reading is recorded instead.
# ─────────────────────────────────────────────────────────

:High Load 10W Setup
Hidden
Critical
setmode CR
setresistance 2.50
loadon

# --- Measurement point: 10 seconds ---

:Voltage Measurement [V] - 10W - 10sec
Group High Load 10W - 10 Seconds
Critical
Limits 4.75 5.25
Unit V
Delay 10000 4.75 5.25
measvoltage

:Current Measurement [A] - 10W - 10sec
Group High Load 10W - 10 Seconds
Unit A
meascurrent

:Power Measurement [W] - 10W - 10sec
Group High Load 10W - 10 Seconds
Unit W
measpower

# --- Measurement point: 20 seconds (10 more seconds) ---

:Voltage Measurement [V] - 10W - 20sec
Group High Load 10W - 20 Seconds
Critical
Limits 4.75 5.25
Unit V
Delay 10000 4.75 5.25
measvoltage

:Current Measurement [A] - 10W - 20sec
Group High Load 10W - 20 Seconds
Unit A
meascurrent

:Power Measurement [W] - 10W - 20sec
Group High Load 10W - 20 Seconds
Unit W
measpower

# --- Measurement point: 30 seconds (10 more seconds) ---

:Voltage Measurement [V] - 10W - 30sec
Group High Load 10W - 30 Seconds
Critical
Limits 4.75 5.25
Unit V
Delay 10000 4.75 5.25
measvoltage

:Current Measurement [A] - 10W - 30sec
Group High Load 10W - 30 Seconds
Unit A
meascurrent

:Power Measurement [W] - 10W - 30sec
Group High Load 10W - 30 Seconds
Unit W
measpower

:Cleanup
Hidden
Always
loadoff
