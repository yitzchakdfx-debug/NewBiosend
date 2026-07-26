"""System feature flags and configuration, sourced from the environment (.env).

`.env` is the single source of truth (see DOC/.env.example). Non-secret values
fall back to safe defaults; password values must be supplied by the environment.
"""
from __future__ import annotations

from env import get_bool, get_list, get_required_str, get_str

# --- Feature flags ---
SHOW_LIVE_MONITOR: bool = get_bool("SHOW_LIVE_MONITOR", True)
SHOW_SEARCH_BAR: bool = get_bool("SHOW_SEARCH_BAR", False)
SHOW_NIGHT_MODE: bool = get_bool("SHOW_NIGHT_MODE", False)

# --- Pre-test dialog UUT options ---
UUT_TYPES: list[str] = get_list("UUT_TYPES", ["Power main", "Power Ctrl DSP", "Demo UUT"])
LOAD_SERIALS: list[str] = get_list(
    "LOAD_SERIALS",
    ["PRODIGIT-CH1", "PRODIGIT-CH2", "PRODIGIT-CH3", "PRODIGIT-CH4"],
)

# --- Global instrument list (Admin → Connections dialog) ---
INSTRUMENTS: list[str] = get_list(
    "INSTRUMENTS",
    ["Power Supply", "Main Board", "Agilent 34980A", "QEI Arduino", "Outputs Arduino", "I2C Arduino"],
)

# --- Secrets (required in .env) ---
LOG_ENCRYPTION_PASSWORD: str = get_required_str("LOG_ENCRYPTION_PASSWORD")
ADMIN_REPORT_PASSWORD: str = get_required_str("ADMIN_REPORT_PASSWORD")

# --- Default admin seed (used only on first DB init) ---
DEFAULT_ADMIN_USERNAME: str = get_str("DEFAULT_ADMIN_USERNAME", "lior")
DEFAULT_ADMIN_PASSWORD: str = get_required_str("DEFAULT_ADMIN_PASSWORD")
DEFAULT_ADMIN_EMPLOYEE_ID: str = get_str("DEFAULT_ADMIN_EMPLOYEE_ID", "0000")

# --- Station / report identity ---
TESTER_SERIAL_NUMBER: str = get_str("TESTER_SERIAL_NUMBER", "ATE-DFX-001")
INPUT_CONNECTED_TARGET_V: str = get_str("INPUT_CONNECTED_TARGET_V", "24")
INPUT_CONNECTED_TOLERANCE_PCT: str = get_str("INPUT_CONNECTED_TOLERANCE_PCT", "5")
# Fallback load resistance per band, used only when a partial run reaches a
# measurement step without its Setup step having set the resistance. The `.tst`
# script remains the source of truth. Spec Rev.1.1 (2026-07-15) §1.1.7.2/§1.1.7.3:
# Low Load = 5.76 ohm (24V^2 / 5.76 = 100 W), Burn-In = 2.0 ohm (~288 W).
LOAD_RESISTANCE_50W_OHM: str = get_str("LOAD_RESISTANCE_50W_OHM", "12.5")
LOAD_RESISTANCE_100W_OHM: str = get_str("LOAD_RESISTANCE_100W_OHM", "5.76")
LOAD_RESISTANCE_300W_OHM: str = get_str("LOAD_RESISTANCE_300W_OHM", "2.0")

# Electronic-load model reported in the CAMSTAR XML <TestLoad><Model> node.
TEST_LOAD_MODEL: str = get_str("TEST_LOAD_MODEL", "3316G")

# --- Hardware backend ---
HARDWARE_BACKEND: str = get_str("HARDWARE_BACKEND", "mock").strip().lower()
PRODIGIT_VISA_RESOURCE: str = get_str("PRODIGIT_VISA_RESOURCE", "")
PRODIGIT_VISA_BACKEND: str = get_str("PRODIGIT_VISA_BACKEND", "")
PRODIGIT_VISA_TIMEOUT_MS: str = get_str("PRODIGIT_VISA_TIMEOUT_MS", "5000")
# Shorter timeout used only by the pre-test channel scan, where a responsive
# device answers in ~50 ms. Caps non-responding channels/probes instead of
# waiting the full test-time timeout. Tunable without code changes.
PRODIGIT_SCAN_TIMEOUT_MS: str = get_str("PRODIGIT_SCAN_TIMEOUT_MS", "1500")
PRODIGIT_VISA_READ_TERM: str = get_str("PRODIGIT_VISA_READ_TERM", "\r\n")
PRODIGIT_VISA_WRITE_TERM: str = get_str("PRODIGIT_VISA_WRITE_TERM", "\r\n")
PRODIGIT_QUERY_ID: str = get_str("PRODIGIT_QUERY_ID", "*IDN?")
PRODIGIT_QUERY_INPUT_VOLT: str = get_str("PRODIGIT_QUERY_INPUT_VOLT", "MEAS:VOLT?")
PRODIGIT_QUERY_POLARITY: str = get_str("PRODIGIT_QUERY_POLARITY", "MEAS:POL?")
PRODIGIT_QUERY_VOLTAGE: str = get_str("PRODIGIT_QUERY_VOLTAGE", "MEAS:VOLT?")
PRODIGIT_QUERY_CURRENT: str = get_str("PRODIGIT_QUERY_CURRENT", "MEAS:CURR?")
PRODIGIT_QUERY_POWER: str = get_str("PRODIGIT_QUERY_POWER", "MEAS:POW?")
PRODIGIT_SET_MODE: str = get_str("PRODIGIT_SET_MODE", "MODE {mode}")
PRODIGIT_QUERY_MODE: str = get_str("PRODIGIT_QUERY_MODE", "MODE?")
PRODIGIT_SET_LOAD: str = get_str("PRODIGIT_SET_LOAD", "LOAD {state}")
PRODIGIT_QUERY_LOAD: str = get_str("PRODIGIT_QUERY_LOAD", "LOAD?")
PRODIGIT_SET_CURRENT: str = get_str("PRODIGIT_SET_CURRENT", "CC:LOW {value}")
PRODIGIT_QUERY_CURRENT_SET: str = get_str("PRODIGIT_QUERY_CURRENT_SET", "CC:LOW?")
PRODIGIT_SET_RESISTANCE: str = get_str("PRODIGIT_SET_RESISTANCE", "CR:HIGH {value}")
PRODIGIT_QUERY_RESISTANCE_SET: str = get_str("PRODIGIT_QUERY_RESISTANCE_SET", "CR:HIGH?")
PRODIGIT_SET_VOLTAGE: str = get_str("PRODIGIT_SET_VOLTAGE", "CV:HIGH {value}")
PRODIGIT_QUERY_VOLTAGE_SET: str = get_str("PRODIGIT_QUERY_VOLTAGE_SET", "CV:HIGH?")
PRODIGIT_SET_POWER: str = get_str("PRODIGIT_SET_POWER", "CP:HIGH {value}")
PRODIGIT_QUERY_POWER_SET: str = get_str("PRODIGIT_QUERY_POWER_SET", "CP:HIGH?")
PRODIGIT_SET_RELAY: str = get_str("PRODIGIT_SET_RELAY", "")
PRODIGIT_SELECT_SLOT: str = get_str("PRODIGIT_SELECT_SLOT", "")
