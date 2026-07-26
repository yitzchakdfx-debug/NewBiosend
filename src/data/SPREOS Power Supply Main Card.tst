# Demo system TEST SUITE 1: SPREOS Power Supply Main Card
# Exercises Critical, Limits, Unit, Delay, and all mock commands.
# PartNum: BAS1540050300

:Power Supply Power on 
Critical
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

:Logic Power LED Check
Critical
setvoltage 28.0
Delay 500

Prompt Set ON. Verify that the RED LD2 LED is ON. 
Log Operator visually verified RED LD2 LED is ON

Prompt Set OFF. Verify that the RED LD2 LED is OFF.
Log Operator visually verified RED LD2 LED is OFF

:Teardown Logic Power Check
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10
Log Power set to 0. Test complete.

# ======================================================================
# 4.2.4. Test Procedure: 28VDC Power Supply Input
# ======================================================================

:Power Supply Power on 
Critical
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

:Setup 4.2 Without Load (28V Input)
Critical
setvoltage 28.0
Delay 500
Prompt Set ON the Logic Power discrete line using the switch. Click OK.
Log Setup complete: No Load, Input Voltage 28VDC.

:Check 28V Output at 28V Input (No Load)
Log Reading voltage between TP1 and TP13
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 Without Load (16V Input)
Log Decreasing Power Supply voltage to 16VDC (-0 / +0.4V)
setvoltage 16.0
Delay 500

:Check 28V Output at 16V Input (No Load)
Log Reading voltage between TP1 and TP13
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 Without Load (32V Input)
Log Increasing Power Supply voltage to 32VDC (-0.4 / +0V)
setvoltage 32.0
Delay 500

:Check 28V Output at 32V Input (No Load)
Log Reading voltage between TP1 and TP13
Limits 27.6 28.4
Unit V
readchannel 2

:Teardown 4.2 Without Load
Log Decreasing Power Supply voltage to 0VDC
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

# ----------------------------------------------------------------------
# 4.2.4 With Load Phase
# ----------------------------------------------------------------------

:Power Supply Power on 
Critical
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

:Setup 4.2 With Load (28V Input)
Critical
setvoltage 28.0
Delay 500
Prompt Power ON the Digital Load and adjust it to 200W power consumption output. Click OK.
Log Setup complete: With 200W Load, Input Voltage 28VDC.

:Check 28V Output at 28V Input (With Load)
Log Reading voltage between TP1 and TP13
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 With Load (16V Input)
Log Decreasing Power Supply voltage to 16VDC (-0 / +0.4V)
setvoltage 16.0
Delay 500

:Check 28V Output at 16V Input (With Load)
Log Reading voltage between TP1 and TP13
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 With Load (32V Input)
Log Increasing Power Supply voltage to 32VDC (-0.4 / +0V)
setvoltage 32.0
Delay 500

:Check 28V Output at 32V Input (With Load)
Log Reading voltage between TP1 and TP13
Limits 27.6 28.4
Unit V
readchannel 2

:Teardown 4.2 With Load
Log Decreasing Power Supply voltage to 0VDC
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

