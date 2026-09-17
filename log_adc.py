#!/usr/bin/env python3
"""
Capture raw ADC values streamed from the Nucleo-F411RE over USART2 and
show how noisy/random the raw ADC reading is.

Firmware sends one line per sample, formatted as:
    T=<0-3> L=<0-3>
(last 2 bits of the temperature-NTC channel and the light-LDR channel).

Usage:
    pip install pyserial matplotlib
    python log_adc.py COM5 --n 10000          (Windows)
    python log_adc.py /dev/ttyACM0 --n 10000  (Linux)

Saves the raw samples to adc_samples.csv and prints/plots basic stats
(min, max, mean, stddev, peak-to-peak, count table, histogram) for each
channel separately.
"""
import argparse
import csv
import re
import sys
from collections import Counter

import serial

LINE_RE = re.compile(r"T=(\d+)\s+L=(\d+)")


def capture(port: str, baud: int, n: int) -> list[tuple[int, int]]:
    samples = []
    with serial.Serial(port, baud, timeout=2) as ser:
        ser.reset_input_buffer()
        while len(samples) < n:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            m = LINE_RE.match(line)
            if not m:
                continue  # skip a torn/garbled line
            samples.append((int(m.group(1)), int(m.group(2))))
            if len(samples) % 200 == 0:
                print(f"\r{len(samples)}/{n} samples", end="", flush=True)
    print()
    return samples


def report_channel(name: str, values: list[int]) -> None:
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    stddev = variance ** 0.5
    lo, hi = min(values), max(values)

    print(f"\n=== {name} ===")
    print(f"n        = {n}")
    print(f"min/max  = {lo} / {hi}  (peak-to-peak = {hi - lo} counts)")
    print(f"mean     = {mean:.3f}")
    print(f"stddev   = {stddev:.3f} counts")

    print("value  count   %")
    counts = Counter(values)
    for value in range(lo, hi + 1):
        c = counts.get(value, 0)
        print(f"{value:5d}  {c:5d}  {100 * c / n:5.1f}%")
    print("(uniform-ish counts across all values = looks random; a value hogging "
          "most of the count = biased/not random)")


def report(samples: list[tuple[int, int]]) -> None:
    temp_vals = [t for t, _ in samples]
    light_vals = [l for _, l in samples]

    report_channel("Temperature (NTC, PA0)", temp_vals)
    report_channel("Light (LDR, PA1)", light_vals)

    with open("adc_samples.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "temp_lsb2", "light_lsb2"])
        w.writerows((i, t, l) for i, (t, l) in enumerate(samples))
    print("\nsaved: adc_samples.csv")

    try:
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 2, figsize=(11, 7))
        for row, (name, values) in enumerate(
            [("Temperature", temp_vals), ("Light", light_vals)]
        ):
            lo, hi = min(values), max(values)
            axes[row][0].plot(values, linewidth=0.7)
            axes[row][0].set_title(f"{name} vs sample #")
            axes[row][0].set_xlabel("sample")
            axes[row][0].set_ylabel("value (0-3)")

            axes[row][1].hist(values, bins=range(lo, hi + 2))
            axes[row][1].set_title(f"{name} histogram")
            axes[row][1].set_xlabel("value")
            axes[row][1].set_ylabel("count")

        fig.tight_layout()
        fig.savefig("adc_samples.png", dpi=150)
        print("saved: adc_samples.png")
        plt.show()
    except ImportError:
        print("(matplotlib not installed - skipping plot, CSV still saved)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("port", help="serial port, e.g. COM5 or /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--n", type=int, default=10000, help="number of samples to capture")
    args = ap.parse_args()

    try:
        samples = capture(args.port, args.baud, args.n)
    except serial.SerialException as e:
        print(f"error opening {args.port}: {e}", file=sys.stderr)
        sys.exit(1)

    report(samples)


if __name__ == "__main__":
    main()
