# Demo system test for DFX ATE
# Exercises Critical, Limits, Unit, Delay, and all mock commands.
# PartNum: BAS1540050320

:Power Supply Power on 
Critical
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

:Power Supply Init
Critical
setvoltage 12.0
Delay 300

:Check 12VDC Output Without Load
Limits 11.6 12.4
Unit V
readchannel 1

:Teardown 4.1 Without Load
relay 1 off
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

:Power Supply Power on 
Critical
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

:Setup 4.1 With Load
Prompt Power ON the Digital Load and adjust it to 105mA output current.
setvoltage 12.0

:Check 12VDC Output With Load
Limits 11.6 12.4
Unit V
readchannel 1

:Teardown 4.1 With Load
relay 1 off
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10



