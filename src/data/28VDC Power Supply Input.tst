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
setvoltage 28.0
Delay 500

:Check 28V Output at 28V Input (No Load)
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 Without Load (16V Input)
setvoltage 16.0
Delay 500

:Check 28V Output at 16V Input (No Load)
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 Without Load (32V Input)
setvoltage 32.0
Delay 500

:Check 28V Output at 32V Input (No Load)
Limits 27.6 28.4
Unit V
readchannel 2

:Teardown 4.2 Without Load
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

:Setup 4.2 With Load (28V Input)
setvoltage 28.0
Delay 500
Prompt Power ON the Digital Load and adjust it to 200W power consumption output.

:Check 28V Output at 28V Input (With Load)
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 With Load (16V Input)
setvoltage 16.0
Delay 500

:Check 28V Output at 16V Input (With Load)
Limits 27.6 28.4
Unit V
readchannel 2

:Setup 4.2 With Load (32V Input)
setvoltage 32.0
Delay 500

:Check 28V Output at 32V Input (With Load)
Limits 27.6 28.4
Unit V
Log Reading voltage between TP1 and TP13
readchannel 2

:Teardown 4.2 With Load
relay 1 off
setvoltage 0.0
Delay 300
Limits -0.4 0.4
readchannel 10

