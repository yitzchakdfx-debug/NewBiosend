# Demo system test for DFX ATE
# Exercises Critical, Limits, Unit, Delay, and all mock commands.
# PartNum: BIRD-DEMO-001

:Power Supply Init
Critical
setvoltage 5.0
setvoltage 12.0
Delay 300

:Communication Check
getid
Delay 100

:Operator Confirm
Prompt Insert UUT and click OK to continue
Log Operator confirmed UUT insertion

:Tolerance + Retry Demo
Target 5.0 Tol 5
Unit V
Retry 2
readchannel 0

:5V Output Voltage
Limits 4.95 5.15
Unit V
readchannel 0

:12V Output Voltage
Limits 11.85 12.15
Unit V
readchannel 1

:5V Load Current
Limits 0.5 2.0
Unit A
readchannel 2

:Cleanup
relay 1 off
setvoltage 0.0
