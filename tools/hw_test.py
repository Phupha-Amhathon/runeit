#!/usr/bin/env python3
"""End-to-end serial smoke test for RUNEIT (Stage C firmware).

Drives the real menus over the board's USART2 link: login (wrong key, then
the right one) or first-time setup on a blank device, the menu, RETRIEVE_MODE
including malformed ids, the not-implemented stub, CHANGE_MK cancel, and
invalid menu input.

Reset the board (RESET button or USB replug) right before running: screens are
printed when a state is entered, not on request. The master key is passed on
the command line and is typed on the terminal in clear, as it is for a human.
A key derivation takes seconds, so waits are per expected text, not per pause.

Usage:
    python3 tools/hw_test.py /dev/ttyACM0 --mk "my long master key"
Requires: pip install pyserial
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("This script needs pyserial: pip install pyserial", file=sys.stderr)
    sys.exit(1)

BAUD = 115200
BOOT_TIMEOUT_S = 8.0
KDF_TIMEOUT_S = 60.0     # derivation is 1-7 s depending on build and iteration count
STEP_TIMEOUT_S = 4.0

_results = []


def check(name, condition, actual=""):
    _results.append(condition)
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        print(f"       actual response: {actual!r}")


def read_until(ser, patterns, timeout_s):
    """Reads until any of patterns appears (or timeout). Returns (text, matched_or_None)."""
    if isinstance(patterns, str):
        patterns = [patterns]
    deadline = time.monotonic() + timeout_s
    buf = ""
    while time.monotonic() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk.decode(errors="replace")
            for p in patterns:
                if p in buf:
                    time.sleep(0.15)                     # let the rest of that screen arrive
                    buf += ser.read(ser.in_waiting or 0).decode(errors="replace")
                    return buf, p
    return buf, None


def send_line(ser, text):
    ser.write((text + "\r\n").encode())


def step(ser, line, pattern, timeout_s=STEP_TIMEOUT_S):
    send_line(ser, line)
    return read_until(ser, pattern, timeout_s)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port")
    ap.add_argument("--mk", required=True, help="master key (8-31 printable characters)")
    ap.add_argument("--baud", type=int, default=BAUD)
    args = ap.parse_args()
    mk = args.mk
    if not 8 <= len(mk) <= 30:
        sys.exit("--mk must be 8-30 characters (the test appends one character to make a mismatch)")

    print("Reset the board right before running this.\n")
    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        text, hit = read_until(ser, ["== LOCKED ==", "FIRST TIME SETUP"], BOOT_TIMEOUT_S)
        check("boot shows a login or first-setup screen", hit is not None, text)
        if hit is None:
            sys.exit(1)

        if hit == "FIRST TIME SETUP":
            print("  (blank device: setting the master key)")
            text, ok = step(ser, "short", "Invalid master key")
            check("first setup rejects a too-short key", ok is not None, text)
            step(ser, mk, "Confirm master key")
            text, ok = step(ser, mk + "x", "do not match")
            check("first setup rejects a mismatching confirmation", ok is not None, text)
            step(ser, mk, "Confirm master key")
            text, ok = step(ser, mk, "== MODE SELECTION ==", KDF_TIMEOUT_S)
            check("first setup accepts a matching key and opens the menu", ok is not None, text)
        else:
            text, ok = step(ser, mk + "-wrong", "Wrong master key", KDF_TIMEOUT_S)
            check("wrong master key is rejected", ok is not None, text)
            text, ok = step(ser, mk, "== MODE SELECTION ==", KDF_TIMEOUT_S)
            check("right master key opens the menu", ok is not None, text)

        text, ok = step(ser, "12", "Unknown option")
        check("menu needs the whole line ('12' rejected)", ok is not None, text)

        text, ok = step(ser, "1", "Enter id to view")
        check("retrieve shows the table header", "-- Password table --" in text, text)
        text, ok = step(ser, "abc", "Enter id to view")
        check("retrieve rejects a non-numeric id", "Invalid id" in text and " : " not in text, text)
        text, ok = step(ser, "99", "Enter id to view")
        check("retrieve rejects an out-of-range id", "Invalid id" in text, text)
        text, ok = step(ser, "q", "== MODE SELECTION ==")
        check("'q' returns to the menu", ok is not None, text)

        text, ok = step(ser, "2", "Not implemented")
        check("option 2 (generate) is still the stub", ok is not None, text)
        read_until(ser, "Select:", STEP_TIMEOUT_S)

        text, ok = step(ser, "3", "New master key")
        check("option 3 opens change master key", ok is not None, text)
        text, ok = step(ser, "", "== MODE SELECTION ==")
        check("an empty line cancels change master key", "Cancelled" in text and ok is not None, text)

    passed = sum(_results)
    print(f"\n{passed}/{len(_results)} checks passed")
    print("Not covered here: panic button, change of key (destructive), power cycle. See TESTING.md.")
    sys.exit(0 if passed == len(_results) else 1)


if __name__ == "__main__":
    main()
