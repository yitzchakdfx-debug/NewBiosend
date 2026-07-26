# Automatic Power Supply Test - Demo
# Fast demo version of the production-oriented power-supply test.
# PartNum: APS-24V-300W-DEMO
#
# Same flow as the full script, but delays are shortened for GUI/demo use.

:Polarity Check
Critical
Log Runtime polarity guard completed before scripted test flow
getid

:Low Load Stability Test - 50W Setup
Critical
Log Applying low-load constant resistance target (~12.5 ohm / 50W)
Delay 300

:Low Load Stability Test - 50W Duration
Log Demo soak at 50W
Delay 1500

:Voltage Measurement [V] - 50W
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Current Measurement [I] - 50W
Limits 12.125 12.875
Unit A
Retry 1
readchannel 3

:Power Measurement [W] - 50W Reference
Limits 0 1000
Unit W
readchannel 4

:Burn-In / Continuous Load Test - 300W Setup
Critical
Log Applying burn-in constant resistance target (~2.2 ohm / 300W)
Delay 300

:Burn-In / Continuous Load Test - 300W - 5 Minutes
Log Demo wait for 5-minute checkpoint
Delay 1500

:Voltage Measurement [V] - 300W - 5min
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Current Measurement [I] - 300W - 5min
Limits 12.125 12.875
Unit A
Retry 1
readchannel 3

:Power Measurement [W] - 300W - 5min
Limits 0 1000
Unit W
readchannel 4

:Burn-In / Continuous Load Test - 300W - 15 Minutes
Log Demo wait for 15-minute checkpoint
Delay 1500

:Voltage Measurement [V] - 300W - 15min
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Current Measurement [I] - 300W - 15min
Limits 12.125 12.875
Unit A
Retry 1
readchannel 3

:Power Measurement [W] - 300W - 15min
Limits 0 1000
Unit W
readchannel 4

:Burn-In / Continuous Load Test - 300W - 30 Minutes
Log Demo wait for 30-minute checkpoint
Delay 1500

:Voltage Measurement [V] - 300W - 30min
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Current Measurement [I] - 300W - 30min
Limits 12.125 12.875
Unit A
Retry 1
readchannel 3

:Power Measurement [W] - 300W - 30min
Limits 0 1000
Unit W
readchannel 4

:Cleanup
Log Releasing load and returning outputs to idle
relay 1 off
setvoltage 0.0
Delay 200
