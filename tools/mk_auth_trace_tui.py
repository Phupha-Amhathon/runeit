#!/usr/bin/env python3
"""Live, paced trace of the MK_AUTH (login) pipeline on RUNEIT, over gdb -
TUI variant (rich-rendered fixed dashboard), draft 1.

Same breakpoints, same gdb mechanics, same data as tools/mk_auth_trace.py -
this is purely a different presentation layer, kept as a SEPARATE file on
purpose so the plain-text version (already proven working on real
hardware) is never at risk while this one is iterated on.

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
Src/app/session.c, Src/crypto/kdf.c and Src/app/app.c, and renders a fixed
dashboard with rich: the same panels stay in the same place for the whole
run, and only the panel relevant to whatever just happened lights up -
nothing appears, scrolls away, or changes position. Flash and RAM are
re-read fresh at every single pause, never from a cache. By default it
pauses for Enter between steps so a presenter can narrate; --auto paces on
a timer instead.

Scope: MK_AUTH only. The board must already have a valid partition from a
prior FIRST_MEET (serial should show "== LOCKED ==" on reset, not
"FIRST TIME SETUP"). See tools/other_modes_trace.py for the other modes.

This never decrypts the password table - Session_Authenticate only
authenticates, it never calls Session_LoadTable (that only happens later,
inside RETRIEVE_MODE/GENERATE_MODE). The "entry 0 (cipher)" row below
is therefore always ciphertext, even right after a correct login - that is
deliberate, and is itself the point: it shows the data is unreadable at
rest regardless of whether the login just succeeded. For a plaintext
comparison, run tools/other_modes_trace.py --mode retrieve afterward.

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
    python3 tools/mk_auth_trace_tui.py
    python3 tools/mk_auth_trace_tui.py --auto
Requires: pip install pexpect rich
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gdb_trace_common import (  # noqa: E402
    GdbSession, LAYOUT, PARTITION_A_ADDR, PARTITION_B_ADDR,
    build_arg_parser, die, hexstr, locate_gdb, pace, read_partition_header,
)

try:
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("This script needs rich: pip install rich", file=sys.stderr)
    sys.exit(1)

PWD_ENTRY_SIZE = 48  # char name[16] + char password[32], Inc/app/password_table.h
DIM = "grey50"

# name, offset, size - matches partition_header_t in Inc/app/partition_store.h
HEADER_FIELDS = [
    ("magic", 0, 4), ("version", 4, 4), ("kdf_iter", 8, 4),
    ("salt", 12, 16), ("auth", 28, 32), ("iv", 60, 16),
    ("tag", 76, 32), ("crc32", 108, 4),
]

console = Console()


def fresh_state():
    return {
        "highlight": None,     # which panel is "live" right now: typed/computed/compared/transition/None
        "hot_rows": set(),     # which Session&FSM rows are "live" right now
        "mk": None, "mk_hex": None,
        "salt": None, "iterations": None, "k": None, "auth_computed": None, "enc": None,
        "auth_stored": None, "verdict": None, "result": None,
        "g_state": None, "s_key": None, "s_authorized": None,
        "transition_before": None, "transition_after": None,
    }


def read_persistent(session, state):
    """Re-reads everything that's always on screen, fresh, every pause -
    never cached from a previous read. Kept local to this file rather than
    added to _gdb_trace_common.dump_base_state(), which other_modes_trace.py
    already relies on for its plain-text output, and rather than touching
    tools/mk_auth_trace.py, which stays untouched on purpose."""
    state["g_state"] = session.read_value("'app.c'::g_state")
    state["s_key"] = session.read_bytes("'session.c'::s_key", 32)
    state["s_authorized"] = session.read_value("'session.c'::s_authorized")

    active_addr = session.read_int("'partition_store.c'::s_active.addr")
    active_sector = session.read_value("'partition_store.c'::s_active.sector")

    state["flash_a"] = read_partition_header(session, PARTITION_A_ADDR)
    state["flash_b"] = read_partition_header(session, PARTITION_B_ADDR)
    state["active_sector"] = active_sector
    state["active_addr"] = active_addr

    entry_base = active_addr + LAYOUT["header_len"]
    state["entry0"] = session.read_bytes(hex(entry_base), PWD_ENTRY_SIZE)


def parse_header(raw_bytes):
    magic, version, _kdf_iter, _salt, _auth, _iv, tag, _crc32 = struct.unpack(
        "<3I16s32s16s32sI", bytes(raw_bytes))
    return {"valid": magic == PARTITION_MAGIC, "version": version,
            "tag": bytes(tag), "raw": bytes(raw_bytes)}


def diff_hex(a, b):
    """Byte-pair colored diff: green where equal, red-on-white where not."""
    t = Text()
    for i, byte in enumerate(a):
        same = i < len(b) and byte == b[i]
        t.append(f"{byte:02x} ", style="green" if same else "bold white on red")
    return t


def border_for(state, name, color):
    return color if state["highlight"] == name else "grey37"


def panel_typed(state):
    border = border_for(state, "typed", "cyan")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["mk"] is None:
        t.add_row("mk (typed)", Text("—", style=DIM))
        t.add_row("mk (hex)", Text("—", style=DIM))
    else:
        t.add_row("mk (typed)", f'"{state["mk"]}"  ({len(state["mk"])} bytes)')
        t.add_row("mk (hex)", state["mk_hex"])
    return Panel(t, title="[b]Login attempt[/b]", title_align="left",
                 subtitle="Session_Authenticate entry", subtitle_align="left",
                 border_style=border)


def panel_computed(state):
    border = border_for(state, "computed", "yellow")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["salt"] is None:
        for label in ("salt", "iterations", "k = PBKDF2(mk, salt, N)",
                      "auth = HMAC(k, 'auth-v1')", "enc = HMAC(k, 'enc-v1')"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("salt", hexstr(state["salt"]))
        t.add_row("iterations", str(state["iterations"]))
        t.add_row("k = PBKDF2(mk, salt, N)", hexstr(state["k"]))
        t.add_row("auth = HMAC(k, 'auth-v1')", hexstr(state["auth_computed"]))
        t.add_row("enc = HMAC(k, 'enc-v1')", hexstr(state["enc"]))
    return Panel(t, title="[b]Key derivation[/b]", title_align="left",
                 subtitle="Kdf_DeriveKeys, kdf.c:71", subtitle_align="left",
                 border_style=border)


def panel_compared(state):
    match = state["verdict"] == "MATCH"
    color = "green" if match else "red"
    border = border_for(state, "compared", color) if state["verdict"] else "grey37"
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["auth_computed"] is None or state["auth_stored"] is None:
        t.add_row("auth (computed)", Text("—", style=DIM))
        t.add_row("auth (in flash)", Text("—", style=DIM))
        t.add_row("verdict", Text("—", style=DIM))
        t.add_row("result", Text("—", style=DIM))
    else:
        t.add_row("auth (computed)", diff_hex(state["auth_computed"], state["auth_stored"]))
        t.add_row("auth (in flash)", diff_hex(state["auth_stored"], state["auth_computed"]))
        badge = Text(f" {state['verdict']} ", style=f"bold black on {color}" if match
                     else f"bold white on {color}")
        t.add_row("verdict", badge)
        t.add_row("result", state["result"])
    return Panel(t, title="[b]Verification[/b]", title_align="left",
                 subtitle="session.c:136", subtitle_align="left", border_style=border)


def panel_session(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()

    def row(key, label, value):
        style = "reverse" if key in state["hot_rows"] else ""
        t.add_row(Text(label, style=f"{DIM} {style}".strip()),
                   value if isinstance(value, Text) else Text(str(value), style=style))

    row("g_state", "g_state", state["g_state"] or "—")
    row("s_key", "s_key", hexstr(state["s_key"]) if state["s_key"] else "—")
    row("s_authorized", "s_authorized", state["s_authorized"] or "—")
    if state["transition_after"]:
        flow = Text()
        flow.append(state["transition_before"], style=DIM)
        flow.append("  ▸▸▸  ", style="magenta")
        flow.append(state["transition_after"], style="bold magenta")
        row("transition", "transition", flow)
    else:
        row("transition", "transition", Text("—", style=DIM))
    border = border_for(state, "transition", "magenta")
    return Panel(t, title="[b]Session & FSM[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style=border)


def panel_flash(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    a, b = state.get("flash_a"), state.get("flash_b")
    if a is None:
        t.add_row("active", Text("—", style=DIM))
    else:
        t.add_row("active", f"sector {state['active_sector']} @ 0x{state['active_addr']:08x}")
        t.add_row("A (0x08008000)",
                   f"{a['status']} · version {a['version']}")
        t.add_row("B (0x0800c000)",
                   f"{'valid' if b['valid'] else 'INVALID'} · version {b['version']}")
        t.add_row("tag A (first 8)", hexstr(a["tag"][:8]) + " ...")
        t.add_row("tag B (first 8)", hexstr(b["tag"][:8]) + " ...")
        t.add_row("", "")
        t.add_row("entry 0 (cipher)", hexstr(state["entry0"]))
    group = Group(t, Text("ciphertext — unreadable without the session key, even right now. "
                           "the tag is checked before any decrypt, in RETRIEVE/GENERATE, not here.",
                           style=f"italic {DIM}"))
    return Panel(group, title="[b]Flash partitions[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style="grey37")


def panel_header_table(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("field", style=DIM)
    t.add_column("start address", style=DIM, no_wrap=True)
    t.add_column("size", style=DIM, no_wrap=True)
    t.add_column("value", overflow="fold")
    a = state.get("flash_a")
    if a is None:
        t.add_row("—", "—", "—", Text("—", style=DIM))
        subtitle = "waiting for the first attempt"
    else:
        active = state["flash_b"] if state["active_sector"] == 3 else state["flash_a"]
        raw = active["raw"]
        base = state["active_addr"]
        for name, offset, size in HEADER_FIELDS:
            value = raw[offset:offset + size]
            t.add_row(name, f"0x{base + offset:08x}", f"{size}B", hexstr(value))
        subtitle = f"active partition, sector {state['active_sector']}, bytes 0-111"
    group = Group(t, Text("nothing is written during login, so this never changes across steps",
                          style=f"italic {DIM}"))
    return Panel(group, title="[b]Header on flash[/b]", title_align="left",
                 subtitle=subtitle, subtitle_align="left", border_style="grey37")


def render(state):
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1.2)
    grid.add_column(ratio=1)
    col1 = Group(panel_typed(state), panel_compared(state))
    col2 = Group(panel_computed(state))
    col3 = Group(panel_session(state), panel_flash(state))
    grid.add_row(col1, col2, col3)
    console.clear()
    console.print(Text("mk_auth_trace (tui)", style="bold"),
                  Text(" — live login pipeline, draft 1", style=DIM))
    console.print(grid)
    console.print(panel_header_table(state))


def handle_bp1(session, state):
    length = int(session.read_value("len"))
    mk_bytes = session.read_bytes("mk", length)
    # A new attempt - clear everything downstream of this step so a retry
    # never shows a previous attempt's stale values next to the new key.
    state.update(fresh_state())
    state["highlight"] = "typed"
    state["mk"] = bytes(mk_bytes).decode("ascii", errors="replace")
    state["mk_hex"] = hexstr(mk_bytes)
    read_persistent(session, state)
    render(state)


def handle_bp2(session, state):
    state["highlight"] = "computed"
    state["salt"] = session.read_bytes("salt", 16)
    state["iterations"] = session.read_value("iterations")
    state["k"] = session.read_bytes("k", 32)
    state["auth_computed"] = session.read_bytes("auth", 32)
    state["enc"] = session.read_bytes("enc", 32)
    read_persistent(session, state)
    render(state)


def handle_bp3(session, state):
    auth = session.read_bytes("auth", 32)
    header_auth = session.read_bytes("header.auth", 32)
    result = session.read_value("result")
    match = auth == header_auth
    state["highlight"] = "compared"
    state["auth_computed"] = auth
    state["auth_stored"] = header_auth
    state["verdict"] = "MATCH" if match else "MISMATCH"
    state["result"] = result
    read_persistent(session, state)
    if match:
        state["hot_rows"] = {"s_key", "s_authorized"}
    render(state)


def handle_bp4(session, state):
    before = session.read_value("'app.c'::g_state")
    session.cmd("next")
    after = session.read_value("'app.c'::g_state")
    state["highlight"] = "transition"
    state["hot_rows"] = {"g_state"}
    state["transition_before"] = before
    state["transition_after"] = after
    read_persistent(session, state)
    state["g_state"] = after
    render(state)


def main():
    ap = build_arg_parser(__doc__.splitlines()[0])
    args = ap.parse_args()
    auto_mode = args.auto or os.environ.get("RUNEIT_TRACE_AUTO") == "1"

    if not Path(args.elf).is_file():
        die(f"ELF not found: {args.elf}\n       Build the project in STM32CubeIDE first.")

    gdb_path = locate_gdb(args.gdb_path)
    print(f"Using gdb: {gdb_path}")
    print(f"Using ELF: {args.elf}")

    session = GdbSession(gdb_path, args.elf, prompt="(mk-auth-trace-tui) ")
    state = fresh_state()
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")

        bp1 = session.set_checked_breakpoint_by_func("Session_Authenticate")
        bp2 = session.set_checked_breakpoint_by_line("kdf.c", 71)
        session.cmd(f"condition {bp2} 'app.c'::g_state == APP_STATE_MK_AUTH")
        bp3 = session.set_checked_breakpoint_by_line("session.c", 136)
        bp4 = session.set_checked_breakpoint_by_line("app.c", 149)
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
            handler(session, state)
            pace(auto_mode)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping.[/dim]")
    finally:
        session.close()


if __name__ == "__main__":
    main()
