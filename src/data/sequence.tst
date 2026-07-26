# Default DFX ATE script - a minimal working sequence used when no
# other script has been loaded via the "Load Script" ribbon button.
# Edit this file in-app via "Edit Test File", or pick another .tst
# from the file picker.
# PartNum: BIRD-DEMO-001

:Power Supply Init
Critical
setvoltage 5.0
Delay 200

:5V Output Voltage
Limits 4.95 5.15
Unit V
readchannel 0

:Cleanup
setvoltage 0.0
