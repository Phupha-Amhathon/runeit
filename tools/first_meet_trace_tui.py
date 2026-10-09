#!/usr/bin/env python3
"""Live, paced trace of FIRST_MEET_MODE on RUNEIT, over gdb - TUI variant.

A blank device has no partition and no old key, so this run is simpler than
the other modes: one key, derived once, committed once. What it shows, one
pause per step:
  1. The first and confirm entries are compared (mode_first_meet.c:63).
  2. The fresh salt, iterations, the intermediate k, and auth/enc are
     derived (kdf.c:78). k is discarded right after this step.
  3. The first-ever partition is committed to sector 2 / A, version 1
     (session.c:202). The header and the table are read fresh from flash
     both before and after this step, so the panels show whatever was
     physically on the sector (erased, or left over from an earlier test)
     until the commit actually lands.
  4. The mode returns to the menu (app.c:153, which steps once more to
     show g_state after the transition).

Breakpoints (4, under the 6-comparator ceiling measured on this board):
  - mode_first_meet.c:63   confirm entry compared.
  - kdf.c:78               auth and enc both derived.
  - session.c:202          first-ever partition committed, session opened.
  - app.c:153              mode returned to MODE_SELECTION.

Precondition: a BLANK device. No valid partition in either sector. If the
device already has one, erase sectors 2 and 3 first (see TESTING.md) or
this mode will never be entered. Start this tool, then power on or reset
the board so it lands on "== FIRST TIME SETUP ==".

Start the ST-LINK GDB server first:

    python3 tools/start_gdbserver.py

Usage:
    python3 tools/first_meet_trace_tui.py
    python3 tools/first_meet_trace_tui.py --auto
Requires: pip install pexpect rich
"""
import os
import re
import sys
import time
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

PWD_ENTRY_SIZE = 48
WINDOW_SIZE = 4
ENC_KEY_LEN = 32
FAST_DELAY_S = 0.2
DIM = "grey50"

console = Console()


def fresh_state():
    return {
        "step": None,  # "confirmed" / "derived" / "committed" / "transitioned"
        "pauses": {"confirmed": 0, "derived": 0, "committed": 0, "transitioned": 0},
        "first_entry": None, "confirm_entry": None, "verdict": None,
        "salt": None, "iterations": None, "k": None, "auth": None, "enc": None,
        "g_state": None, "g_state_before": None, "g_state_after": None,
        "s_authorized": None, "session_key": None,
        "active_sector": None, "active_addr": None,
        "flash_a": None, "flash_b": None,
        "header_raw": None, "entries": None,
    }


def decode_c_string(raw):
    return bytes(raw).split(b"\x00", 1)[0].decode("ascii", errors="replace")


def read_session(session, state):
    state["g_state"] = session.read_value("'app.c'::g_state")
    state["s_authorized"] = session.read_value("'session.c'::s_authorized")
    state["session_key"] = bytes(session.read_bytes("'session.c'::s_key", ENC_KEY_LEN))


def read_flash(session, state):
    """Real memory, read fresh every pause - valid or not, written or not."""
    state["flash_a"] = read_partition_header(session, PARTITION_A_ADDR)
    state["flash_b"] = read_partition_header(session, PARTITION_B_ADDR)
    state["header_raw"] = state["flash_a"]["raw"]

    raw_table = bytes(session.read_bytes(
        hex(PARTITION_A_ADDR + LAYOUT["header_len"]), WINDOW_SIZE * PWD_ENTRY_SIZE))
    state["entries"] = [raw_table[i * PWD_ENTRY_SIZE:(i + 1) * PWD_ENTRY_SIZE]
                        for i in range(WINDOW_SIZE)]

    if state["flash_a"]["valid"]:
        state["active_sector"] = int(session.read_value("'partition_store.c'::s_active.sector"))
        state["active_addr"] = session.read_int("'partition_store.c'::s_active.addr")


def panel_input(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["first_entry"] is None:
        t.add_row("first entry", Text("—", style=DIM))
        t.add_row("confirm entry", Text("—", style=DIM))
        t.add_row("verdict", Text("—", style=DIM))
    else:
        t.add_row("first entry", f'"{state["first_entry"]}"')
        t.add_row("confirm entry", f'"{state["confirm_entry"]}"')
        t.add_row("verdict", Text("MATCH", style="bold green") if state["verdict"]
                  else Text("MISMATCH", style="bold red"))
    border = "cyan" if state["step"] == "confirmed" else "grey37"
    return Panel(t, title="[b]Input[/b]", title_align="left",
                 subtitle="mode_first_meet.c:63", subtitle_align="left", border_style=border)


def panel_flash_compact(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    a, b = state["flash_a"], state["flash_b"]
    if a is None:
        t.add_row("sector 2 · A", Text("—", style=DIM))
        t.add_row("sector 3 · B", Text("—", style=DIM))
    else:
        a_txt = (f"valid · version {a['version']}" if a["valid"] else "not written yet")
        b_txt = (f"valid · version {b['version']}" if b["valid"] else "not written yet")
        t.add_row("sector 2 · A", Text(a_txt, style="green" if a["valid"] else None))
        t.add_row("sector 3 · B", Text(b_txt, style=DIM if not b["valid"] else None))
    border = "green" if state["step"] in ("committed", "transitioned") else "grey37"
    return Panel(t, title="[b]Flash header[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style=border)


def panel_derive(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["salt"] is None:
        for label in ("salt (fresh)", "iterations", "auth (header)", "enc (RAM only)"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("salt (fresh)", hexstr(state["salt"]))
        t.add_row("iterations", str(state["iterations"]))
        t.add_row("k (discarded)", Text(hexstr(state["k"]), style="yellow", overflow="fold"))
        t.add_row("auth (header)", Text(hexstr(state["auth"]), style="yellow", overflow="fold"))
        t.add_row("enc (RAM only)", Text(hexstr(state["enc"]), style="yellow", overflow="fold"))
    border = "yellow" if state["step"] in ("derived", "committed", "transitioned") else "grey37"
    return Panel(t, title="[b]Key derivation[/b]", title_align="left",
                 subtitle="kdf.c:78", subtitle_align="left", border_style=border)


def panel_session(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["step"] == "transitioned":
        t.add_row("g_state", f"{state['g_state_before']} -> {state['g_state_after']}")
    else:
        t.add_row("g_state", str(state["g_state"] or Text("—", style=DIM)))
    t.add_row("s_authorized", str(state["s_authorized"] or Text("—", style=DIM)))
    if state["session_key"] is None:
        t.add_row("s_key", Text("—", style=DIM))
    elif state["s_authorized"]:
        t.add_row("s_key", Text(hexstr(state["session_key"]), style="bold cyan", overflow="fold"))
    else:
        t.add_row("s_key", Text("never held a value", style=DIM))
    border = "green" if state["step"] == "transitioned" else "grey37"
    return Panel(t, title="[b]Session & FSM[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style=border)


def panel_header_table(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("field", style=DIM)
    t.add_column("start address", style=DIM, no_wrap=True)
    t.add_column("size", style=DIM, no_wrap=True)
    t.add_column("value", overflow="fold")
    raw = state["header_raw"]
    written = state["flash_a"] is not None and state["flash_a"]["valid"]
    if raw is None:
        t.add_row("—", "—", "—", Text("—", style=DIM))
    else:
        for name, offset, size in LAYOUT["fields"]:
            value = raw[offset:offset + size]
            style = "green" if written else "grey50"
            t.add_row(name, f"0x{PARTITION_A_ADDR + offset:08x}", f"{size}B",
                      Text(hexstr(value), style=style))
    subtitle = ("partition A, bytes 0-111 · just written" if written
                else "partition A, bytes 0-111 · raw flash, before this commit")
    border = "green" if written else "grey37"
    return Panel(t, title="[b]Header on flash[/b]", title_align="left",
                 subtitle=subtitle, subtitle_align="left", border_style=border)


def panel_entries(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("id", style=DIM, width=3)
    t.add_column("address", style=DIM, no_wrap=True)
    t.add_column("raw 48 bytes on flash", overflow="fold")
    written = state["flash_a"] is not None and state["flash_a"]["valid"]
    if state["entries"] is None:
        t.add_row("—", "—", Text("—", style=DIM))
    else:
        base = PARTITION_A_ADDR + LAYOUT["header_len"]
        for i, chunk in enumerate(state["entries"]):
            style = "green" if written else "grey50"
            t.add_row(str(i), f"0x{base + i * PWD_ENTRY_SIZE:08x}", Text(hexstr(chunk), style=style))
    subtitle = ("every entry is 48 bytes of ciphertext over an all-zero plaintext"
                if written else "raw flash, before this commit")
    return Panel(t, title="[b]Password table on flash[/b]", title_align="left",
                 subtitle=subtitle, subtitle_align="left", border_style="grey37")


def render(state):
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1.2)
    grid.add_column(ratio=1)
    col1 = Group(panel_input(state), panel_flash_compact(state))
    grid.add_row(col1, panel_derive(state), panel_session(state))
    console.clear()
    console.print(Text("first_meet_trace (tui)", style="bold"),
                  Text(" — live FIRST_MEET_MODE pipeline", style=DIM))
    console.print(grid)
    console.print(panel_header_table(state))
    console.print(panel_entries(state))


def pause_or_fast(state, key, auto_mode):
    state["pauses"][key] += 1
    if state["pauses"][key] == 1:
        pace(auto_mode)
    else:
        time.sleep(FAST_DELAY_S)


def handle_confirm(session, state, auto_mode):
    length = int(session.read_value("len"))
    line_bytes = session.read_bytes("line", length) if length else []
    first_len = int(session.read_value("s_first_len"))
    first_bytes = session.read_bytes("s_first", first_len) if first_len else []

    state["first_entry"] = decode_c_string(first_bytes)
    state["confirm_entry"] = decode_c_string(line_bytes)
    state["verdict"] = (length == first_len and line_bytes == first_bytes)
    state["step"] = "confirmed"

    read_session(session, state)
    read_flash(session, state)
    render(state)
    pause_or_fast(state, "confirmed", auto_mode)


def handle_derived(session, state, auto_mode):
    state["salt"] = bytes(session.read_bytes("salt", 16))
    state["iterations"] = int(session.read_value("iterations"))
    state["k"] = bytes(session.read_bytes("k", 32))
    state["auth"] = bytes(session.read_bytes("auth", 32))
    state["enc"] = bytes(session.read_bytes("enc", 32))
    state["step"] = "derived"

    read_session(session, state)
    read_flash(session, state)
    render(state)
    pause_or_fast(state, "derived", auto_mode)


def handle_committed(session, state, auto_mode):
    state["step"] = "committed"
    read_session(session, state)
    read_flash(session, state)
    render(state)
    pause_or_fast(state, "committed", auto_mode)


def handle_transition(session, state, auto_mode):
    state["g_state_before"] = session.read_value("'app.c'::g_state")
    session.cmd("next")
    state["g_state_after"] = session.read_value("'app.c'::g_state")
    state["step"] = "transitioned"

    read_session(session, state)
    read_flash(session, state)
    render(state)
    pause_or_fast(state, "transitioned", auto_mode)


def main():
    ap = build_arg_parser(__doc__.splitlines()[0])
    args = ap.parse_args()
    auto_mode = args.auto or os.environ.get("RUNEIT_TRACE_AUTO") == "1"

    if not Path(args.elf).is_file():
        die(f"ELF not found: {args.elf}\n       Build the project in STM32CubeIDE first.")

    gdb_path = locate_gdb(args.gdb_path)
    print(f"Using gdb: {gdb_path}")
    print(f"Using ELF: {args.elf}")

    session = GdbSession(gdb_path, args.elf, prompt="(first-meet-trace) ")
    state = fresh_state()
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")

        bp_confirm = session.set_checked_breakpoint_by_line("mode_first_meet.c", 63)
        bp_derived = session.set_checked_breakpoint_by_line("kdf.c", 78)
        bp_committed = session.set_checked_breakpoint_by_line("session.c", 202)
        bp_transition = session.set_checked_breakpoint_by_line("app.c", 153)
        handlers = {bp_confirm: handle_confirm, bp_derived: handle_derived,
                    bp_committed: handle_committed, bp_transition: handle_transition}

        print("\nAll breakpoints verified against the loaded ELF.")
        print("Needs a BLANK device. Power on or reset the board so it lands on")
        print("'== FIRST TIME SETUP ==', then type a master key twice.\n")

        while True:
            out = session.continue_and_wait()
            m = re.search(r"Breakpoint (\d+),", out)
            if not m:
                if "exited" in out.lower():
                    print("\nTarget program exited.")
                    break
                if "Cannot insert hardware breakpoint" in out or "Command aborted" in out:
                    print("\nThe GDB server could not arm all breakpoints. Reset the board"
                          " (NRST button), then run this tool again.")
                    print(out)
                    break
                print(f"\nUnexpected stop, continuing:\n{out}")
                continue
            handler = handlers.get(m.group(1))
            if handler is None:
                print(f"\nUnknown breakpoint {m.group(1)} hit, continuing.")
                continue
            handler(session, state, auto_mode)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping.[/dim]")
    finally:
        session.close()


if __name__ == "__main__":
    main()
