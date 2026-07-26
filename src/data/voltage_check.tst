# Simple voltage check — measures the voltage at the load.
# Pass if >= 5 V, Fail if < 5 V.
# PartNum: VOLT-CHECK-001
# InputTarget: 5
# InputTol: 20

:Voltage Check
measvoltage
Limits 5.0 35.0
Unit V
Critical
