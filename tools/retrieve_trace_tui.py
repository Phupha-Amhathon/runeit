#!/usr/bin/env python3
"""Live, paced trace of RETRIEVE_MODE on RUNEIT, over gdb - TUI variant.

What it shows:
  - Choosing "Retrieve password" decrypts the whole table once, in one pass,
    into RAM (s_table). That happens before you type anything. This tool
    records it and does not pause for it.
  - Each id you type pauses at the first line read (mode_retrieve.c:72):
    the raw 48 bytes from flash next to the same entry decrypted in RAM,
    the flash header, and the 10 flash entries around the id.
  - After the serial terminal prints the entry, a second pause (:78) shows
    the name and password exactly as the firmware sent them.

Breakpoints (4, under the 6-comparator ceiling measured on this board):
  - Mode_Retrieve_Enter        menu choice enters RETRIEVE_MODE (no pause).
  - mode_retrieve.c:28         table decrypted into RAM (no pause).
  - mode_retrieve.c:72         first input line read (pause).
  - mode_retrieve.c:78         entry printed (pause).

Precondition: log in over the serial terminal, then choose "Retrieve
password". Start this tool before you choose the menu item.

Start the ST-LINK GDB server first:

    python3 tools/start_gdbserver.py

Usage:
    python3 tools/retrieve_trace_tui.py
    python3 tools/retrieve_trace_tui.py --auto
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

PWD_ENTRY_SIZE = 48  # char name[16] + char password[32], Inc/app/password_table.h
PWD_TABLE_MAX_ENTRIES = 31  # ids 0..30, Inc/app/password_table.h
WINDOW_SIZE = 10
ENC_KEY_LEN = 32
FAST_DELAY_S = 0.2
DIM = "grey50"

console = Console()


def fresh_state():
    return {
        "step": None,             # "menu" / "input" / "shown"
        "input_pauses": 0,
        "shown_pauses": 0,
        "typed": None, "valid": None, "entry_id": None,
        "g_state": None, "s_authorized": None, "session_key": None,
        "active_sector": None, "active_addr": None, "active_version": None,
        "flash_a": None, "flash_b": None,
        "raw": None, "dec": None, "dec_addr": None, "raw_addr": None,
        "name": None, "password": None,
        "window_start": 0, "window": None,
    }


def parse_header(raw_bytes):
    magic, version, _kdf_iter, _salt, _auth, _iv, tag, _crc32 = struct.unpack(
        "<3I16s32s16s32sI", bytes(raw_bytes))
    return {"valid": magic == PARTITION_MAGIC, "version": version, "tag": bytes(tag)}


def decode_c_string(raw):
    return bytes(raw).split(b"\x00", 1)[0].decode("ascii", errors="replace")


def parse_id(line_str):
    """Python copy of mode_retrieve.c's ParseId(). Keep in sync with the C."""
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


def window_start_for(entry_id):
    """Same window as the mockup: the 10 entries around the id, clamped to 0..30."""
    if entry_id is None or entry_id >= PWD_TABLE_MAX_ENTRIES:
        return 0
    return min(max(entry_id - 5, 0), PWD_TABLE_MAX_ENTRIES - WINDOW_SIZE)


def read_session(session, state):
    state["g_state"] = session.read_value("'app.c'::g_state")
    state["s_authorized"] = session.read_value("'session.c'::s_authorized")
    state["session_key"] = bytes(session.read_bytes("'session.c'::s_key", ENC_KEY_LEN))


def read_flash(session, state):
    state["active_sector"] = session.read_value("'partition_store.c'::s_active.sector")
    state["active_addr"] = session.read_int("'partition_store.c'::s_active.addr")
    state["active_version"] = session.read_value("'partition_store.c'::s_active.header.version")
    state["flash_a"] = read_partition_header(session, PARTITION_A_ADDR)
    state["flash_b"] = read_partition_header(session, PARTITION_B_ADDR)


def read_window(session, state, entry_id):
    """Ciphertext of the 10 entries around entry_id, read fresh from the active partition."""
    start = window_start_for(entry_id)
    addr = state["active_addr"] + LAYOUT["header_len"] + start * PWD_ENTRY_SIZE
    raw = bytes(session.read_bytes(hex(addr), WINDOW_SIZE * PWD_ENTRY_SIZE))
    state["window_start"] = start
    state["window"] = [raw[i * PWD_ENTRY_SIZE:(i + 1) * PWD_ENTRY_SIZE] for i in range(WINDOW_SIZE)]


def read_entry(session, state, entry_id):
    addr = state["active_addr"] + LAYOUT["header_len"] + entry_id * PWD_ENTRY_SIZE
    state["raw_addr"] = addr
    state["raw"] = bytes(session.read_bytes(hex(addr), PWD_ENTRY_SIZE))
    state["dec_addr"] = f"s_table.entries[{entry_id}]"
    state["dec"] = bytes(session.read_bytes(
        f"&'mode_retrieve.c'::s_table.entries[{entry_id}]", PWD_ENTRY_SIZE))
    state["name"] = decode_c_string(state["dec"][0:16])
    state["password"] = decode_c_string(state["dec"][16:48])


def panel_input(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["typed"] is None:
        t.add_row("you typed", Text("—", style=DIM))
        t.add_row("parsed id", Text("—", style=DIM))
    else:
        t.add_row("you typed", repr(state["typed"]))
        if state["valid"]:
            t.add_row("parsed id", str(state["entry_id"]))
        else:
            t.add_row("parsed id", Text("invalid - firmware prints 'Invalid id'", style="bold red"))
    t.add_row("", Text(""))
    if state["step"] == "shown" and state["valid"]:
        t.add_row("shown name", f'"{state["name"]}"')
        t.add_row("shown password", f'"{state["password"]}"')
    else:
        t.add_row("shown name", Text("—", style=DIM))
        t.add_row("shown password", Text("—", style=DIM))
    border = "green" if state["step"] == "shown" else ("cyan" if state["step"] == "input" else "grey37")
    return Panel(t, title="[b]Input[/b]", title_align="left",
                 subtitle="mode_retrieve.c:72 / :78", subtitle_align="left",
                 border_style=border)


def panel_ram(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["raw"] is None:
        t.add_row("raw (flash)", Text("—", style=DIM))
        t.add_row("decrypted (RAM)", Text("—", style=DIM))
    else:
        t.add_row("raw (flash)", Text(f"@ 0x{state['raw_addr']:08x}", style=DIM))
        t.add_row("", Text(hexstr(state["raw"]), style="cyan", overflow="fold"))
        t.add_row("decrypted (RAM)", Text(f"@ {state['dec_addr']}", style=DIM))
        t.add_row("", Text(hexstr(state["dec"]), style="green", overflow="fold"))
        t.add_row("name", f'"{state["name"]}"')
        t.add_row("password", f'"{state["password"]}"')
    return Panel(t, title="[b]RAM read - selected entry[/b]", title_align="left",
                 subtitle="raw from flash, then the same entry decrypted in RAM",
                 subtitle_align="left", border_style="grey37")


def panel_session(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    t.add_row("g_state", str(state["g_state"] or Text("—", style=DIM)))
    t.add_row("s_authorized", str(state["s_authorized"] or Text("—", style=DIM)))
    if state["session_key"] is None:
        t.add_row("enc key (s_key)", Text("—", style=DIM))
    elif state["s_authorized"]:
        t.add_row("enc key (s_key)", Text(hexstr(state["session_key"]), style="bold cyan", overflow="fold"))
    else:
        t.add_row("enc key (s_key)", Text("zeroed - no session yet", style=DIM))
    return Panel(t, title="[b]Session & FSM[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style="grey37")


def panel_flash(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    a, b = state["flash_a"], state["flash_b"]
    if a is None:
        t.add_row("sector 2 · A", Text("—", style=DIM))
        t.add_row("sector 3 · B", Text("—", style=DIM))
    else:
        active_a = state["active_sector"] == 2
        t.add_row("sector 2 · A",
                  f"@ 0x{PARTITION_A_ADDR:08x} · {a['status']} · "
                  f"version {a['version']}" + ("  [active]" if active_a else ""))
        t.add_row("sector 3 · B",
                  f"@ 0x{PARTITION_B_ADDR:08x} · {b['status']} · "
                  f"version {b['version']}" + ("  [active]" if not active_a else ""))
    if a is not None:
        t.add_row("tag A", Text(hexstr(a["tag"][:8]) + " ...", style=DIM))
        t.add_row("tag B", Text(hexstr(b["tag"][:8]) + " ...", style=DIM))
    return Panel(t, title="[b]Flash header[/b]", title_align="left",
                 subtitle="nothing is written here; the table decrypts only after its tag verifies",
                 subtitle_align="left",
                 border_style="grey37")


def panel_table(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("id", style=DIM, width=3)
    t.add_column("address", style=DIM, no_wrap=True)
    t.add_column("raw 48 bytes on flash (ciphertext)", overflow="fold")
    if state["window"] is None:
        t.add_row("—", "—", Text("—", style=DIM))
        title_loc = "type an id to show the entries around it"
    else:
        base = state["active_addr"] + LAYOUT["header_len"]
        for i, chunk in enumerate(state["window"]):
            entry_id = state["window_start"] + i
            style = "bold green" if state["valid"] and entry_id == state["entry_id"] else None
            t.add_row(str(entry_id), f"0x{base + entry_id * PWD_ENTRY_SIZE:08x}",
                      hexstr(chunk), style=style)
        title_loc = (f"entries {state['window_start']}–"
                     f"{state['window_start'] + WINDOW_SIZE - 1} · "
                     f"active partition + 112, each entry 48 bytes, all ciphertext")
    return Panel(t, title="[b]Password table on flash[/b]", title_align="left",
                 subtitle=title_loc, subtitle_align="left", border_style="grey37")


def render(state):
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    col1 = Group(panel_input(state))
    col2 = Group(panel_ram(state))
    col3 = Group(panel_session(state), panel_flash(state))
    grid.add_row(col1, col2, col3)
    console.clear()
    console.print(Text("retrieve_trace (tui)", style="bold"),
                  Text(" — live RETRIEVE_MODE pipeline", style=DIM))
    console.print(grid)
    console.print(panel_table(state))


def handle_enter(session, state, auto_mode):
    state.update(fresh_state())
    state["step"] = "menu"
    read_session(session, state)
    read_flash(session, state)
    render(state)
    time.sleep(FAST_DELAY_S)


def handle_decrypted(session, state, auto_mode):
    read_flash(session, state)
    render(state)
    time.sleep(FAST_DELAY_S)


def handle_input(session, state, auto_mode):
    line_bytes = session.read_bytes("line", 32)
    typed = decode_c_string(line_bytes)
    entry_id = parse_id(typed)
    state["typed"] = typed
    state["entry_id"] = entry_id
    state["valid"] = entry_id < PWD_TABLE_MAX_ENTRIES
    state["step"] = "input"

    read_session(session, state)
    read_flash(session, state)
    read_window(session, state, entry_id if state["valid"] else None)
    if state["valid"]:
        read_entry(session, state, entry_id)
    else:
        state["raw"] = None
    render(state)
    state["input_pauses"] += 1
    if state["input_pauses"] == 1:
        pace(auto_mode)
    else:
        time.sleep(FAST_DELAY_S)


def handle_shown(session, state, auto_mode):
    state["step"] = "shown"
    if state["valid"]:
        read_entry(session, state, state["entry_id"])
    render(state)
    state["shown_pauses"] += 1
    if state["shown_pauses"] == 1:
        pace(auto_mode)
    else:
        time.sleep(FAST_DELAY_S)


def main():
    ap = build_arg_parser(__doc__.splitlines()[0])
    args = ap.parse_args()
    auto_mode = args.auto or os.environ.get("RUNEIT_TRACE_AUTO") == "1"

    if not Path(args.elf).is_file():
        die(f"ELF not found: {args.elf}\n       Build the project in STM32CubeIDE first.")

    gdb_path = locate_gdb(args.gdb_path)
    print(f"Using gdb: {gdb_path}")
    print(f"Using ELF: {args.elf}")

    session = GdbSession(gdb_path, args.elf, prompt="(retrieve-trace) ")
    state = fresh_state()
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")

        bp_enter = session.set_checked_breakpoint_by_func("Mode_Retrieve_Enter")
        bp_decrypted = session.set_checked_breakpoint_by_line("mode_retrieve.c", 28)
        bp_input = session.set_checked_breakpoint_by_line("mode_retrieve.c", 72)
        bp_shown = session.set_checked_breakpoint_by_line("mode_retrieve.c", 78)
        handlers = {bp_enter: handle_enter, bp_decrypted: handle_decrypted,
                    bp_input: handle_input, bp_shown: handle_shown}

        print("\nAll breakpoints verified against the loaded ELF.")
        print("Choose 'Retrieve password' on the serial terminal, then type an id.")
        print("The first id you type pauses. Later ids update the panels in place.\n")

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
            handler(session, state, auto_mode)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping.[/dim]")
    finally:
        session.close()


if __name__ == "__main__":
    main()
