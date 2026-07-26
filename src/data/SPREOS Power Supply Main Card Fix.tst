# Demo system TEST SUITE 1: SPREOS Power Supply Main Card
# PartNum: BAS1540050300

# ======================================================================
# 1. INIT & Communication Check
# ======================================================================

:System INIT and Comms Check
Critical
Log Initializing IDRC-040-076HR (CH1), DAQ-9600 (CH2-4), and 3315G (CH5).
Log Applying PS Default Protections: OVP = 35V, OCP = 20A.
getid
Delay 500
Log Instruments Communication Verified.

# ======================================================================
# 2. Power ON 28V (No Load)
# ======================================================================

:Setup PS to 28VDC
Critical
setlogic off
setvoltage 28.0
Delay 1000
Log Power supply set to 28VDC. Logic Power is OFF (Open). No Load applied.

:Check PS Current at 28V (CH 1)
Log Reading PS output current (Idle state)
Limits 0.0 0.5
Unit A
readchannel 1

:Check PS Voltage at 28V (CH 1)
Log Reading PS output voltage
Limits 27.6 28.4
Unit V
readchannel 1

:Check DAQ JX1 Voltage at 28V (CH 3)
Log Reading DAQ JX1 voltage
Limits 27.6 28.4
Unit V
readchannel 3

# ======================================================================
# 3. Logic Power & LED Interactive Checks
# ======================================================================

:Verify LED ON (Logic Power ON)
Log Setting Logic Power discrete line ON (Ground) via automated breaker.
setlogic on
Delay 500
# YES = 1. Limit is 0.6 to 1.4 to pass on YES.
Limits 0.6 1.4
PromptYesNo Is the RED LED turned ON?

:Verify LED OFF (Logic Power OFF)
Log Setting Logic Power discrete line OFF (Open) via automated breaker.
setlogic off
Delay 500
# The question is phrased so that YES means PASS (LED is OFF)
Limits 0.6 1.4
PromptYesNo Is the RED LED successfully turned OFF?

# ======================================================================
# 4. Voltage Checks under Logic Power ON (No Load)
# ======================================================================

:Setup Logic Power ON for Tests
setlogic on
Delay 500
Log Automated breaker activated. Logic Power is ON. PS remains at 28VDC.

:Check DAQ JX2-4 Voltage at 28V Input (CH 4)
Log Reading voltage between TP1 and TP13 on JX2-4
Limits 27.6 28.4
Unit V
readchannel 4

:Setup 16V Input (No Load)
Log Decreasing Power Supply voltage to 16VDC
setvoltage 16.0
Delay 1000

:Check PS Voltage at 16V Input (CH 1)
Limits 15.6 16.4
Unit V
readchannel 1

:Check DAQ JX2-4 Voltage at 16V Input (CH 4)
Limits 15.6 16.4
Unit V
readchannel 4

:Setup 32V Input (No Load)
Log Increasing Power Supply voltage to 32VDC
setvoltage 32.0
Delay 1000

:Check PS Voltage at 32V Input (CH 1)
Limits 31.6 32.4
Unit V
readchannel 1

:Check DAQ JX2-4 Voltage at 32V Input (CH 4)
Limits 31.6 32.4
Unit V
readchannel 4

# ======================================================================
# 5. Electronic Load Tests (200W Constant Power)
# ======================================================================

:Setup 200W Load Test at 28VDC
Critical
setvoltage 28.0
setlogic off
Delay 500
Log Automating 3315G Electronic Load to 200W Constant Power mode.
setload 200
setlogic on
Delay 1000
Log Setup complete: 28VDC Input, 200W Load actively applied, Logic Power ON.

:Check PS Voltage at 28V (With Load) (CH 1)
Limits 27.6 28.4
Unit V
readchannel 1

:Check DAQ JX2-4 Voltage at 28V (With Load) (CH 4)
Limits 27.6 28.4
Unit V
readchannel 4

:Check Load Voltage at 28V (CH 5)
Limits 27.6 28.4
Unit V
readchannel 5

:Check Load Current at 28V (CH 5)
# 200W / 28V = ~7.14A. Tolerance window applied.
Limits 6.5 7.8
Unit A
readchannel 5

:Setup 16V Input (With 200W Load)
Log Decreasing input to 16VDC. Verifying 200W load stability.
setvoltage 16.0
Delay 1000

:Check PS Voltage at 16V (With Load) (CH 1)
Limits 15.6 16.4
Unit V
readchannel 1

:Check DAQ JX2-4 Voltage at 16V (With Load) (CH 4)
Limits 15.6 16.4
Unit V
readchannel 4

:Check Load Voltage at 16V (CH 5)
Limits 15.6 16.4
Unit V
readchannel 5

:Check Load Current at 16V (CH 5)
# 200W / 16V = ~12.5A. Tolerance window applied.
Limits 11.5 13.5
Unit A
readchannel 5

:Setup 32V Input (With 200W Load)
Log Increasing input to 32VDC. Verifying 200W load stability.
setvoltage 32.0
Delay 1000

:Check PS Voltage at 32V (With Load) (CH 1)
Limits 31.6 32.4
Unit V
readchannel 1

:Check DAQ JX2-4 Voltage at 32V (With Load) (CH 4)
Limits 31.6 32.4
Unit V
readchannel 4

:Check Load Voltage at 32V (CH 5)
Limits 31.6 32.4
Unit V
readchannel 5

:Check Load Current at 32V (CH 5)
# 200W / 32V = ~6.25A. Tolerance window applied.
Limits 5.5 7.0
Unit A
readchannel 5

# ======================================================================
# 6. Cleanup & Teardown
# ======================================================================

:System Teardown
Log Programmatically turning off 3315G Electronic Load.
setload 0
Log Setting Logic Power discrete line OFF (Open).
setlogic off
Log Shutting down Main Power Supply. Waiting for voltage to drop.
setvoltage 0.0
# Long delay to allow hardware capacitors to discharge fully
Delay 2000

:Verify Safe State (CH 1)
Log Verifying PS Voltage is at 0V (Safe to disconnect)
Limits -0.4 0.4
Unit V
readchannel 1

Log Testing complete. Safe to disconnect all cables.