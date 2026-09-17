"""
Drives the ADC password generator over UART: automatically answers 'y'
to all 4 character-type questions and a fixed length, N times in a row,
then analyzes the resulting batch of passwords for character-level bias
(same method used on adc_samples.csv earlier in this project).

Usage:
    pip install pyserial
    python3 collect_passwords.py /dev/ttyACM0 --count 150 --length 32

Outputs:
    collect_raw.log   - the exact raw byte stream from the board
    passwords.csv     - one parsed password per line
    a printed report  - class balance, character frequency, entropy
"""
import argparse
import collections
import math
import re
import sys
import time

import serial

PROMPTS = [
    "Include lowercase a-z? (y/n): ",
    "Include uppercase A-Z? (y/n): ",
    "Include digits 0-9? (y/n): ",
    "Include symbols !@#$...? (y/n): ",
]
LENGTH_PROMPT = "Password length (1-250): "

CLASSES = {
    "lower":  "abcdefghijklmnopqrstuvwxyz",
    "upper":  "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "digit":  "0123456789",
    "symbol": "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
}


KNOWN_PROMPTS = PROMPTS + [LENGTH_PROMPT]


def collect(port, baud, count, length, timeout, logfile):
    """Drives the device through `count` full rounds, regardless of
    which of the 5 known prompts it happens to be sitting at when this
    connects. The device always leaves its most recently sent prompt
    text as the last bytes on the wire whenever it is blocked waiting
    for a reply, so matching the tail of the accumulated buffer against
    every known prompt (rather than assuming a fixed starting point)
    resyncs to whatever state a prior manual session left it in. """
    ser = serial.Serial(port, baud, timeout=0.1)
    time.sleep(0.3)

    full_log = bytearray()   # everything ever received - kept for parsing
    tail = bytearray()       # cleared after every match, used only to
                              # detect the *next* prompt boundary, so a
                              # stale match can't be answered twice
                              # before new bytes have actually arrived
    starts_seen = 0

    # Whatever prompt the board is currently blocked on was already sent
    # before this connection opened - it will not be repeated, so just
    # listening first sees nothing. Probe blind instead: 'y' answers any
    # of the 4 yes/no prompts; if that produced no reply, the board must
    # be sitting at the length prompt instead (where a bare 'y' is a
    # harmless no-op, since it is not a digit), so '1' unsticks that case.
    for probe in (b"y\n", b"1\n"):
        ser.write(probe)
        probe_deadline = time.time() + 2.0
        got_any = False
        while time.time() < probe_deadline:
            chunk = ser.read(256)
            if chunk:
                full_log.extend(chunk)
                tail.extend(chunk)
                got_any = True
                break
        if got_any:
            break
    else:
        print("No response from the board after probing - check the "
              "board is running (not paused in a debugger) and the port "
              "is correct.")
        with open(logfile, "wb") as f:
            f.write(full_log)
        ser.close()
        return bytes(full_log)

    deadline = time.time() + timeout

    while starts_seen <= count:
        matched = None
        for p in KNOWN_PROMPTS:
            if tail.endswith(p.encode()):
                matched = p
                break

        if matched is not None:
            if matched == PROMPTS[0]:
                starts_seen += 1
                if starts_seen > count:
                    break
                if starts_seen % 10 == 0:
                    print(f"...{starts_seen}/{count} rounds complete")
            if matched == LENGTH_PROMPT:
                ser.write(f"{length}\n".encode())
            else:
                ser.write(b"y\n")
            tail.clear()
            deadline = time.time() + timeout
            continue

        if time.time() > deadline:
            print(f"Timed out waiting for a known prompt after "
                  f"{starts_seen} round(s). Partial log saved.")
            break

        chunk = ser.read(256)
        if chunk:
            full_log.extend(chunk)
            tail.extend(chunk)

    ser.close()

    with open(logfile, "wb") as f:
        f.write(full_log)
    print(f"Raw session saved to {logfile} ({len(full_log)} bytes)")
    return bytes(full_log)


def parse(raw):
    text = raw.decode(errors="replace")
    chunks = text.split(PROMPTS[0])
    passwords = []
    faults = 0
    for chunk in chunks[1:]:
        m = re.search(r"Password length \(1-250\): \d+\r?\n(.*?)\n", chunk, re.S)
        if not m:
            continue
        result = m.group(1).rstrip("\r")
        if "ENTROPY SOURCE FAULT" in result:
            faults += 1
        else:
            passwords.append(result)
    return passwords, faults


def report(passwords, expected_len):
    print(f"\nParsed {len(passwords)} passwords")

    lens = [len(p) for p in passwords]
    bad = [l for l in lens if l != expected_len]
    if bad:
        print(f"WARNING: {len(bad)} of {len(passwords)} passwords do not "
              f"match the requested length {expected_len}: {bad[:10]}"
              f"{' ...' if len(bad) > 10 else ''}")
    else:
        print(f"All {len(passwords)} passwords match the requested "
              f"length ({expected_len}).")

    all_chars = "".join(passwords)
    n = len(all_chars)
    if n == 0:
        print("No characters to analyze.")
        return
    counts = collections.Counter(all_chars)
    charset_len = sum(len(c) for c in CLASSES.values())

    print(f"\ntotal characters: {n}  distinct seen: {len(counts)} of {charset_len}")
    print(f"expected per class (uniform): "
          + ", ".join(f"{name} {100*len(chars)/charset_len:.1f}%"
                       for name, chars in CLASSES.items()))
    print("actual per class:            "
          + ", ".join(
              f"{name} {100*sum(counts[ch] for ch in chars)/n:.1f}%"
              for name, chars in CLASSES.items()))

    H = -sum((v / n) * math.log2(v / n) for v in counts.values())
    ideal = math.log2(charset_len)
    print(f"\nShannon entropy: {H:.2f} bit/char (ideal {ideal:.2f} bit/char)")
    print("(small n biases this estimate low - trust it more as n grows; "
          f"n={n} gives ~{n/charset_len:.1f} expected hits per character)")

    print("\ntop 10 most frequent characters:")
    for ch, cnt in counts.most_common(10):
        print(f"  {ch!r}: {cnt} ({100*cnt/n:.2f}%, expected {100/charset_len:.2f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--count", type=int, default=150,
                     help="number of passwords to generate")
    ap.add_argument("--length", type=int, default=32)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=5.0,
                     help="seconds to wait for each prompt before giving up")
    ap.add_argument("--log", default="collect_raw.log")
    ap.add_argument("--from-log", default=None,
                     help="skip collection, analyze an existing raw log instead")
    args = ap.parse_args()

    if args.from_log:
        raw = open(args.from_log, "rb").read()
    else:
        raw = collect(args.port, args.baud, args.count, args.length,
                       args.timeout, args.log)

    passwords, faults = parse(raw)
    print(f"faults reported by device: {faults}")

    with open("passwords.csv", "w") as f:
        f.write("password\n")
        for p in passwords:
            f.write(p.replace(",", "\\,") + "\n")
    print("Saved passwords.csv")

    report(passwords, args.length)


if __name__ == "__main__":
    main()
