# Power Supply EM-5406-00F / EM-5406-00 Load Test
# PartNum: EM-5406-00
# InputTarget: 24
# InputTol: 10

# Test 1 - LED Indication (answer from the pre-test dialog)
:LED Indication Test
Critical
Limits 1.0 1.0
Unit pass
Report LED Indication Test
PromptYesNo Is the Green LED on the UUT panel ON and STEADY?

# Test 2 - Polarity Check: run by the engine, no steps here

# Test 3 - Low Load 100W (CR 5.76 ohm), 1 min, V 22.8-25.2
:Low Load 100W Setup
Hidden
Critical
setmode CR
setresistance 5.76
loadon

:Voltage Measurement [V] - 100W - 1min
Group Low Load 100W - 1 Minute
Critical
Limits 22.8 25.2
Unit V
Report Low Load Stability Test
Quantity Voltage
TimePoint 1 min
Delay 60000 22.8 25.2
measvoltage

:Current Measurement [A] - 100W - 1min
Group Low Load 100W - 1 Minute
Unit A
Report Low Load Stability Test
Quantity Current
meascurrent

:Power Measurement [W] - 100W - 1min
Group Low Load 100W - 1 Minute
Unit W
Report Low Load Stability Test
Quantity Power
measpower

:Resistance Measurement [ohm] - 100W - 1min
Group Low Load 100W - 1 Minute
Unit ohm
Report Low Load Stability Test
Quantity Resistance
getresistance

# Test 4 - Burn-In 300W (CR 2.0 ohm), points at 5/15/30 min
:Burn-In 300W Setup
Hidden
Critical
setmode CR
setresistance 2.0
loadon

:Voltage Measurement [V] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Critical
Limits 22.8 25.2
Unit V
Report Burn-In / Continuous Load Test
Quantity Voltage
Measurement 1
TimePoint 5 min
Delay 300000 22.8 25.2
measvoltage

:Current Measurement [A] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit A
Report Burn-In / Continuous Load Test
Quantity Current
Measurement 1
meascurrent

:Power Measurement [W] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit W
Report Burn-In / Continuous Load Test
Quantity Power
Measurement 1
measpower

:Resistance Measurement [ohm] - 300W - 5min
Group Burn-In 300W - 5 Minutes
Unit ohm
Report Burn-In / Continuous Load Test
Quantity Resistance
Measurement 1
getresistance

:Voltage Measurement [V] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Critical
Limits 22.8 25.2
Unit V
Report Burn-In / Continuous Load Test
Quantity Voltage
Measurement 2
TimePoint 15 min
Delay 600000 22.8 25.2
measvoltage

:Current Measurement [A] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit A
Report Burn-In / Continuous Load Test
Quantity Current
Measurement 2
meascurrent

:Power Measurement [W] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit W
Report Burn-In / Continuous Load Test
Quantity Power
Measurement 2
measpower

:Resistance Measurement [ohm] - 300W - 15min
Group Burn-In 300W - 15 Minutes
Unit ohm
Report Burn-In / Continuous Load Test
Quantity Resistance
Measurement 2
getresistance

:Voltage Measurement [V] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Critical
Limits 22.8 25.2
Unit V
Report Burn-In / Continuous Load Test
Quantity Voltage
Measurement 3
TimePoint 30 min
Delay 900000 22.8 25.2
measvoltage

:Current Measurement [A] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit A
Report Burn-In / Continuous Load Test
Quantity Current
Measurement 3
meascurrent

:Power Measurement [W] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit W
Report Burn-In / Continuous Load Test
Quantity Power
Measurement 3
measpower

:Resistance Measurement [ohm] - 300W - 30min
Group Burn-In 300W - 30 Minutes
Unit ohm
Report Burn-In / Continuous Load Test
Quantity Resistance
Measurement 3
getresistance

:Cleanup
Hidden
Always
loadoff
