"""
Manual test script for Prodigit 3316G electronic load.

Run from the repo root:
    python test_load_manual.py

Connection settings are read from .env (same file as the app).
You can also override them at the top of this file.
"""

import sys
import time

# ── Connection defaults (override in .env or change here) ─────────────────────
# Placeholder: set the real address in .env via PRODIGIT_VISA_RESOURCE
# (e.g. TCPIP0::<host>::<port>::SOCKET), which overrides these below.
HOST = "0.0.0.0"
PORT = 4001
TIMEOUT_MS = 5000
READ_TERM  = "\r\n"
WRITE_TERM = "\r\n"
BACKEND    = "@py"          # "@py" = pyvisa-py (pure Python), "" = NI-VISA

# Try to read overrides from .env ────────────────────────────────────────────
import os
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_path):
    for _line in open(_env_path, encoding="utf-8"):
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k, _v = _k.strip(), _v.strip()
        if _k == "PRODIGIT_VISA_RESOURCE" and _v.startswith("TCPIP"):
            # TCPIP0::host::port::SOCKET
            parts = _v.split("::")
            if len(parts) >= 3:
                HOST = parts[1]
                try:
                    PORT = int(parts[2])
                except ValueError:
                    pass
        elif _k == "PRODIGIT_VISA_TIMEOUT_MS" and _v:
            try:
                TIMEOUT_MS = int(_v)
            except ValueError:
                pass
        elif _k == "PRODIGIT_VISA_BACKEND":
            BACKEND = _v
        elif _k == "PRODIGIT_VISA_READ_TERM":
            READ_TERM = _v.replace("\\r", "\r").replace("\\n", "\n")
        elif _k == "PRODIGIT_VISA_WRITE_TERM":
            WRITE_TERM = _v.replace("\\r", "\r").replace("\\n", "\n")

# ── Helpers ───────────────────────────────────────────────────────────────────
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
INFO = "\033[94mINFO\033[0m"
WARN = "\033[93mWARN\033[0m"


def header(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def check(label: str, result, expected=None, min_val=None, max_val=None) -> bool:
    if expected is not None:
        ok = str(result).strip().upper() == str(expected).strip().upper()
    elif min_val is not None and max_val is not None:
        try:
            ok = min_val <= float(result) <= max_val
        except (TypeError, ValueError):
            ok = False
    else:
        ok = result is not None
    status = PASS if ok else FAIL
    print(f"  [{status}] {label}: {result}")
    return ok


VERBOSE = True   # set False to hide raw SCPI traffic


def send(inst, cmd: str) -> str:
    try:
        if cmd.endswith("?"):
            if VERBOSE:
                print(f"  \033[90m→ query : {cmd}\033[0m", end=" ", flush=True)
            r = inst.query(cmd).strip()
            if VERBOSE:
                print(f"\033[90m← {r!r}\033[0m")
            return r
        else:
            if VERBOSE:
                print(f"  \033[90m→ write : {cmd}\033[0m")
            inst.write(cmd)
            time.sleep(0.15)
            return "sent"
    except Exception as exc:
        if VERBOSE:
            print()
        return f"ERROR: {exc}"


# ── Menu ──────────────────────────────────────────────────────────────────────
MENU = """
Select test group:
  1  Connection / Identity
  2  Mode (CC / CV / CR / CP)
  3  Resistance set & readback   (CR mode)
  4  Current set & readback      (CC mode)
  5  Load ON / OFF
  6  Measurements  (VOLT / CURR / POW)
  7  Slot selection  (CHAN n)
  8  Full auto sequence  (safe values — CR 12.5 Ω, load ON 3 s then OFF)
  9  Raw SCPI command
  c  Interactive control  (choose mode, load ON, set values live, q = load OFF)
  q  Quit
"""

# ── Interactive set command per mode ──────────────────────────────────────────
_MODE_CONFIG = {
    "CC": ("CC:LOW",  "CC:LOW?",  "A",   "current"),
    "CV": ("CV:HIGH", "CV:HIGH?", "V",   "voltage"),
    "CR": ("CR:HIGH", "CR:HIGH?", "Ω",   "resistance"),
    "CP": ("CP:HIGH", "CP:HIGH?", "W",   "power"),
}


def interactive_control(inst) -> None:
    header("c · Interactive control")

    # Choose mode
    print("  Available modes: CC  CV  CR  CP")
    mode = input("  MODE> ").strip().upper()
    if mode not in _MODE_CONFIG:
        print(f"  [{FAIL}] Unknown mode: {mode}")
        return

    set_cmd, query_cmd, unit, label = _MODE_CONFIG[mode]

    # Set mode and turn load ON
    send(inst, f"MODE {mode}")
    time.sleep(0.2)
    send(inst, "LOAD ON")
    time.sleep(0.3)
    print(f"  [{INFO}] MODE {mode} — Load ON")
    print(f"  Enter {label} value [{unit}], or 'q' to stop and turn load OFF.\n")

    while True:
        raw = input(f"  {mode} [{unit}]> ").strip().lower()
        if raw == "q" or raw == "":
            break
        try:
            float(raw)
        except ValueError:
            print(f"  [{WARN}] Not a valid number, try again.")
            continue

        # Send set command
        send(inst, f"{set_cmd} {raw}")
        time.sleep(0.2)

        # Readback configured value
        rb = send(inst, query_cmd)

        # Measurements
        v  = send(inst, "MEAS:VOLT?")
        i  = send(inst, "MEAS:CURR?")
        p  = send(inst, "MEAS:POW?")

        try:
            rbf = float(rb)
            vf, if_, pf = float(v), float(i), float(p)
            print(
                f"    set={rbf:.3f} {unit}  │  "
                f"V={vf:.3f} V  I={if_:.3f} A  P={pf:.1f} W"
            )
        except Exception:
            print(f"    readback={rb}  V={v}  I={i}  P={p}")

    # Turn load OFF on exit
    send(inst, "LOAD OFF")
    time.sleep(0.2)
    print(f"  [{INFO}] Load OFF — back to menu")


def test_identity(inst) -> None:
    header("1 · Connection / Identity")
    r = send(inst, "*IDN?")
    check("*IDN?", r)
    print(f"  [{INFO}] Response: {r}")


def test_mode(inst) -> None:
    header("2 · Mode switching")
    modes = [("CC", 1), ("CV", 2), ("CR", 3), ("CP", 4)]
    for mode_str, _ in modes:
        send(inst, f"MODE {mode_str}")
        time.sleep(0.3)
        r = send(inst, "MODE?")
        check(f"MODE {mode_str} → MODE?", r.upper(), expected=mode_str)
    # restore CR
    send(inst, "MODE CR")
    print(f"  [{INFO}] Restored to CR mode")


def test_resistance(inst) -> None:
    header("3 · Resistance (CR mode)")
    send(inst, "MODE CR")
    time.sleep(0.2)
    for ohm in ["12.5", "5.76", "2.0"]:
        send(inst, f"CR:HIGH {ohm}")
        time.sleep(0.3)
        r = send(inst, "CR:HIGH?")
        try:
            diff_pct = abs(float(r) - float(ohm)) / float(ohm) * 100
            ok = diff_pct < 5.0
            status = PASS if ok else FAIL
            print(f"  [{status}] CR:HIGH {ohm} Ω → readback {r} Ω  (Δ {diff_pct:.1f} %)")
        except Exception:
            print(f"  [{FAIL}] CR:HIGH {ohm} → could not parse: {r}")
    send(inst, "CR:HIGH 12.5")
    print(f"  [{INFO}] Restored to 12.5 Ω")


def test_current(inst) -> None:
    header("4 · Current setpoint (CC mode)")
    send(inst, "MODE CC")
    time.sleep(0.2)
    for amps in ["1.0", "2.5", "5.0"]:
        send(inst, f"CC:LOW {amps}")
        time.sleep(0.3)
        r = send(inst, "CC:LOW?")
        try:
            diff = abs(float(r) - float(amps))
            ok = diff < 0.2
            status = PASS if ok else FAIL
            print(f"  [{status}] CC:LOW {amps} A → readback {r} A  (Δ {diff:.2f} A)")
        except Exception:
            print(f"  [{FAIL}] CC:LOW {amps} → could not parse: {r}")
    send(inst, "MODE CR")
    print(f"  [{INFO}] Restored to CR mode")


def test_load(inst) -> None:
    header("5 · Load ON / OFF")
    for state in ["OFF", "ON", "OFF"]:
        send(inst, f"LOAD {state}")
        time.sleep(0.3)
        r = send(inst, "LOAD?")
        expected = "1" if state == "ON" else "0"
        r_norm = "1" if r.upper() in {"1", "ON"} else "0"
        check(f"LOAD {state} → LOAD?", r, expected=r_norm)
    print(f"  [{INFO}] Load left OFF")


def test_measurements(inst) -> None:
    header("6 · Measurements")
    print(f"  [{WARN}] Make sure UUT is connected and powered before reading.")
    input("  Press Enter to continue...")

    for cmd, label, unit, lo, hi in [
        ("MEAS:VOLT?", "Voltage",  "V",   0.0, 35.0),
        ("MEAS:CURR?", "Current",  "A",   0.0, 20.0),
        ("MEAS:POW?",  "Power",    "W",   0.0, 400.0),
    ]:
        r = send(inst, cmd)
        try:
            val = float(r)
            ok = lo <= val <= hi
            status = PASS if ok else FAIL
            print(f"  [{status}] {cmd} = {val:.3f} {unit}  (range {lo}–{hi})")
        except Exception:
            print(f"  [{FAIL}] {cmd} → could not parse: {r}")


def test_slot(inst) -> None:
    header("7 · Slot selection (CHAN n)")
    for slot in [1, 2, 3, 4]:
        r = send(inst, f"CHAN {slot}")
        check(f"CHAN {slot}", r)
        time.sleep(0.2)
    send(inst, "CHAN 1")
    print(f"  [{INFO}] Restored to slot 1")


def test_auto_sequence(inst) -> None:
    header("8 · Full auto sequence  (CR 12.5 Ω, 3-second load-on burst)")
    steps = [
        ("MODE CR",        "Set CR mode"),
        ("CR:HIGH 12.5",   "Set 12.5 Ω  (~46 W at 24 V)"),
        ("LOAD ON",        "Load ON"),
    ]
    for cmd, label in steps:
        r = send(inst, cmd)
        print(f"  [{INFO}] {label}: {r}")
        time.sleep(0.3)

    print(f"  [{INFO}] Waiting 3 seconds...")
    for _ in range(3):
        time.sleep(1.0)
        v = send(inst, "MEAS:VOLT?")
        i = send(inst, "MEAS:CURR?")
        p = send(inst, "MEAS:POW?")
        try:
            vf, if_, pf = float(v), float(i), float(p)
            v_ok = 21.6 <= vf <= 26.4      # 24 V ±10 %
            status = PASS if v_ok else FAIL
            print(f"    [{status}] V={vf:.2f} V  I={if_:.2f} A  P={pf:.1f} W")
        except Exception:
            print(f"    [{FAIL}] Parse error: V={v} I={i} P={p}")

    send(inst, "LOAD OFF")
    print(f"  [{INFO}] Load OFF — sequence complete")


def test_raw(inst) -> None:
    header("9 · Raw SCPI")
    while True:
        cmd = input("  SCPI> ").strip()
        if not cmd:
            break
        r = send(inst, cmd)
        print(f"  ← {r}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    print("\n" + "=" * 55)
    print("  Prodigit 3316G — Manual Command Test")
    print(f"  Host: {HOST}:{PORT}  Backend: {BACKEND or 'NI-VISA'}")
    print("=" * 55)

    try:
        import pyvisa
    except ImportError:
        print(f"[{FAIL}] pyvisa not installed.  Run:  pip install pyvisa pyvisa-py")
        sys.exit(1)

    print("Connecting...", end=" ", flush=True)
    try:
        rm = pyvisa.ResourceManager(BACKEND) if BACKEND else pyvisa.ResourceManager()
        resource_str = f"TCPIP0::{HOST}::{PORT}::SOCKET"
        inst = rm.open_resource(resource_str, open_timeout=TIMEOUT_MS)
        inst.timeout = TIMEOUT_MS
        inst.read_termination  = READ_TERM
        inst.write_termination = WRITE_TERM
        idn = inst.query("*IDN?").strip()
        print(f"OK\n  Device: {idn}\n")
    except Exception as exc:
        print(f"\n[{FAIL}] Connection failed: {exc}")
        sys.exit(1)

    handlers = {
        "1": test_identity,
        "2": test_mode,
        "3": test_resistance,
        "4": test_current,
        "5": test_load,
        "6": test_measurements,
        "7": test_slot,
        "8": test_auto_sequence,
        "9": test_raw,
        "c": interactive_control,
    }

    try:
        while True:
            print(MENU)
            choice = input("Choice: ").strip().lower()
            if choice == "q":
                break
            if choice in handlers:
                try:
                    handlers[choice](inst)
                except Exception as exc:
                    print(f"  [{FAIL}] Unexpected error: {exc}")
            else:
                print("  Invalid choice.")
    finally:
        try:
            inst.close()
        except Exception:
            pass
        print("\nDisconnected. Bye.")


if __name__ == "__main__":
    main()
