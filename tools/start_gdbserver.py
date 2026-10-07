#!/usr/bin/env python3
"""Starts the ST-LINK GDB server standalone, for tools/mk_auth_trace.py and
tools/other_modes_trace.py to attach to.

Finding the two flags this needs took real trial and error on a real board,
both are easy to miss and both make the difference between it working and
failing with a confusing "Unknown MCU target" error:

- It needs -cp pointing at the directory that directly contains
  STM32_Programmer_CLI (".../tools/bin", not ".../tools" - the parent
  folder one level up looks right but is NOT what it wants).
- It needs -d (SWD debug mode) explicitly. Without it, it defaults to
  JTAG, which fails outright on a NUCLEO board's 2-wire SWD-only
  connection - at every frequency it tries, from 9000 kHz down to
  140 kHz, which looks like a signal/frequency problem but isn't one.

This script finds both the gdbserver binary and the STM32CubeProgrammer
install under the usual STM32CubeIDE location and runs it with both flags
already set, so neither gotcha has to be rediscovered again.

Usage:
    python3 tools/start_gdbserver.py
    python3 tools/start_gdbserver.py --port 61234
Then, in another terminal, run tools/mk_auth_trace.py or
tools/other_modes_trace.py --mode ...

Close any open STM32CubeIDE debug session for this board first - only one
program can hold the ST-LINK at a time.
"""
import argparse
import glob
import os
import sys


def locate(glob_pattern, env_var, what):
    env = os.environ.get(env_var)
    if env:
        return env
    candidates = sorted(glob.glob(glob_pattern))
    if candidates:
        return candidates[-1]
    print(f"error: could not find {what}.", file=sys.stderr)
    print(f"       Set {env_var}=/path/to/it, or check your STM32CubeIDE install.",
          file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=61234)
    args = ap.parse_args()

    gdbserver = locate(
        "/opt/st/stm32cubeide_*/plugins/*stlink-gdb-server*/tools/bin/ST-LINK_gdbserver",
        "RUNEIT_GDBSERVER", "ST-LINK_gdbserver")
    cubeprog_bin = locate(
        "/opt/st/stm32cubeide_*/plugins/*cubeprogrammer*/tools/bin",
        "RUNEIT_CUBEPROGRAMMER", "STM32CubeProgrammer's tools/bin directory")

    cmd = [gdbserver, "-cp", cubeprog_bin, "-p", str(args.port), "-e", "-d"]
    print(f"gdbserver : {gdbserver}")
    print(f"-cp       : {cubeprog_bin}")
    print(f"Running: {' '.join(cmd)}")
    print("Close any open STM32CubeIDE debug session for this board first.\n")
    sys.stdout.flush()  # execv replaces this process, so unflushed output is lost
    os.execv(gdbserver, cmd)


if __name__ == "__main__":
    main()
