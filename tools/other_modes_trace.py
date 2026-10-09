#!/usr/bin/env python3
"""Live, paced trace of RUNEIT's other four modes, over gdb: FIRST_MEET,
CHANGE_MK_MODE, RETRIEVE_MODE, GENERATE_MODE. Companion to
tools/mk_auth_trace.py, which covers MK_AUTH (login) on its own - try that
one first; this file covers everything else.

Pick exactly ONE mode per run with --mode. Between them these four modes
use 4 + 5 + 3 + 3 = 15 gdb breakpoints, far more than the Cortex-M4's
hardware breakpoint unit can hold at once (it typically has 6 instruction
comparators, and flash-resident code needs a hardware comparator per
breakpoint - there's no practical software-breakpoint path there). Each
mode's own set is small enough on its own (5 at most, for change_mk), so
this tool only ever arms one mode's breakpoints per invocation, never all
four together.

  --mode first_meet   First-time master-key setup. Needs a BLANK device
                       (serial shows "== FIRST TIME SETUP ==", not
                       "== LOCKED =="). The only mode here with that
                       precondition - every other mode needs a session
                       that's already open.
  --mode change_mk     Changing the master key. Needs an open session:
                       log in first, then choose "Change master key".
  --mode retrieve       Decrypting and showing a password. Needs an open
                       session: log in first, then choose "Retrieve
                       password". This is the direct answer to "we store a
                       password, then retrieve it - how do we know it's
                       correct?"
  --mode generate       ADC-noise password generation and save. Needs an
                       open session AND the id/name/class/length prompts
                       already answered before starting this tool - see
                       tools/other_modes_trace.md. Paces asymmetrically:
                       only the first sampling round pauses with full
                       detail, later rounds scroll by as one-line
                       summaries, because a full password needs many
                       rounds.

Precondition (all modes): the ST-LINK GDB server must already be listening
on its own, standalone - not via an open STM32CubeIDE debug session, which
already holds that port's one client slot. End any open CubeIDE debug
session for this board first, then in its own terminal:

    ST-LINK_gdbserver -p 61234 -e

Also rebuild and reflash from STM32CubeIDE first if Src/ has changed since
Debug/runeit.elf was last built - this script checks that its breakpoints
land in the expected functions/lines and refuses to run against a stale or
differently-optimized ELF.

A hit breakpoint halts the whole core, so typing on the serial terminal
will visibly pause the instant Enter is pressed there. That is expected.

Usage:
    python3 tools/other_modes_trace.py --mode first_meet
    python3 tools/other_modes_trace.py --mode change_mk
    python3 tools/other_modes_trace.py --mode retrieve
    python3 tools/other_modes_trace.py --mode generate --auto
Requires: pip install pexpect
See tools/other_modes_trace.md for detailed preconditions, steps and
troubleshooting, one section per mode.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gdb_trace_common import (  # noqa: E402
    GdbSession, LAYOUT, banner, build_arg_parser, die, dump_base_state, hexstr,
    locate_gdb, pace,
)

PARTITION_HEADER_SIZE = 112
PWD_ENTRY_SIZE = 48  # char name[16] + char password[32], Inc/app/password_table.h
PWD_TABLE_MAX_ENTRIES = 31  # ids 0-30; mode_retrieve.c's ParseId() returns this for "invalid"
ENTROPY_BLOCK_SAMPLES = 512  # Inc/crypto/entropy_pool.h


# =============================================================================
# FIRST_MEET
# =============================================================================

def first_meet_bp1(session, auto_mode):
    length = int(session.read_value("len"))
    line_bytes = session.read_bytes("line", length)
    first_len = int(session.read_value("s_first_len"))
    first_bytes = session.read_bytes("s_first", first_len) if first_len else []
    verdict = "MATCH" if (length == first_len and line_bytes == first_bytes) else "MISMATCH"
    print(banner(1, "CONFIRMED - second entry checked against the first"))
    print(f"  first entry  ({first_len} bytes): {hexstr(first_bytes)}")
    print(f"  second entry ({length} bytes)   : {hexstr(line_bytes)}")
    print(f"  ==> {verdict}")
    print("\n".join(dump_base_state(session)))
    print("  note: 'active partition' above is expected to show no valid partition yet -")
    print("  this is the very first key this device has ever had.")


def first_meet_bp2(session, auto_mode):
    salt = session.read_bytes("salt", 16)
    iterations = session.read_value("iterations")
    k = session.read_bytes("k", 32)
    auth = session.read_bytes("auth", 32)
    enc = session.read_bytes("enc", 32)
    print(banner(2, "COMPUTED - PBKDF2-HMAC-SHA256 derivation finished"))
    print(f"  salt (freshly generated)   : {hexstr(salt)}")
    print(f"  iterations                 : {iterations}")
    print(f"  k    = PBKDF2(mk, salt, N) : {hexstr(k)}")
    print(f"  auth = HMAC(k, 'auth-v1')  : {hexstr(auth)}")
    print(f"  enc  = HMAC(k, 'enc-v1')   : {hexstr(enc)}")
    print("\n".join(dump_base_state(session)))


def first_meet_bp3(session, auto_mode):
    salt = session.read_bytes("salt", 16)
    auth = session.read_bytes("auth", 32)
    enc = session.read_bytes("enc", 32)
    print(banner(3, "COMMITTED - the first-ever partition"))
    print(f"  salt written to flash : {hexstr(salt)}")
    print(f"  auth written to flash : {hexstr(auth)}")
    print(f"  enc (new session key) : {hexstr(enc)}")
    print("  note: nothing existed to compare this against - there is no MATCH/MISMATCH")
    print("  here, this is simply the value that just got written.")
    print("\n".join(dump_base_state(session)))


def first_meet_bp4(session, auto_mode):
    before = session.read_value("'app.c'::g_state")
    session.cmd("next")
    after = session.read_value("'app.c'::g_state")
    print(banner(4, "TRANSITION - setup finished, FSM state changes"))
    print(f"  g_state before : {before}")
    print(f"  g_state after  : {after}")
    print("\n".join(dump_base_state(session)))


def setup_first_meet(session):
    bp1 = session.set_checked_breakpoint_by_line("mode_first_meet.c", 63)
    bp2 = session.set_checked_breakpoint_by_line("kdf.c", 78)
    session.cmd(f"condition {bp2} 'app.c'::g_state == APP_STATE_FIRST_MEET")
    bp3 = session.set_checked_breakpoint_by_line("session.c", 202)
    session.cmd(f"condition {bp3} 'app.c'::g_state == APP_STATE_FIRST_MEET")
    bp4 = session.set_checked_breakpoint_by_line("app.c", 153)
    print("Type a master key twice at the board's '== FIRST TIME SETUP ==' serial")
    print("prompt to begin.")
    handlers = {bp1: first_meet_bp1, bp2: first_meet_bp2,
                bp3: first_meet_bp3, bp4: first_meet_bp4}
    return handlers, True  # True = pace() automatically after every handler


# =============================================================================
# CHANGE_MK_MODE
# =============================================================================

# Carries the old session key from bp2 to bp4, where it's shown side by
# side with the newly-derived one.
_change_mk_stash = {}


def change_mk_bp1(session, auto_mode):
    length = int(session.read_value("len"))
    line_bytes = session.read_bytes("line", length)
    new_len = int(session.read_value("s_new_len"))
    new_bytes = session.read_bytes("s_new", new_len) if new_len else []
    verdict = "MATCH" if (length == new_len and line_bytes == new_bytes) else "MISMATCH"
    print(banner(1, "CONFIRMED - second entry checked against the first"))
    print(f"  first entry  ({new_len} bytes): {hexstr(new_bytes)}")
    print(f"  second entry ({length} bytes) : {hexstr(line_bytes)}")
    print(f"  ==> {verdict}")
    print("\n".join(dump_base_state(session)))


def change_mk_bp2(session, auto_mode):
    old_key = session.read_bytes("'session.c'::s_key", 32)
    _change_mk_stash["old_key"] = old_key
    name0 = session.read_bytes("s_work.entries[0].name", 16)
    pwd0 = session.read_bytes("s_work.entries[0].password", 32)
    print(banner(2, "BEFORE - table decrypted with the key you're about to replace"))
    print(f"  current session key (about to be retired) : {hexstr(old_key)}")
    print(f"  entry 0, decrypted under the old key:")
    print(f"    name     : {bytes(name0).decode('ascii', errors='replace')!r}")
    print(f"    password : {bytes(pwd0).decode('ascii', errors='replace')!r}")
    print("\n".join(dump_base_state(session)))


def change_mk_bp3(session, auto_mode):
    salt = session.read_bytes("salt", 16)
    iterations = session.read_value("iterations")
    k = session.read_bytes("k", 32)
    auth = session.read_bytes("auth", 32)
    enc = session.read_bytes("enc", 32)
    print(banner(3, "COMPUTED - new key material being derived"))
    print(f"  salt (freshly generated)   : {hexstr(salt)}")
    print(f"  iterations                 : {iterations}")
    print(f"  k    = PBKDF2(mk, salt, N) : {hexstr(k)}")
    print(f"  auth = HMAC(k, 'auth-v1')  : {hexstr(auth)}")
    print(f"  enc  = HMAC(k, 'enc-v1')   : {hexstr(enc)}")
    print("\n".join(dump_base_state(session)))


def change_mk_bp4(session, auto_mode):
    salt = session.read_bytes("salt", 16)
    auth = session.read_bytes("auth", 32)
    enc = session.read_bytes("enc", 32)
    old_key = _change_mk_stash.get("old_key")
    print(banner(4, "COMMITTED - table re-encrypted under the new key"))
    if old_key is not None:
        print(f"  old session key (retired)      : {hexstr(old_key)}")
    print(f"  new session key (enc, just now) : {hexstr(enc)}")
    print(f"  salt written to flash           : {hexstr(salt)}")
    print(f"  auth written to flash           : {hexstr(auth)}")
    print("\n".join(dump_base_state(session)))


def change_mk_bp5(session, auto_mode):
    before = session.read_value("'app.c'::g_state")
    session.cmd("next")
    after = session.read_value("'app.c'::g_state")
    print(banner(5, "TRANSITION - mode finished (done or cancelled)"))
    print(f"  g_state before : {before}")
    print(f"  g_state after  : {after}")
    print("  note: this line is reached on a plain cancel too, not only success -")
    print("  check block [4] above to see whether a commit actually happened.")
    print("\n".join(dump_base_state(session)))


def setup_change_mk(session):
    bp1 = session.set_checked_breakpoint_by_line("mode_change_mk.c", 87)
    bp2 = session.set_checked_breakpoint_by_line("mode_change_mk.c", 66)
    bp3 = session.set_checked_breakpoint_by_line("kdf.c", 78)
    session.cmd(f"condition {bp3} 'app.c'::g_state == APP_STATE_CHANGE_MK_MODE")
    bp4 = session.set_checked_breakpoint_by_line("session.c", 202)
    session.cmd(f"condition {bp4} 'app.c'::g_state == APP_STATE_CHANGE_MK_MODE")
    bp5 = session.set_checked_breakpoint_by_line("app.c", 211)
    print("Log in first over the serial terminal, choose 'Change master key' from")
    print("the menu, then type the new key twice to begin.")
    handlers = {bp1: change_mk_bp1, bp2: change_mk_bp2, bp3: change_mk_bp3,
                bp4: change_mk_bp4, bp5: change_mk_bp5}
    return handlers, True


# =============================================================================
# RETRIEVE_MODE
# =============================================================================

def parse_id(line_str):
    """Python copy of mode_retrieve.c's ParseId(): optional leading spaces,
    then 1-2 decimal digits and nothing else. Anything else is "invalid","""
    s = line_str
    i = 0
    while i < len(s) and s[i] == " ":
        i += 1
    digits = 0
    value = 0
    while i < len(s) and s[i].isdigit():
        value = (value * 10) + int(s[i])
        digits += 1
        i += 1
    if digits == 0 or digits > 2 or i != len(s):
        return PWD_TABLE_MAX_ENTRIES
    return value


def retrieve_bp1(session, auto_mode):
    sector = session.read_value("'partition_store.c'::s_active.sector")
    version = session.read_value("'partition_store.c'::s_active.header.version")
    addr = session.read_int("'partition_store.c'::s_active.addr")
    ciphertext = session.read_bytes(hex(addr + LAYOUT["header_len"]), PWD_ENTRY_SIZE)
    print(banner(1, "BEFORE - ciphertext at rest in flash (entry 0)"))
    print(f"  active partition: sector {sector}, version {version}, @ 0x{addr:08x}")
    print(f"  entry 0 raw bytes, straight from flash (looks like noise):")
    print(f"    {hexstr(ciphertext)}")
    print("\n".join(dump_base_state(session)))


def retrieve_bp2(session, auto_mode):
    plaintext = session.read_bytes("(char*)&s_table", PWD_ENTRY_SIZE)
    name = bytes(plaintext[0:16]).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    pwd = bytes(plaintext[16:48]).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    print(banner(2, "AFTER - the same bytes, decrypted in RAM (entry 0)"))
    print(f"  entry 0 raw bytes, decrypted, same offset as block [1]:")
    print(f"    {hexstr(plaintext)}")
    print(f"  entry 0 decoded: name={name!r} password={pwd!r}")
    print("\n".join(dump_base_state(session)))


def retrieve_bp3(session, auto_mode):
    # Mode_Retrieve_Run() discards USART_Drv_TakeLine()'s returned length
    # with (void), so there is no "len" variable in this frame - unlike
    # mk_auth_trace's and first_meet/change_mk's breakpoints, which sit
    # inside functions that take "len" as a real parameter. LineBuf_Extract
    # NUL-terminates "line" at the real typed length, so split on the NUL
    # instead, same as how name/password are decoded below.
    line_bytes = session.read_bytes("line", 32)
    line_str = bytes(line_bytes).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    entry_id = parse_id(line_str)
    print(banner(3, "SHOWN - the exact RAM content behind what serial just printed"))
    print(f"  you typed: {line_str!r} -> parsed id {entry_id}")
    if entry_id < PWD_TABLE_MAX_ENTRIES:
        name = session.read_bytes(f"s_table.entries[{entry_id}].name", 16)
        pwd = session.read_bytes(f"s_table.entries[{entry_id}].password", 32)
        name_s = bytes(name).split(b"\x00", 1)[0].decode("ascii", errors="replace")
        pwd_s = bytes(pwd).split(b"\x00", 1)[0].decode("ascii", errors="replace")
        print(f"  s_table.entries[{entry_id}] in RAM: name={name_s!r} password={pwd_s!r}")
        print("  this should be exactly what the serial terminal just printed.")
    else:
        print("  id was out of range or malformed - the firmware reports 'Invalid id'.")
    print("\n".join(dump_base_state(session)))


def setup_retrieve(session):
    bp1 = session.set_checked_breakpoint_by_func("Mode_Retrieve_Enter")
    bp2 = session.set_checked_breakpoint_by_line("mode_retrieve.c", 34)
    bp3 = session.set_checked_breakpoint_by_line("mode_retrieve.c", 88)
    print("Log in first over the serial terminal, then choose 'Retrieve password'")
    print("from the menu to begin. If there's no open session, Mode_Retrieve_Enter")
    print("fails before block [2] or [3] can fire - seeing only block [1] in that")
    print("case is expected, not a bug.")
    handlers = {bp1: retrieve_bp1, bp2: retrieve_bp2, bp3: retrieve_bp3}
    return handlers, True


# =============================================================================
# GENERATE_MODE
# =============================================================================

def bias_and_yield(samples):
    ones = sum(s & 0x1 for s in samples)
    bias_pct = 100.0 * ones / len(samples)
    pairs = [(samples[i] & 0x1, samples[i + 1] & 0x1)
             for i in range(0, len(samples) - 1, 2)]
    accepted = sum(1 for a, b in pairs if a != b)
    return bias_pct, accepted, len(pairs)


def generate_absorb(session, channel_name, auto_mode):
    rounds = int(session.read_value("s_rounds"))
    samples = session.read_u16_list("s_samples", ENTROPY_BLOCK_SAMPLES)
    bias_pct, accepted, total_pairs = bias_and_yield(samples)
    chars_done = session.read_value("s_chars_done")
    length = session.read_value("s_length")

    if rounds == 1:
        print(banner(f"round 1, {channel_name}", "RAW - before debiasing"))
        print(f"  {ENTROPY_BLOCK_SAMPLES} raw ADC samples, {channel_name} channel")
        print(f"  first 16 raw samples : {samples[:16]}")
        print(f"  LSB bias this block  : {bias_pct:.1f}% ones "
              f"(expect skewed, not ~50% - README.md measured up to 83/17)")
        print(f"  Von Neumann yield    : {accepted}/{total_pairs} adjacent pairs differ "
              f"(these become debiased bits, the rest are discarded)")
        print(f"  password progress    : {chars_done}/{length} characters so far")
        print("\n".join(dump_base_state(session)))
        pace(auto_mode)
    else:
        print(f"  round {rounds:>2}, {channel_name:<4}: bias {bias_pct:5.1f}%  "
              f"yield {accepted:3d}/{total_pairs}  progress {chars_done}/{length}")


def generate_bp1(session, auto_mode):
    generate_absorb(session, "temp", auto_mode)


def generate_bp2(session, auto_mode):
    generate_absorb(session, "light", auto_mode)


def generate_bp3(session, auto_mode):
    entry_id = int(session.read_value("s_id"))
    name = session.read_bytes("s_name", 16)
    pwd = session.read_bytes("s_pwd", 32)
    name_s = bytes(name).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    pwd_s = bytes(pwd).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    print(banner(3, "SAVED - plaintext in, about to commit to flash"))
    print(f"  entry id {entry_id}: name={name_s!r} password={pwd_s!r}")
    session.cmd("next")  # let Session_Save()/Partition_Store_Commit() finish
    addr = session.read_int("'partition_store.c'::s_active.addr")
    offset = LAYOUT["header_len"] + (entry_id * PWD_ENTRY_SIZE)
    ciphertext = session.read_bytes(hex(addr + offset), PWD_ENTRY_SIZE)
    print(f"  same entry, now on flash, encrypted (looks like noise):")
    print(f"    {hexstr(ciphertext)}")
    print("  run 'other_modes_trace.py --mode retrieve' afterwards to see this decrypt")
    print("  back to the same plaintext shown above - the 'how do we know it's")
    print("  correct' proof.")
    print("\n".join(dump_base_state(session)))
    pace(auto_mode)


def setup_generate(session):
    bp1 = session.set_checked_breakpoint_by_line("mode_generate.c", 370)
    bp2 = session.set_checked_breakpoint_by_line("mode_generate.c", 386)
    bp3 = session.set_checked_breakpoint_by_line("mode_generate.c", 424)
    print("Log in over the serial terminal, choose 'Generate password', and answer")
    print("the id/name/class/length prompts first - see other_modes_trace.md. Once")
    print("sampling starts, round 1 pauses for narration, later rounds scroll by.")
    handlers = {bp1: generate_bp1, bp2: generate_bp2, bp3: generate_bp3}
    return handlers, False  # False = handlers pace themselves, don't double-pace


# =============================================================================
# main
# =============================================================================

MODE_SETUP = {
    "first_meet": setup_first_meet,
    "change_mk": setup_change_mk,
    "retrieve": setup_retrieve,
    "generate": setup_generate,
}

MODE_PROMPT = {
    "first_meet": "(other-modes-trace:first_meet) ",
    "change_mk": "(other-modes-trace:change_mk) ",
    "retrieve": "(other-modes-trace:retrieve) ",
    "generate": "(other-modes-trace:generate) ",
}


def main():
    ap = build_arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--mode", required=True, choices=sorted(MODE_SETUP.keys()),
                     help="which mode to trace - only one can be armed per run "
                          "(see the module docstring for why)")
    args = ap.parse_args()
    auto_mode = args.auto or os.environ.get("RUNEIT_TRACE_AUTO") == "1"

    if not Path(args.elf).is_file():
        die(f"ELF not found: {args.elf}\n       Build the project in STM32CubeIDE first.")

    gdb_path = locate_gdb(args.gdb_path)
    print(f"Using gdb: {gdb_path}")
    print(f"Using ELF: {args.elf}")
    print(f"Mode: {args.mode}")

    session = GdbSession(gdb_path, args.elf, prompt=MODE_PROMPT[args.mode])
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")

        handlers, auto_pace = MODE_SETUP[args.mode](session)

        print("\nAll breakpoints verified against the loaded ELF.")
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
            handler(session, auto_mode)
            if auto_pace:
                pace(auto_mode)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
