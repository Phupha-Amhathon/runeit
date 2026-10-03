#!/usr/bin/env python3
"""Live, paced trace of RETRIEVE_MODE on RUNEIT, over gdb - TUI variant.

What it shows:
  - Enter: the table is decrypted once, in one pass, on entry to the mode.
    The firmware does not decrypt per entry; it decrypts the whole table
    into RAM (s_table) and serves each id from there.
  - Each id you type: the raw 48 bytes read from flash (ciphertext) next to
    the same 48 bytes in RAM after decryption, then the decoded name and
    password.

Breakpoints (3, under the 6-comparator ceiling measured on this board):
  - Mode_Retrieve_Enter        the menu choice enters RETRIEVE_MODE.
  - mode_retrieve.c:28         the table has just been decrypted (return true).
  - mode_retrieve.c:78         an id was typed and ShowEntry has run.

Precondition: log in over the serial terminal first, then choose
"Retrieve password" from the menu. The breakpoints are set before the
menu is chosen, so start this tool, then pick the menu item.

Start the ST-LINK GDB server first:

    python3 tools/start_gdbserver.py

Usage:
    python3 tools/retrieve_trace_tui.py
    python3 tools/retrieve_trace_tui.py --auto
Requires: pip install pexpect rich
"""
import os
import re
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gdb_trace_common import (  # noqa: E402
    GdbSession, PARTITION_A_ADDR, PARTITION_B_ADDR, PARTITION_HEADER_LEN,
    PARTITION_MAGIC, build_arg_parser, die, hexstr, locate_gdb, pace,
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
PWD_TABLE_MAX_ENTRIES = 31  # ids 0..30
TABLE_BYTES = PWD_TABLE_MAX_ENTRIES * PWD_ENTRY_SIZE
ENC_KEY_LEN = 32  # enc key in session.c's s_key
FAST_DELAY_S = 0.2
DIM = "grey50"

console = Console()


def fresh_state():
    return {
        "step": None,             # "enter" / "decrypted" / "shown"
        "shown_once": False,
        "typed": None, "valid": None, "entry_id": None,
        "g_state": None, "s_authorized": None, "session_key": None,
        "active_sector": None, "active_addr": None,
        "flash_a": None, "flash_b": None,
        "table_names": None,      # list of (id, name) for used entries, from RAM
        "table_count": None,
        "raw": None, "dec": None, "name": None, "password": None,
    }


def parse_header(raw_bytes):
    magic, version, _kdf_iter, _salt, _auth, _crc32 = struct.unpack(
        "<3I16s32sI", bytes(raw_bytes))
    return {"valid": magic == PARTITION_MAGIC, "version": version}


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


def read_persistent(session, state):
    """Re-read each pause. Nothing carries over from an earlier pause."""
    state["g_state"] = session.read_value("'app.c'::g_state")
    state["s_authorized"] = session.read_value("'session.c'::s_authorized")
    state["session_key"] = bytes(session.read_bytes("'session.c'::s_key", ENC_KEY_LEN))

    state["active_sector"] = session.read_value("'partition_store.c'::s_active.sector")
    state["active_addr"] = session.read_int("'partition_store.c'::s_active.addr")
    state["flash_a"] = parse_header(session.read_bytes(hex(PARTITION_A_ADDR), PARTITION_HEADER_LEN))
    state["flash_b"] = parse_header(session.read_bytes(hex(PARTITION_B_ADDR), PARTITION_HEADER_LEN))


def read_table_names(session, state):
    """Decrypted table as it sits in RAM right now, names of used entries only."""
    raw = session.read_bytes("&'mode_retrieve.c'::s_table", TABLE_BYTES)
    names = []
    for i in range(PWD_TABLE_MAX_ENTRIES):
        chunk = bytes(raw[i * PWD_ENTRY_SIZE:(i + 1) * PWD_ENTRY_SIZE])
        name = decode_c_string(chunk[0:16])
        if name:
            names.append((i, name))
    state["table_names"] = names
    state["table_count"] = len(names)


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
    border = "cyan" if state["step"] == "shown" else "grey37"
    return Panel(t, title="[b]Your selection[/b]", title_align="left",
                 subtitle="the id you type on serial", subtitle_align="left",
                 border_style=border)


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
    border = "cyan" if state["step"] == "enter" else "grey37"
    return Panel(t, title="[b]Session & FSM[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style=border)


def panel_flash(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    a, b = state["flash_a"], state["flash_b"]
    if a is None:
        t.add_row("active", Text("—", style=DIM))
    else:
        t.add_row("active", f"sector {state['active_sector']} @ 0x{state['active_addr']:08x}")
        t.add_row("A (0x08008000)", f"{'valid' if a['valid'] else 'INVALID'} · version {a['version']}")
        t.add_row("B (0x0800c000)", f"{'valid' if b['valid'] else 'INVALID'} · version {b['version']}")
    return Panel(t, title="[b]Flash header[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style="grey37")


def panel_decrypt(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["table_count"] is None:
        t.add_row("table in RAM", Text("— (not decrypted yet)", style=DIM))
    else:
        t.add_row("table in RAM", f"decrypted in one pass - {state['table_count']} used entries")
        t.add_row("keystream", f"XOR, nonce = partition version {state['active_version']}")
        t.add_row("per-entry?", Text("no - the whole table is decrypted at once", style="italic"))
    border = "cyan" if state["step"] == "decrypted" else "grey37"
    return Panel(t, title="[b]Decrypt table[/b]", title_align="left",
                 subtitle="mode_retrieve.c:28", subtitle_align="left", border_style=border)


def panel_entry(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["raw"] is None:
        t.add_row("raw (flash)", Text("—", style=DIM))
        t.add_row("decrypted (RAM)", Text("—", style=DIM))
        t.add_row("decoded", Text("—", style=DIM))
    else:
        t.add_row("raw (flash)", Text(f"@ 0x{state['raw_addr']:08x}", style=DIM))
        t.add_row("", hexstr(state["raw"]))
        t.add_row("decrypted (RAM)", Text(f"@ {state['dec_label']}", style=DIM))
        t.add_row("", hexstr(state["dec"]))
        t.add_row("name", f'"{state["name"]}"')
        t.add_row("password", f'"{state["password"]}"')
    border = "green" if state["step"] == "shown" else "grey37"
    return Panel(t, title="[b]RAM read - selected entry[/b]", title_align="left",
                 subtitle="raw from flash, then the same entry decrypted in RAM",
                 subtitle_align="left", border_style=border)


def panel_table(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("id", style=DIM, width=3)
    t.add_column("name in RAM (decrypted)", overflow="fold")
    if state["table_names"] is None:
        t.add_row("—", Text("—", style=DIM))
    else:
        for i, name in state["table_names"]:
            style = "bold green" if i == state["entry_id"] and state["valid"] else None
            t.add_row(str(i), name, style=style)
    return Panel(t, title="[b]Password table (RAM, decrypted)[/b]", title_align="left",
                 subtitle="ids and names only; passwords are shown in the RAM panel",
                 subtitle_align="left", border_style="grey37")


def render(state):
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    col1 = Group(panel_input(state), panel_session(state), panel_flash(state))
    col2 = Group(panel_decrypt(state), panel_entry(state))
    col3 = Group(panel_table(state))
    grid.add_row(col1, col2, col3)
    console.clear()
    console.print(Text("retrieve_trace (tui)", style="bold"),
                  Text(" — live RETRIEVE_MODE pipeline", style=DIM))
    console.print(grid)


def handle_enter(session, state, auto_mode):
    state.update(fresh_state())
    state["step"] = "enter"
    read_persistent(session, state)
    render(state)
    pace(auto_mode)


def handle_decrypted(session, state, auto_mode):
    state["active_version"] = session.read_value("'partition_store.c'::s_active.header.version")
    read_table_names(session, state)
    state["step"] = "decrypted"
    read_persistent(session, state)
    render(state)
    pace(auto_mode)


def handle_shown(session, state, auto_mode):
    line_bytes = session.read_bytes("line", 32)
    typed = decode_c_string(line_bytes)
    entry_id = parse_id(typed)
    state["typed"] = typed
    state["entry_id"] = entry_id
    state["valid"] = entry_id < PWD_TABLE_MAX_ENTRIES
    state["step"] = "shown"

    if state["valid"]:
        raw_addr = state["active_addr"] + PARTITION_HEADER_LEN + entry_id * PWD_ENTRY_SIZE
        state["raw_addr"] = raw_addr
        state["raw"] = bytes(session.read_bytes(hex(raw_addr), PWD_ENTRY_SIZE))
        dec_expr = f"&'mode_retrieve.c'::s_table.entries[{entry_id}]"
        state["dec"] = bytes(session.read_bytes(dec_expr, PWD_ENTRY_SIZE))
        state["dec_label"] = f"s_table.entries[{entry_id}]"
        state["name"] = decode_c_string(state["dec"][0:16])
        state["password"] = decode_c_string(state["dec"][16:48])
    else:
        state["raw"] = None

    read_persistent(session, state)
    render(state)
    if not state["shown_once"]:
        state["shown_once"] = True
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
        bp_shown = session.set_checked_breakpoint_by_line("mode_retrieve.c", 78)
        handlers = {bp_enter: handle_enter, bp_decrypted: handle_decrypted,
                    bp_shown: handle_shown}

        print("\nAll breakpoints verified against the loaded ELF.")
        print("Choose 'Retrieve password' on the serial terminal. The first pass pauses,")
        print("then each id you type pauses until the next one.\n")

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
