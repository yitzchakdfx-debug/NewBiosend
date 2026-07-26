# Automatic Power Supply Test
# Based on: "Automatic Power Supply Test – Specification.pdf"
# PartNum: APS-24V-300W
#
# Notes:
# 1. Runtime already performs input-voltage detection and a polarity gate
#    before the scripted sequence begins.
# 2. Step names containing "50W" / "300W" trigger the batch runner's
#    constant-resistance preset logic.
# 3. Channel mapping below is a placeholder for the current generic driver:
#    - readchannel 2 -> Voltage measurement
#    - readchannel 3 -> Current measurement
#    - readchannel 4 -> Power measurement

:LED Indication Test
Critical
Limits 1.0 1.0
Unit pass
PromptYesNo Is the Green LED on the UUT panel ON and STEADY?

:Polarity Check
Critical
Log Runtime polarity guard completed before scripted test flow
getid

:Low Load Stability Test - 50W
Critical
Log Applying low-load constant resistance (~12.5 ohm / 50W) and soaking for 1 minute
relay 1 on
Delay 1000
Delay 60000
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Burn-In / Continuous Load Test - 300W Setup
Hidden
Critical
Log Applying burn-in constant resistance target (~2.2 ohm / 300W)
relay 1 on
Delay 1000

:Burn-In / Continuous Load Test - 300W - 5 Minutes
Hidden
Critical
Log Waiting for 5-minute burn-in measurement point
Delay 300000 22.8 25.2

:Voltage Measurement [V] - 300W - 5min
Group Burn-In 300W — 5 Minutes
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Current Measurement [I] - 300W - 5min
Group Burn-In 300W — 5 Minutes
Limits 12.125 12.875
Unit A
Retry 1
readchannel 3

:Power Measurement [W] - 300W - 5min
Group Burn-In 300W — 5 Minutes
Limits 0 1000
Unit W
readchannel 4

:Burn-In / Continuous Load Test - 300W - 15 Minutes
Hidden
Critical
Log Waiting for 15-minute burn-in measurement point
Delay 600000 22.8 25.2

:Voltage Measurement [V] - 300W - 15min
Group Burn-In 300W — 15 Minutes
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Current Measurement [I] - 300W - 15min
Group Burn-In 300W — 15 Minutes
Limits 12.125 12.875
Unit A
Retry 1
readchannel 3

:Power Measurement [W] - 300W - 15min
Group Burn-In 300W — 15 Minutes
Limits 0 1000
Unit W
readchannel 4

:Burn-In / Continuous Load Test - 300W - 30 Minutes
Hidden
Critical
Log Waiting for 30-minute burn-in measurement point
Delay 900000 22.8 25.2

:Voltage Measurement [V] - 300W - 30min
Group Burn-In 300W — 30 Minutes
Limits 22.8 25.2
Unit V
Retry 1
readchannel 2

:Current Measurement [I] - 300W - 30min
Group Burn-In 300W — 30 Minutes
Limits 12.125 12.875
Unit A
Retry 1
readchannel 3

:Power Measurement [W] - 300W - 30min
Group Burn-In 300W — 30 Minutes
Limits 0 1000
Unit W
readchannel 4

:Cleanup
Hidden
Log Releasing load and returning outputs to idle
relay 1 off
setvoltage 0.0
Delay 300
