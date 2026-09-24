#!/usr/bin/env python3
"""Hardware-in-the-loop smoke test for RUNEIT over its USART2 serial protocol.

Run this against a NUCLEO-F411RE flashed with the current firmware (see
../README.md). It drives INIT -> MODE_SELECTION -> RETRIEVE_MODE over the
real UART link and checks the responses against the two auto-seeded sample
entries, then exercises GENERATE_MODE: it saves a generated password at id
30 and reads it back through RETRIEVE_MODE. That save commits to flash, so a
run leaves entry 30 behind (a later run asks to overwrite it and says yes).

IMPORTANT: reset the board (press the black RESET button, or unplug/replug
USB) immediately before running this script. The firmware only prints the
MODE_SELECTION menu when it *enters* that state, not on demand -- if the
board is already sitting idle at the menu from an earlier run, this script
has no way to ask it to repeat that prompt, so it needs a fresh boot to
observe.

This script cannot press the PA10 panic button, and cannot power-cycle the
board to check partition persistence -- see ../TESTING.md for those manual
checks.

Usage:
    python3 tools/hw_test.py /dev/ttyACM0
    python3 tools/hw_test.py COM5

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
BOOT_TIMEOUT_S = 5.0    # first read after reset: flash may still be erasing on first-ever boot
STEP_TIMEOUT_S = 3.0
QUIET_S = 0.3           # consider a response complete after this much silence

_results = []


def check(name, condition, actual=None, expected=None):
    status = "PASS" if condition else "FAIL"
    _results.append((name, condition))
    line = f"[{status}] {name}"
    if not condition:
        if expected is not None:
            line += f"\n       expected to contain: {expected!r}"
        if actual is not None:
            line += f"\n       actual response:      {actual!r}"
    print(line)


def read_until_idle(ser, max_wait_s, quiet_s=QUIET_S):
    """Reads from ser until no new bytes arrive for quiet_s, or max_wait_s elapses."""
    deadline = time.monotonic() + max_wait_s
    buf = b""
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            last_data = time.monotonic()
        elif (time.monotonic() - last_data) >= quiet_s:
            break
    return buf.decode(errors="replace")


def read_until_text(ser, needle, max_wait_s, quiet_s=QUIET_S):
    """Reads until needle has arrived, then until quiet_s of silence.

    Unlike read_until_idle() this does not give up during a long silent
    stretch, such as the 16 KB sector erase inside a save.
    """
    deadline = time.monotonic() + max_wait_s
    buf = b""
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            last_data = time.monotonic()
        elif needle.encode() in buf and (time.monotonic() - last_data) >= quiet_s:
            break
    return buf.decode(errors="replace")


def send_line(ser, text):
    ser.write((text + "\r\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", help="serial device, e.g. /dev/ttyACM0 or COM5")
    parser.add_argument("--baud", type=int, default=BAUD)
    args = parser.parse_args()

    print("Make sure you just reset the board (RESET button or USB replug) -- ")
    print("this script needs to observe a fresh boot.\n")
    print(f"Opening {args.port} @ {args.baud} 8N1 ...")

    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        boot_text = read_until_idle(ser, BOOT_TIMEOUT_S)
        check("boot reaches MODE_SELECTION", "== MODE SELECTION ==" in boot_text,
              actual=boot_text, expected="== MODE SELECTION ==")

        send_line(ser, "1")
        listing = read_until_idle(ser, STEP_TIMEOUT_S)
        check("retrieve: table lists 'example.com'", "example.com" in listing, actual=listing)
        check("retrieve: table lists 'email'", "email" in listing, actual=listing)

        send_line(ser, "0")
        entry0 = read_until_idle(ser, STEP_TIMEOUT_S)
        check("retrieve: id 0 shows seeded password 'hunter2'", "hunter2" in entry0,
              actual=entry0, expected="example.com : hunter2")

        send_line(ser, "99")
        bad_id = read_until_idle(ser, STEP_TIMEOUT_S)
        check("retrieve: out-of-range id is rejected", "Invalid id" in bad_id, actual=bad_id)

        send_line(ser, "q")
        back_to_menu = read_until_idle(ser, STEP_TIMEOUT_S)
        check("retrieve: 'q' returns to MODE_SELECTION",
              "== MODE SELECTION ==" in back_to_menu, actual=back_to_menu)

        # GENERATE_MODE: cancel path first, then a real save that is read back
        send_line(ser, "2")
        id_prompt = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: asks for an id", "Id to write" in id_prompt, actual=id_prompt)

        send_line(ser, "")
        cancelled = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: empty line cancels back to MODE_SELECTION",
              "== MODE SELECTION ==" in cancelled, actual=cancelled)

        send_line(ser, "2")
        read_until_idle(ser, STEP_TIMEOUT_S)
        send_line(ser, "30")
        name_prompt = read_until_idle(ser, STEP_TIMEOUT_S)
        if "Overwrite" in name_prompt:  # id 30 is left over from an earlier run
            send_line(ser, "y")
            name_prompt = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: asks for a service name", "Service name" in name_prompt, actual=name_prompt)

        send_line(ser, "hwtest")
        classes_prompt = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: asks for character classes", "Character classes" in classes_prompt,
              actual=classes_prompt)

        send_line(ser, "xyz")
        bad_classes = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: classes without l/u/d/s are rejected", "Pick at least" in bad_classes,
              actual=bad_classes)

        send_line(ser, "luds")
        length_prompt = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: asks for a length", "Password length" in length_prompt, actual=length_prompt)

        send_line(ser, "32")
        bad_length = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: length above 31 is rejected", "Length must be" in bad_length,
              actual=bad_length)

        send_line(ser, "20")
        # sampling, then a 16 KB sector erase (silent), then INIT and the menu
        saved = read_until_text(ser, "== MODE SELECTION ==", 10.0)
        check("generate: reports the save", "Saved as id 30" in saved, actual=saved)
        match = re.search(r"Password: (\S+)", saved)
        generated = match.group(1) if match else ""
        check("generate: password is 20 printable characters",
              len(generated) == 20 and all(0x21 <= ord(c) <= 0x7E for c in generated),
              actual=generated)
        check("generate: returns to MODE_SELECTION afterwards",
              "== MODE SELECTION ==" in saved, actual=saved)

        send_line(ser, "1")
        listing2 = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: new entry appears in the retrieve listing",
              "30 - hwtest" in listing2, actual=listing2)
        check("generate: older entries survived the commit",
              "example.com" in listing2 and "email" in listing2, actual=listing2)

        send_line(ser, "30")
        entry30 = read_until_idle(ser, STEP_TIMEOUT_S)
        check("generate: retrieve returns exactly the password that was shown",
              generated != "" and f"hwtest : {generated}" in entry30, actual=entry30, expected=generated)

        send_line(ser, "q")
        read_until_idle(ser, STEP_TIMEOUT_S)

        send_line(ser, "3")
        change_stub = read_until_idle(ser, STEP_TIMEOUT_S)
        check("mode 3 (change MK) reports not implemented",
              "Not implemented" in change_stub, actual=change_stub)

    passed = sum(1 for _, ok in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    print("\nNot covered by this script -- see TESTING.md for manual steps:")
    print("  - PA10 panic button wipes RAM and returns to the menu")
    print("  - Power-cycle persistence of the committed partition")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
