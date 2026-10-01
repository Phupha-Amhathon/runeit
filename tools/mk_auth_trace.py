#!/usr/bin/env python3
"""Live, paced trace of the MK_AUTH (login) pipeline on RUNEIT, over gdb.

The board's own serial output only ever shows "Access granted" or "Wrong
master key" - what actually happens inside (the typed key, the PBKDF2/HMAC
derivation, the comparison against the value stored in flash) is invisible
from the terminal, and is gone from RAM within milliseconds because
session.c and kdf.c wipe every intermediate buffer before returning. The
only way to see it is a debugger breakpoint at the exact line where each
value is briefly live.

This script drives arm-none-eabi-gdb (attached to the ST-LINK GDB server
that the project already debugs through) so nobody has to type gdb commands
by hand during a demo. It sets four breakpoints across
Src/app/session.c, Src/crypto/kdf.c and Src/app/app.c, and on each hit
prints what was typed, what got computed, what was already stored in flash,
and whether they matched - re-reading flash and RAM fresh at every single
pause, never from a cache. By default it pauses for Enter between steps so
a presenter can narrate; --auto paces on a timer instead.

Scope: MK_AUTH only. The board must already have a valid partition from a
prior FIRST_MEET (serial should show "== LOCKED ==" on reset, not
"FIRST TIME SETUP"). See tools/first_meet_trace.py for that mode instead.

Precondition: the ST-LINK GDB server must already be listening on its own,
standalone - not via an open STM32CubeIDE debug session, which already
holds that port's one client slot. End any open CubeIDE debug session for
this board first, then in its own terminal:

    ST-LINK_gdbserver -p 61234 -e

Also rebuild and reflash from STM32CubeIDE first if Src/ has changed since
Debug/runeit.elf was last built - this script checks that its breakpoints
land in the expected functions and refuses to run against a stale ELF.

A hit breakpoint halts the whole core, including the UART DMA/ISR, so
typing on the serial terminal will visibly pause the instant Enter is
pressed there. That is expected: it is what lets each step be narrated
while the board is provably stopped, not a hang.

Usage:
    python3 tools/mk_auth_trace.py
    python3 tools/mk_auth_trace.py --auto
Requires: pip install pexpect
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gdb_trace_common import (  # noqa: E402
    GdbSession, banner, build_arg_parser, die, dump_base_state, hexstr,
    locate_gdb, pace,
)


def handle_bp1(session):
    length = int(session.read_value("len"))
    mk_bytes = session.read_bytes("mk", length)
    mk_ascii = bytes(mk_bytes).decode("ascii", errors="replace")
    print(banner(1, "TYPED - host sent a master key attempt"))
    print(f"  mk (typed on serial, {length} bytes): {mk_ascii!r}")
    print(f"  mk (hex)                            : {hexstr(mk_bytes)}")
    print("\n".join(dump_base_state(session)))


def handle_bp2(session):
    salt = session.read_bytes("salt", 16)
    iterations = session.read_value("iterations")
    k = session.read_bytes("k", 32)
    auth = session.read_bytes("auth", 32)
    enc = session.read_bytes("enc", 32)
    print(banner(2, "COMPUTED - PBKDF2-HMAC-SHA256 derivation finished"))
    print(f"  salt (from stored header)  : {hexstr(salt)}")
    print(f"  iterations                 : {iterations}")
    print(f"  k    = PBKDF2(mk, salt, N) : {hexstr(k)}")
    print(f"  auth = HMAC(k, 'auth-v1')  : {hexstr(auth)}")
    print(f"  enc  = HMAC(k, 'enc-v1')   : {hexstr(enc)}")
    print("\n".join(dump_base_state(session)))


def handle_bp3(session):
    auth = session.read_bytes("auth", 32)
    header_auth = session.read_bytes("header.auth", 32)
    result = session.read_value("result")
    verdict = "MATCH" if auth == header_auth else "MISMATCH"
    print(banner(3, "COMPARED - checked against the value stored in flash"))
    print(f"  auth (just computed)      : {hexstr(auth)}")
    print(f"  auth (already in flash)   : {hexstr(header_auth)}")
    print(f"  ==> {verdict}")
    print(f"  result                    : {result}")
    print("\n".join(dump_base_state(session)))


def handle_bp4(session):
    before = session.read_value("'app.c'::g_state")
    session.cmd("next")
    after = session.read_value("'app.c'::g_state")
    print(banner(4, "TRANSITION - login accepted, FSM state changes"))
    print(f"  g_state before : {before}")
    print(f"  g_state after  : {after}")
    print("\n".join(dump_base_state(session)))


def main():
    ap = build_arg_parser(__doc__.splitlines()[0])
    args = ap.parse_args()
    auto_mode = args.auto or os.environ.get("RUNEIT_TRACE_AUTO") == "1"

    if not Path(args.elf).is_file():
        die(f"ELF not found: {args.elf}\n       Build the project in STM32CubeIDE first.")

    gdb_path = locate_gdb(args.gdb_path)
    print(f"Using gdb: {gdb_path}")
    print(f"Using ELF: {args.elf}")

    session = GdbSession(gdb_path, args.elf, prompt="(mk-auth-trace) ")
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")

        bp1 = session.set_checked_breakpoint_by_func("Session_Authenticate")
        bp2 = session.set_checked_breakpoint_by_line("kdf.c", 71)
        session.cmd(f"condition {bp2} 'app.c'::g_state == APP_STATE_MK_AUTH")
        bp3 = session.set_checked_breakpoint_by_line("session.c", 136)
        bp4 = session.set_checked_breakpoint_by_line("app.c", 127)
        handlers = {bp1: handle_bp1, bp2: handle_bp2, bp3: handle_bp3, bp4: handle_bp4}

        print("\nAll breakpoints verified against the loaded ELF.")
        print("Type a master key at the board's '== LOCKED ==' serial prompt to begin.")
        print("Note: the whole core halts on each hit, so serial typing will visibly")
        print("pause the instant you press Enter there - that's expected, not a hang.\n")

        while True:
            out = session.continue_and_wait()
            m = re.search(r"Breakpoint (\d+),", out)
            if not m:
                if "exited" in out.lower():
                    print("\nTarget program exited.")
                    break
                print(f"\nUnexpected stop, continuing:\n{out}")
                continue
            handler = handlers.get(m.group(1))
            if handler is None:
                print(f"\nUnknown breakpoint {m.group(1)} hit, continuing.")
                continue
            handler(session)
            pace(auto_mode)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
