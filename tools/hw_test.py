#!/usr/bin/env python3
"""Hardware-in-the-loop smoke test for RUNEIT over its USART2 serial protocol.

Run this against a NUCLEO-F411RE flashed with the current Stage B firmware
(see ../README.md). It drives INIT -> MODE_SELECTION -> RETRIEVE_MODE over
the real UART link and checks the responses against the two auto-seeded
sample entries.

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

        send_line(ser, "2")
        gen_stub = read_until_idle(ser, STEP_TIMEOUT_S)
        check("mode 2 (generate) reports not implemented",
              "Not implemented" in gen_stub, actual=gen_stub)

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
