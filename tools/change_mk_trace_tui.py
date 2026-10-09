#!/usr/bin/env python3
"""Live, paced trace of CHANGE_MK_MODE on RUNEIT, over gdb - TUI variant.

What it shows, one pause per step:
  1. The new master key is typed and confirmed (mode_change_mk.c:87).
  2. The current table is decrypted with the old key (mode_change_mk.c:66).
  3. The new salt, iterations, auth and enc are derived (kdf.c:78).
  4. The table is re-encrypted and committed to the inactive partition,
     with the session key and iv used for the encryption (session.c:202).
  5. The mode returns to the menu (app.c:183).

Breakpoints (5, under the 6-comparator ceiling measured on this board):
  - mode_change_mk.c:87    confirm entry compared.
  - mode_change_mk.c:66    old table decrypted, before SetNewKey.
  - kdf.c:78               auth and enc both derived, K about to be wiped.
  - session.c:202         partition committed and session reopened.
  - app.c:211              mode returned to MODE_SELECTION.

Power-loss test (--power-loss WORD): stops the commit inside
Flash_Drv_Write() right after table word WORD (0-371) of the new partition
is written, and holds the CPU there so you can pull the USB cable. The
header (with its magic and new version) is already on flash, the table is
not complete, so the boot CRC check must reject that partition and keep the
old one. app.c:211 is not armed in this mode (the menu is never reached),
which keeps the breakpoint count at 5. After replugging, check the result
with tools/power_loss_check.gdb, or log in and confirm the OLD key still
works and the NEW one does not.

Precondition: a session must already be open. Log in over the serial
terminal first, then choose "Change master key". Start this tool before
you choose the menu item.

Start the ST-LINK GDB server first:

    python3 tools/start_gdbserver.py

Usage:
    python3 tools/change_mk_trace_tui.py
    python3 tools/change_mk_trace_tui.py --auto
    python3 tools/change_mk_trace_tui.py --power-loss 200
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
    build_arg_parser, die, hexstr, locate_gdb, pace, read_partition_crc_ok,
    read_partition_header,
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
PWD_TABLE_MAX_ENTRIES = 31
TABLE_BYTES = PWD_TABLE_MAX_ENTRIES * PWD_ENTRY_SIZE
TABLE_WORDS = TABLE_BYTES // 4
# Flash_Drv_Write(): the line after the BSY wait, so word i is already on flash.
FLASH_WORD_DONE_LINE = 72
WINDOW_SIZE = 10
ENC_KEY_LEN = 32
MK_MIN_LEN = 8
MK_MAX_LEN = 31
FAST_DELAY_S = 0.2
DIM = "grey50"

console = Console()

PARTITION_ADDR = {2: PARTITION_A_ADDR, 3: PARTITION_B_ADDR}
PARTITION_NAME = {2: "A", 3: "B"}


def fresh_state():
    return {
        "step": "menu",
        "typed_mk": None, "policy_ok": None, "confirm_match": None,
        "old_raw": None, "old_dec": None, "old_addr": None, "old_count": None, "first_used": None,
        "salt": None, "iterations": None, "auth": None, "enc": None,
        "target_sector": None, "target_addr": None,
        "commit_key": None, "commit_iv": None, "commit_entry0": None,
        "session_key": None, "s_authorized": None, "g_state": None,
        "active_sector": None, "active_addr": None,
        "flash_a": None, "flash_b": None, "header_raw": None,
        "window_start": 0, "window": None,
        "pauses": {"typed": 0, "old": 0, "derived": 0, "committed": 0, "menu": 0},
        "power_loss": None,       # filled by handle_power_loss
    }


def decode_c_string(raw):
    return bytes(raw).split(b"\x00", 1)[0].decode("ascii", errors="replace")


def read_session(session, state):
    state["g_state"] = session.read_value("'app.c'::g_state")
    state["s_authorized"] = session.read_value("'session.c'::s_authorized")
    state["session_key"] = bytes(session.read_bytes("'session.c'::s_key", ENC_KEY_LEN))


def read_flash(session, state):
    """Real memory: s_active from partition_store.c, headers read from flash."""
    state["active_sector"] = int(session.read_value("'partition_store.c'::s_active.sector"))
    state["active_addr"] = session.read_int("'partition_store.c'::s_active.addr")
    state["flash_a"] = read_partition_header(session, PARTITION_A_ADDR)
    state["flash_b"] = read_partition_header(session, PARTITION_B_ADDR)
    # Magic only says the header was written; boot also needs the CRC.
    for key, addr in (("flash_a", PARTITION_A_ADDR), ("flash_b", PARTITION_B_ADDR)):
        hdr = state[key]
        hdr["crc_ok"] = read_partition_crc_ok(session, addr) if hdr["valid"] else None


def target_partition(state):
    """The commit writes the inactive partition (partition_store.c:105-110)."""
    active = state["active_sector"]
    return 3 if active == 2 else 2


def read_target_window(session, state):
    """Ciphertext of the 10 entries around entry 0 on the target partition.

    The target is fixed at the first pause of a run. The commit changes
    s_active, so recomputing it afterward would pick the wrong partition.
    """
    sector = state["target_sector"] or target_partition(state)
    addr = PARTITION_ADDR[sector] + LAYOUT["header_len"]
    raw = bytes(session.read_bytes(hex(addr), WINDOW_SIZE * PWD_ENTRY_SIZE))
    state["target_sector"] = sector
    state["target_addr"] = PARTITION_ADDR[sector]
    state["window_start"] = 0
    state["window"] = [raw[i * PWD_ENTRY_SIZE:(i + 1) * PWD_ENTRY_SIZE] for i in range(WINDOW_SIZE)]
    # read_flash() already pulled the full 112-byte header for both sectors;
    # reuse it here rather than reading the same bytes from flash twice.
    state["header_raw"] = (state["flash_b"] if sector == 3 else state["flash_a"])["raw"]


def panel_input(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["typed_mk"] is None:
        t.add_row("new MK", Text("—", style=DIM))
        t.add_row("length", Text("—", style=DIM))
        t.add_row("policy", Text("—", style=DIM))
        t.add_row("confirm", Text("—", style=DIM))
    else:
        t.add_row("new MK", f'"{state["typed_mk"]}"')
        t.add_row("length", str(len(state["typed_mk"])))
        t.add_row("policy", Text("ok · 8-31 printable", style="green") if state["policy_ok"]
                  else Text("invalid", style="bold red"))
        t.add_row("confirm", Text("match", style="green") if state["confirm_match"]
                  else Text("no match", style="bold red"))
    border = "cyan" if state["step"] == "typed" else "grey37"
    return Panel(t, title="[b]Input[/b]", title_align="left",
                 subtitle="mode_change_mk.c:87", subtitle_align="left", border_style=border)


def panel_old(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["old_raw"] is None:
        t.add_row("first used entry", Text("—", style=DIM))
        t.add_row("raw (flash)", Text("—", style=DIM))
        t.add_row("in RAM", Text("—", style=DIM))
        t.add_row("entries", Text("—", style=DIM))
    else:
        t.add_row("first used entry", f"id {state['first_used']}")
        t.add_row("raw (flash)", Text(f"@ 0x{state['old_addr']:08x} (active partition)", style=DIM))
        t.add_row("", Text(hexstr(state["old_raw"]), style="cyan", overflow="fold"))
        t.add_row("in RAM", Text("decrypted with the old key", style=DIM))
        t.add_row("", Text(hexstr(state["old_dec"]), style="magenta", overflow="fold"))
        t.add_row("entries", f"{state['old_count']} used, decrypted in one pass")
    border = "magenta" if state["step"] == "old" else "grey37"
    return Panel(t, title="[b]Old key -> table[/b]", title_align="left",
                 subtitle="mode_change_mk.c:66", subtitle_align="left", border_style=border)


def panel_session(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    t.add_row("g_state", str(state["g_state"] or Text("—", style=DIM)))
    t.add_row("s_authorized", str(state["s_authorized"] or Text("—", style=DIM)))
    if state["session_key"] is None:
        t.add_row("s_key", Text("—", style=DIM))
    else:
        t.add_row("s_key", Text(hexstr(state["session_key"]), style="bold cyan", overflow="fold"))
    border = "green" if state["step"] in ("committed", "menu") else "grey37"
    return Panel(t, title="[b]Session & FSM[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style=border)


def panel_derive(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["salt"] is None:
        for label in ("salt (new)", "iterations", "auth (header)", "enc (RAM only)"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("salt (new)", hexstr(state["salt"]))
        t.add_row("iterations", str(state["iterations"]))
        t.add_row("auth (header)", Text(hexstr(state["auth"]), style="yellow", overflow="fold"))
        if state["enc"] is None:
            t.add_row("enc (RAM only)", Text("derived next, not yet read", style=DIM))
        else:
            t.add_row("enc (RAM only)", Text(hexstr(state["enc"]), style="yellow", overflow="fold"))
    border = "yellow" if state["step"] in ("derived", "committed") else "grey37"
    return Panel(t, title="[b]New key derivation[/b]", title_align="left",
                 subtitle="kdf.c:78", subtitle_align="left", border_style=border)


def panel_commit(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["commit_entry0"] is None:
        t.add_row("target", Text("—", style=DIM))
        t.add_row("entry 0 (new key)", Text("—", style=DIM))
        t.add_row("session key (enc)", Text("—", style=DIM))
    else:
        sector = state["target_sector"]
        t.add_row("target", f"sector {sector} · {PARTITION_NAME[sector]} @ 0x{state['target_addr']:08x}")
        t.add_row(f"entry {state['first_used']} (new key)", Text(hexstr(state["commit_entry0"]), style="green", overflow="fold"))
        t.add_row("session key (enc)", Text(hexstr(state["commit_key"]), style="yellow", overflow="fold"))
    border = "green" if state["step"] == "committed" else "grey37"
    return Panel(t, title="[b]Re-encrypt and commit[/b]", title_align="left",
                 subtitle="session.c:202 · AES-128-CTR, tag = HMAC over header + ciphertext",
                 subtitle_align="left", border_style=border)


def panel_flash(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    a, b = state["flash_a"], state["flash_b"]
    if a is None:
        t.add_row("sector 2 · A", Text("—", style=DIM))
        t.add_row("sector 3 · B", Text("—", style=DIM))
    else:
        active = state["active_sector"]
        for label, addr, hdr, sector in (("sector 2 · A", PARTITION_A_ADDR, a, 2),
                                         ("sector 3 · B", PARTITION_B_ADDR, b, 3)):
            t.add_row(label, flash_status_text(addr, hdr, active == sector))
    return Panel(t, title="[b]Flash header[/b]", title_align="left",
                 subtitle="live, every pause · magic + CRC, as at boot · iv/tag below",
                 subtitle_align="left", border_style="grey37")


def flash_status_text(addr, hdr, is_active):
    """magic + CRC, the same two checks boot uses, so a half-written partition
    does not read as "valid"."""
    line = Text(f"@ 0x{addr:08x} · ")
    if not hdr["valid"]:
        line.append(hdr["status"], style=None if hdr["status"] == "empty" else "bold red")
    elif hdr.get("crc_ok") is False:
        line.append("magic ok, CRC BAD -> boot rejects it", style="bold red")
    elif hdr.get("crc_ok") is None:
        line.append("magic ok, CRC not checked", style="yellow")
    else:
        line.append("valid (magic + CRC)", style="green")
    line.append(f" · version {hdr['version']}")
    if is_active:
        line.append("  [active]")
    return line


def panel_header_table(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("field", style=DIM)
    t.add_column("start address", style=DIM, no_wrap=True)
    t.add_column("size", style=DIM, no_wrap=True)
    t.add_column("value", overflow="fold")
    raw = state["header_raw"]
    committed = state["step"] == "committed"
    if raw is None:
        t.add_row("—", "—", "—", Text("—", style=DIM))
        subtitle = "waiting for the first input"
        border = "grey37"
    else:
        base = state["target_addr"]
        for name, offset, size in LAYOUT["fields"]:
            value = raw[offset:offset + size]
            style = "green" if committed else "grey50"
            t.add_row(name, f"0x{base + offset:08x}", f"{size}B", Text(hexstr(value), style=style))
        sector = state["target_sector"]
        subtitle = (f"partition {PARTITION_NAME[sector]}, bytes 0-111 · just written" if committed
                    else f"partition {PARTITION_NAME[sector]}, bytes 0-111 · its old header")
        border = "green" if committed else "grey37"
    return Panel(t, title="[b]Header on flash[/b]", title_align="left",
                 subtitle=subtitle, subtitle_align="left", border_style=border)


def panel_table(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("id", style=DIM, width=3)
    t.add_column("address", style=DIM, no_wrap=True)
    t.add_column("raw 48 bytes on flash (ciphertext)", overflow="fold")
    if state["window"] is None:
        t.add_row("—", "—", Text("—", style=DIM))
        subtitle = "waiting for the first input"
    else:
        sector = state["target_sector"]
        base = state["target_addr"] + LAYOUT["header_len"]
        for i, chunk in enumerate(state["window"]):
            entry_id = state["window_start"] + i
            style = "bold green" if state["step"] == "committed" and entry_id == state["first_used"] else None
            t.add_row(str(entry_id), f"0x{base + entry_id * PWD_ENTRY_SIZE:08x}",
                      hexstr(chunk), style=style)
        subtitle = f"showing partition {PARTITION_NAME[sector]} (sector {sector} · 0x{state['target_addr']:08x})"
    return Panel(t, title="[b]Target partition table on flash[/b]", title_align="left",
                 subtitle=subtitle, subtitle_align="left", border_style="grey37")


def panel_power_loss(state):
    pl = state["power_loss"]
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    sector = pl["sector"]
    t.add_row("partition being written",
              f"sector {sector} · {PARTITION_NAME[sector]} @ 0x{PARTITION_ADDR[sector]:08x}")
    t.add_row("header on flash", f"{pl['header']['status']} magic · version {pl['header']['version']} "
              "(written before the table)")
    t.add_row("table words written", f"0-{pl['word']}  ({pl['word'] + 1} of {TABLE_WORDS})")
    t.add_row(f"word {pl['word']} @ 0x{pl['addr']:08x}", Text(f"0x{pl['done']:08x}", style="green"))
    if pl["next"] is not None:
        t.add_row(f"word {pl['word'] + 1} @ 0x{pl['addr'] + 4:08x}",
                  Text(f"0x{pl['next']:08x}  not written yet", style=DIM))
    if pl["crc_ok"] is None:
        crc = Text("could not run ValidateCrc", style=DIM)
    elif pl["crc_ok"]:
        crc = Text("ok  (unexpected: the table looks complete)", style="bold yellow")
    else:
        crc = Text("BAD -> boot will reject this partition", style="bold green")
    t.add_row("boot CRC check now", crc)
    t.add_row("", Text(""))
    t.add_row("", Text(">>> UNPLUG THE USB CABLE NOW, wait 2 s, plug it back in.", style="bold red"))
    t.add_row("", Text("Then log in with the OLD key: it must still work. Ctrl+C here instead "
                       "lets the save finish (test aborted).", style=DIM))
    return Panel(t, title="[b]Power-loss point[/b]", title_align="left",
                 subtitle=f"flash_drv.c:{FLASH_WORD_DONE_LINE} · CPU held, nothing runs until you unplug",
                 subtitle_align="left", border_style="bold red")


def render(state):
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    col1 = Group(panel_input(state), panel_derive(state))
    col2 = Group(panel_old(state), panel_commit(state))
    col3 = Group(panel_session(state), panel_flash(state))
    grid.add_row(col1, col2, col3)
    console.clear()
    console.print(Text("change_mk_trace (tui)", style="bold"),
                  Text(" — live CHANGE_MK_MODE pipeline", style=DIM))
    if state["power_loss"] is not None:
        console.print(panel_power_loss(state))
    console.print(grid)
    console.print(panel_header_table(state))
    console.print(panel_table(state))


def pause_or_fast(state, key, auto_mode):
    state["pauses"][key] += 1
    if state["pauses"][key] == 1:
        pace(auto_mode)
    else:
        time.sleep(FAST_DELAY_S)


def handle_confirm(session, state, auto_mode):
    if state["step"] == "menu":
        state.update(fresh_state())
    mk_len = int(session.read_value("'mode_change_mk.c'::s_new_len"))
    new_mk = decode_c_string(session.read_bytes("'mode_change_mk.c'::s_new", mk_len))
    confirm = decode_c_string(session.read_bytes("line", MK_MAX_LEN + 2))
    state["typed_mk"] = new_mk
    state["policy_ok"] = MK_MIN_LEN <= len(new_mk) <= MK_MAX_LEN
    state["confirm_match"] = confirm == new_mk
    state["step"] = "typed"
    read_session(session, state)
    read_flash(session, state)
    read_target_window(session, state)
    render(state)
    pause_or_fast(state, "typed", auto_mode)


def handle_old(session, state, auto_mode):
    state["step"] = "old"
    read_session(session, state)
    read_flash(session, state)
    table = bytes(session.read_bytes("&'mode_change_mk.c'::s_work", TABLE_BYTES))
    used = [i for i in range(PWD_TABLE_MAX_ENTRIES)
            if any(table[i * PWD_ENTRY_SIZE:(i + 1) * PWD_ENTRY_SIZE])]
    first = used[0] if used else 0
    state["old_count"] = len(used)
    state["first_used"] = first
    state["old_addr"] = state["active_addr"] + LAYOUT["header_len"] + first * PWD_ENTRY_SIZE
    state["old_raw"] = bytes(session.read_bytes(hex(state["old_addr"]), PWD_ENTRY_SIZE))
    state["old_dec"] = table[first * PWD_ENTRY_SIZE:(first + 1) * PWD_ENTRY_SIZE]
    read_target_window(session, state)
    render(state)
    pause_or_fast(state, "old", auto_mode)


def handle_derived(session, state, auto_mode):
    state["step"] = "derived"
    state["salt"] = bytes(session.read_bytes("salt", 16))
    state["iterations"] = int(session.read_value("iterations"))
    state["auth"] = bytes(session.read_bytes("auth", 32))
    read_session(session, state)
    read_flash(session, state)
    read_target_window(session, state)
    render(state)
    pause_or_fast(state, "derived", auto_mode)


def handle_committed(session, state, auto_mode):
    state["step"] = "committed"
    state["enc"] = bytes(session.read_bytes("enc", ENC_KEY_LEN))
    read_session(session, state)
    read_flash(session, state)
    read_target_window(session, state)
    target = state["target_sector"]
    header = state["flash_b"] if target == 3 else state["flash_a"]
    state["commit_iv"] = header["iv"]
    state["commit_key"] = state["enc"]
    entry_addr = state["target_addr"] + LAYOUT["header_len"] + state["first_used"] * PWD_ENTRY_SIZE
    state["commit_entry0"] = bytes(session.read_bytes(hex(entry_addr), PWD_ENTRY_SIZE))
    render(state)
    pause_or_fast(state, "committed", auto_mode)


def handle_menu(session, state, auto_mode):
    state["step"] = "menu"
    read_session(session, state)
    read_flash(session, state)
    read_target_window(session, state)
    render(state)
    pause_or_fast(state, "menu", auto_mode)


def handle_power_loss(session, state, auto_mode):
    """Hit inside the table write. Reads what is on flash, runs the firmware's
    own boot CRC check on it, then holds: the main loop must not continue."""
    sector = state["power_loss_sector"]
    base = PARTITION_ADDR[sector]
    word = state["power_loss_word"]
    addr = base + LAYOUT["header_len"] + word * 4
    read_session(session, state)
    read_flash(session, state)
    read_target_window(session, state)
    header = state["flash_b"] if sector == 3 else state["flash_a"]
    done = session.read_int(f"*(unsigned int *){addr:#x}")
    nxt = session.read_int(f"*(unsigned int *){addr + 4:#x}") if word + 1 < TABLE_WORDS else None
    try:
        crc_ok = int(session.read_value(
            f"(int)'partition_store.c'::ValidateCrc({base:#x}, (const partition_header_t *){base:#x})")) != 0
    except RuntimeError:
        crc_ok = None
    state["step"] = "power_loss"
    state["power_loss"] = {"sector": sector, "word": word, "addr": addr, "done": done,
                           "next": nxt, "header": header, "crc_ok": crc_ok}
    render(state)
    state["hold"] = True


def arm_power_loss(session, state, word):
    """One conditional breakpoint on the table write of the next commit. The
    target is the inactive partition, fixed now: only this commit changes it."""
    read_flash(session, state)
    sector = target_partition(state)
    # Own keys, not in fresh_state(): handle_confirm() resets those per run.
    state["power_loss_sector"] = sector
    state["power_loss_word"] = word
    table_addr = PARTITION_ADDR[sector] + LAYOUT["header_len"]
    bp = session.set_checked_breakpoint_by_line("flash_drv.c", FLASH_WORD_DONE_LINE)
    session.cmd(f"condition {bp} address == {table_addr:#x} && i == {word * 4}")
    print(f"Power-loss test armed: stop after table word {word} of partition "
          f"{PARTITION_NAME[sector]} (0x{table_addr + word * 4:08x}).")
    return bp


def hold_for_power_cut():
    console.print("\n[bold red]Holding the CPU. Unplug the USB cable now.[/bold red] "
                  "[dim](Ctrl+C to quit; quitting before the unplug lets the save finish.)[/dim]")
    while True:
        time.sleep(1)


def main():
    ap = build_arg_parser(__doc__.splitlines()[0])
    ap.add_argument("--power-loss", type=int, default=None, metavar="WORD",
                    help=f"stop the commit after table word WORD (0-{TABLE_WORDS - 1}) "
                         "is written, then hold for a USB unplug")
    args = ap.parse_args()
    if args.power_loss is not None and not 0 <= args.power_loss < TABLE_WORDS:
        die(f"--power-loss must be 0-{TABLE_WORDS - 1}")
    auto_mode = args.auto or os.environ.get("RUNEIT_TRACE_AUTO") == "1"

    if not Path(args.elf).is_file():
        die(f"ELF not found: {args.elf}\n       Build the project in STM32CubeIDE first.")

    gdb_path = locate_gdb(args.gdb_path)
    print(f"Using gdb: {gdb_path}")
    print(f"Using ELF: {args.elf}")

    session = GdbSession(gdb_path, args.elf, prompt="(change-mk-trace) ")
    state = fresh_state()
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")

        bp_confirm = session.set_checked_breakpoint_by_line("mode_change_mk.c", 87)
        bp_old = session.set_checked_breakpoint_by_line("mode_change_mk.c", 66)
        bp_derived = session.set_checked_breakpoint_by_line("kdf.c", 78)
        bp_committed = session.set_checked_breakpoint_by_line("session.c", 202)
        handlers = {bp_confirm: handle_confirm, bp_old: handle_old,
                    bp_derived: handle_derived, bp_committed: handle_committed}
        if args.power_loss is None:
            handlers[session.set_checked_breakpoint_by_line("app.c", 211)] = handle_menu
        else:
            handlers[arm_power_loss(session, state, args.power_loss)] = handle_power_loss

        print("\nAll breakpoints verified against the loaded ELF.")
        print("Log in, choose 'Change master key', and type the new key and its confirmation.\n")

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
            if state.get("hold"):
                hold_for_power_cut()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping.[/dim]")
    finally:
        session.close()


if __name__ == "__main__":
    main()
