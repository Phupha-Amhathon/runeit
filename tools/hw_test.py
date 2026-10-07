#!/usr/bin/env python3
"""End-to-end serial smoke test for RUNEIT (Stage C firmware).

Drives the real menus over the board's USART2 link: login (wrong key, then
the right one) or first-time setup on a blank device, the menu, RETRIEVE_MODE
including malformed ids, GENERATE_MODE (a real password is generated, saved
at id 30 and read back), CHANGE_MK cancel, and invalid menu input.

The generate step writes to flash, so a run leaves entry 30 behind; a later
run answers the overwrite question with 'y'.

Reset the board (RESET button or USB replug) right before running: screens are
printed when a state is entered, not on request. The master key is passed on
the command line and is typed on the terminal in clear, as it is for a human.
A key derivation takes seconds, so waits are per expected text, not per pause.

Usage:
    python3 tools/hw_test.py /dev/ttyACM0 --mk "my long master key"
Requires: pip install pyserial
"""
import argparse
import re
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

        # --- GENERATE_MODE: cancel, input validation, then a real save ---
        text, ok = step(ser, "2", "Id to write")
        check("generate asks for an id", ok is not None, text)
        text, ok = step(ser, "", "== MODE SELECTION ==")
        check("an empty line cancels generate", ok is not None, text)

        step(ser, "2", "Id to write")
        text, ok = step(ser, "abc", "Invalid id")
        check("generate rejects a non-numeric id", ok is not None, text)
        text, ok = step(ser, "31", "Invalid id")
        check("generate rejects an out-of-range id", ok is not None, text)

        text, ok = step(ser, "30", ["Service name", "Overwrite"])
        if ok == "Overwrite":                       # left over from an earlier run
            text, ok = step(ser, "y", "Service name")
        check("generate asks for a service name", ok is not None, text)

        text, ok = step(ser, "x" * 16, "Name must be")
        check("generate rejects a 16-character name", ok is not None, text)
        text, ok = step(ser, "hwtest", "Character classes")
        check("generate asks for character classes", ok is not None, text)
        text, ok = step(ser, "xyz", "Pick at least")
        check("generate rejects classes without l/u/d/s", ok is not None, text)
        text, ok = step(ser, "luds", "Password length")
        check("generate asks for a length", ok is not None, text)
        text, ok = step(ser, "32", "Length must be")
        check("generate rejects a length above 31", ok is not None, text)

        # sampling, then a 16 KB sector erase: allow well over the usual step
        text, ok = step(ser, "20", ["== MODE SELECTION ==", "FAULT"], KDF_TIMEOUT_S)
        check("generate reports the save", "Saved as id 30" in text, text)
        m = re.search(r"Password: (\S+)", text)
        generated = m.group(1) if m else ""
        check("the password is 20 printable characters",
              len(generated) == 20 and all(0x21 <= ord(c) <= 0x7E for c in generated),
              generated)

        text, ok = step(ser, "1", "Enter id to view")
        check("the new entry is listed by retrieve", "30 - hwtest" in text, text)
        text, ok = step(ser, "30", "Enter id to view")
        check("retrieve returns exactly the password that was shown",
              generated != "" and f"hwtest : {generated}" in text, text)
        step(ser, "q", "== MODE SELECTION ==")

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
