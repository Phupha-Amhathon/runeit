#!/usr/bin/env python3
"""Live, paced trace of GENERATE_MODE on RUNEIT, over gdb - TUI variant,
draft 2. Builds on tools/generate_trace_tui.py (kept untouched as draft 1).

What's new in draft 2:
  - The save is split into two visible moments. "pending" is shown before
    the commit runs: the target entry is flagged on the OLD partition, and
    nothing on flash has changed yet. "done" is shown after the commit:
    the active partition has toggled, and the entry's new ciphertext is read
    back from the NEW partition.
  - A full-width "Password table on flash" panel at the bottom shows the
    raw 48 bytes of each of the first 10 entries, read fresh from the
    active partition every pause.
  - The Flash header panel reads the real A/B state every pause, so it
    shows the toggle as it happens instead of a fixed caption.

Breakpoints (4, under the 6-hardware-comparator ceiling measured on this
board):
  - mode_generate.c:333, :349  Entropy_Pool_Absorb() for temp/light ADC.
  - mode_generate.c:187        the accept/reject check inside ProduceChars().
  - mode_generate.c:387        Session_Save() - the commit.

Pacing: the first sampling round, the first byte-to-character mapping, and
both save moments pause for Enter. Later repeats of the sampling and mapping
events update the same panels and advance on a short timer, ignoring --auto.

Scope: GENERATE_MODE only. Needs an already-open session, and you must be
past the id/name/classes/length prompts before starting this tool. Log in
over the serial terminal, choose "Generate password", answer the prompts,
then start this tool.

Precondition: the ST-LINK GDB server must already be listening on its own:

    python3 tools/start_gdbserver.py

Rebuild and reflash from STM32CubeIDE first if Src/ has changed since
Debug/runeit.elf was last built. This tool checks its breakpoint locations
against the loaded ELF and refuses to run against a stale one.

Usage:
    python3 tools/generate_trace_tui_v2.py
    python3 tools/generate_trace_tui_v2.py --auto
Requires: pip install pexpect rich
"""
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gdb_trace_common import (  # noqa: E402
    GdbSession, LAYOUT, build_arg_parser, die, hexstr, locate_gdb, pace,
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
ENC_KEY_LEN = 32  # enc = HMAC(K, "RUNEIT-enc-v1"), the session key in session.c
ENTROPY_BLOCK_SAMPLES = 512  # Inc/crypto/entropy_pool.h
ENTRY_COUNT_SHOWN = 10
FAST_DELAY_S = 0.2
DIM = "grey50"

console = Console()


def fresh_state():
    return {
        "highlight": None,        # "input" / "sample" / "mapping" / "save" / None
        "seen_input": False,
        "first_mapping_shown": False,
        "id": None, "name": None, "classes": None, "length": None,
        "channel": None, "samples": None, "bias_pct": None, "yield_pairs": None,
        "sample_loc": None,
        "byte_value": None, "draw_bits": None, "threshold": None, "accepted": None, "mapped_char": None,
        "chars_done": 0, "pwd_so_far": b"",
        "write_phase": None,      # None / "pending" / "done"
        "save_id": None, "save_name": None, "save_pwd": None,
        "save_addr": None, "save_cipher": None,
        "g_state": None, "s_authorized": None, "session_key": None,
        "active_sector": None, "active_addr": None,
        "flash_a": None, "flash_b": None,
        "entries": None,          # raw bytes of the first ENTRY_COUNT_SHOWN entries, active partition
    }


def read_input(session, state):
    """Read once. s_id, s_name, s_charset and s_length are set before any
    breakpoint in this file can fire."""
    state["id"] = session.read_value("s_id")
    name = session.read_bytes("s_name", 16)
    state["name"] = bytes(name).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    charset_len = int(session.read_value("s_charset_len"))
    charset = session.read_bytes("s_charset", charset_len) if charset_len else []
    state["classes"] = bytes(charset).decode("ascii", errors="replace")
    state["length"] = session.read_value("s_length")
    state["seen_input"] = True


def read_persistent(session, state):
    """Everything on screen is re-read fresh every pause. Nothing is cached
    from an earlier pause, and that includes the entries table."""
    state["g_state"] = session.read_value("'app.c'::g_state")
    state["s_authorized"] = session.read_value("'session.c'::s_authorized")
    state["session_key"] = bytes(session.read_bytes("'session.c'::s_key", ENC_KEY_LEN))

    state["active_addr"] = session.read_int("'partition_store.c'::s_active.addr")
    state["active_sector"] = session.read_value("'partition_store.c'::s_active.sector")

    state["flash_a"] = read_partition_header(session, LAYOUT["a_addr"])
    state["flash_b"] = read_partition_header(session, LAYOUT["b_addr"])

    chars_done = int(session.read_value("s_chars_done"))
    state["chars_done"] = chars_done
    state["pwd_so_far"] = bytes(session.read_bytes("s_pwd", chars_done)) if chars_done else b""

    entries_len = ENTRY_COUNT_SHOWN * PWD_ENTRY_SIZE
    state["entries"] = bytes(session.read_bytes(
        hex(state["active_addr"] + LAYOUT["header_len"]), entries_len))


def border_for(state, name, color):
    return color if state["highlight"] == name else "grey37"


def panel_input(state):
    border = border_for(state, "input", "cyan")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if not state["seen_input"]:
        for label in ("entry id", "name", "classes", "length"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("entry id", str(state["id"]))
        t.add_row("name", f'"{state["name"]}"')
        t.add_row("classes", state["classes"])
        t.add_row("length", str(state["length"]))
    return Panel(t, title="[b]Input[/b]", title_align="left",
                 subtitle="already typed, read on first hit", subtitle_align="left",
                 border_style=border)


def panel_sample(state):
    border = border_for(state, "sample", "cyan" if state["channel"] == "temp" else "yellow")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["channel"] is None:
        for label in ("channel", "raw samples", "LSB bias", "VN yield"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("channel", state["channel"])
        t.add_row("raw samples", ", ".join(str(s) for s in state["samples"][:12]) + ", … (512 total)")
        t.add_row("LSB bias", f"{state['bias_pct']:.1f}% ones")
        t.add_row("VN yield", f"{state['yield_pairs']} / 256 pairs accepted")
    return Panel(t, title="[b]Entropy sampling[/b]", title_align="left",
                 subtitle=state["sample_loc"] or "mode_generate.c", subtitle_align="left",
                 border_style=border)


def panel_mapping(state):
    border = border_for(state, "mapping", "magenta")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["byte_value"] is None:
        for label in ("debiased draw", "reject if ≥", "verdict", "maps to"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("debiased draw", f"{state['byte_value']}  (0x{state['byte_value']:02x}, "
                                   f"{state['draw_bits']} bits)")
        t.add_row("reject if ≥", f"{state['threshold']} (out of {1 << state['draw_bits']})")
        if state["accepted"]:
            t.add_row("verdict", Text("ACCEPTED", style="bold green"))
            t.add_row("maps to", Text(f"-> {state['mapped_char']!r} (not yet written)", style="bold white"))
        else:
            t.add_row("verdict", Text("REJECTED", style="bold red"))
            t.add_row("maps to", Text("discarded, drawing another byte", style=DIM))
    pwd_display = state["pwd_so_far"].decode("ascii", errors="replace") if state["pwd_so_far"] else ""
    t.add_row("password so far", pwd_display or Text("—", style=DIM))
    group = Group(t, Text("values ≥ the cutoff are discarded so every character stays equally likely",
                           style=f"italic {DIM}"))
    return Panel(group, title="[b]Character mapping[/b]", title_align="left",
                 subtitle="ProduceChars, mode_generate.c:187", subtitle_align="left",
                 border_style=border)


def panel_save(state):
    phase = state["write_phase"]
    if phase == "pending":
        border = "yellow"
    elif phase == "done":
        border = "green"
    else:
        border = border_for(state, "save", "green")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["save_pwd"] is None:
        for label in ("password (plain)", "flash address", "on flash (cipher)"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("password (plain)", f'"{state["save_pwd"]}"')
        if phase == "pending":
            t.add_row("flash address", Text("pending … (blocking write in progress)", style="yellow"))
            t.add_row("on flash (cipher)", Text("pending …", style="yellow"))
        else:
            t.add_row("flash address", f"0x{state['save_addr']:08x}")
            t.add_row("on flash (cipher)", hexstr(state["save_cipher"]))
    group = Group(t, Text("run generate then retrieve_trace afterward to see this decrypt "
                           "back to the same password", style=f"italic {DIM}"))
    return Panel(group, title="[b]Save[/b]", title_align="left",
                 subtitle="SaveEntry, mode_generate.c:387", subtitle_align="left",
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
    return Panel(t, title="[b]Session & FSM[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style="grey37")


def panel_flash(state):
    phase = state["write_phase"]
    if phase == "pending":
        border = "yellow"
    elif phase == "done":
        border = "green"
    else:
        border = "grey37"
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    a, b = state["flash_a"], state["flash_b"]
    if a is None:
        t.add_row("active", Text("—", style=DIM))
    else:
        t.add_row("active", f"sector {state['active_sector']} @ 0x{state['active_addr']:08x}")
        for name, addr, hdr in (("A", LAYOUT["a_addr"], a), ("B", LAYOUT["b_addr"], b)):
            if hdr["status"] == "empty":
                shown = Text("empty (erased)", style=DIM)
            else:
                shown = f"{hdr['status']} · version {hdr['version']}"
            t.add_row(f"{name} (0x{addr:08x})", shown)
    caption = None
    if phase == "pending":
        caption = "commit about to write the INACTIVE partition - the active one is still untouched"
    elif phase == "done":
        caption = "the inactive partition was written, then became active - the old one is left intact"
    group = Group(t, Text(caption, style=f"italic {DIM}") if caption else Text(""))
    return Panel(group, title="[b]Flash header[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style=border)


def panel_entries(state):
    t = Table(box=None, show_header=True, header_style=DIM, padding=(0, 1), expand=True)
    t.add_column("id", style=DIM, width=3)
    t.add_column("address", style=DIM, no_wrap=True)
    t.add_column("raw 48 bytes on flash (ciphertext)", overflow="fold")
    if state["entries"] is None:
        t.add_row("—", "—", Text("—", style=DIM))
    else:
        base = state["active_addr"] + LAYOUT["header_len"]
        for i in range(ENTRY_COUNT_SHOWN):
            chunk = state["entries"][i * PWD_ENTRY_SIZE:(i + 1) * PWD_ENTRY_SIZE]
            addr = base + i * PWD_ENTRY_SIZE
            label = str(i)
            style = None
            if state["save_id"] is not None and i == state["save_id"]:
                if state["write_phase"] == "pending":
                    label = f"{i} ← writing"
                    style = "bold yellow"
                elif state["write_phase"] == "done":
                    label = f"{i} ← written"
                    style = "bold green"
            t.add_row(label, f"0x{addr:08x}", hexstr(chunk), style=style)
    return Panel(t, title="[b]Password table on flash[/b]", title_align="left",
                 subtitle=f"active partition + {LAYOUT['header_len']} (header), "
                          f"every entry is 48 bytes, all ciphertext",
                 subtitle_align="left", border_style="grey37")


def render(state):
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    col1 = Group(panel_input(state), panel_sample(state))
    col2 = Group(panel_mapping(state), panel_save(state))
    col3 = Group(panel_session(state), panel_flash(state))
    grid.add_row(col1, col2, col3)
    console.clear()
    console.print(Text("generate_trace (tui v2)", style="bold"),
                  Text(" — live GENERATE_MODE pipeline, draft 2", style=DIM))
    console.print(grid)
    console.print(panel_entries(state))


def handle_absorb(session, state, auto_mode, channel, loc):
    rounds = int(session.read_value("s_rounds"))
    if rounds == 1 and channel == "temp":
        # A new generate run is starting - clear anything left from the last one.
        state.update(fresh_state())
    if not state["seen_input"]:
        read_input(session, state)

    samples = session.read_u16_list("s_samples", ENTROPY_BLOCK_SAMPLES)
    ones = sum(s & 0x1 for s in samples)
    bias_pct = 100.0 * ones / len(samples)
    pairs = [(samples[i] & 0x1, samples[i + 1] & 0x1) for i in range(0, len(samples) - 1, 2)]
    accepted_pairs = sum(1 for a, b in pairs if a != b)

    state["highlight"] = "input" if rounds == 1 and channel == "temp" else "sample"
    state["channel"] = channel
    state["samples"] = samples
    state["bias_pct"] = bias_pct
    state["yield_pairs"] = accepted_pairs
    state["sample_loc"] = loc
    read_persistent(session, state)
    render(state)

    if rounds == 1:
        pace(auto_mode)
    else:
        time.sleep(FAST_DELAY_S)


def handle_temp(session, state, auto_mode):
    handle_absorb(session, state, auto_mode, "temp", "mode_generate.c:333")


def handle_light(session, state, auto_mode):
    handle_absorb(session, state, auto_mode, "light", "mode_generate.c:349")


def read_draw_bits(session):
    """Bits per draw for the chosen charset. Firmware older than the
    dynamic-draw change has no s_draw_bits and always drew a full byte."""
    try:
        return int(session.read_value("(unsigned)s_draw_bits"))
    except RuntimeError:
        return 8


def handle_mapping(session, state, auto_mode):
    if not state["seen_input"]:
        read_input(session, state)
    value = int(session.read_value("(unsigned)value"))
    threshold = int(session.read_value("s_reject_threshold"))
    state["draw_bits"] = read_draw_bits(session)
    accepted = value < threshold
    charset_len = int(session.read_value("s_charset_len"))
    mapped_char = None
    if accepted:
        charset = session.read_bytes("s_charset", charset_len)
        mapped_char = chr(charset[value % charset_len])

    narrated = not state["first_mapping_shown"]
    state["first_mapping_shown"] = True

    state["highlight"] = "mapping"
    state["byte_value"] = value
    state["threshold"] = threshold
    state["accepted"] = accepted
    state["mapped_char"] = mapped_char
    read_persistent(session, state)  # s_pwd/s_chars_done here do NOT include this byte yet
    render(state)

    if narrated:
        pace(auto_mode)
    else:
        time.sleep(FAST_DELAY_S)


def handle_save(session, state, auto_mode):
    entry_id = int(session.read_value("s_id"))
    name = session.read_bytes("s_name", 16)
    pwd = session.read_bytes("s_pwd", 32)
    state["save_id"] = entry_id
    state["save_name"] = bytes(name).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    state["save_pwd"] = bytes(pwd).split(b"\x00", 1)[0].decode("ascii", errors="replace")

    # Pending moment: the commit has not run yet. Read everything from the
    # partition that is still active, so the table shows the OLD bytes.
    state["write_phase"] = "pending"
    state["highlight"] = "save"
    read_persistent(session, state)
    render(state)
    pace(auto_mode)

    session.cmd("next")  # runs Session_Save() / Partition_Store_Commit() to completion

    # Done moment: the active partition has toggled. Read the new layout
    # back from flash rather than assuming the toggle happened.
    state["write_phase"] = "done"
    read_persistent(session, state)
    addr = state["active_addr"] + LAYOUT["header_len"] + entry_id * PWD_ENTRY_SIZE
    state["save_addr"] = addr
    state["save_cipher"] = session.read_bytes(hex(addr), PWD_ENTRY_SIZE)
    render(state)
    pace(auto_mode)


def main():
    ap = build_arg_parser(__doc__.splitlines()[0])
    args = ap.parse_args()
    auto_mode = args.auto or os.environ.get("RUNEIT_TRACE_AUTO") == "1"

    if not Path(args.elf).is_file():
        die(f"ELF not found: {args.elf}\n       Build the project in STM32CubeIDE first.")

    gdb_path = locate_gdb(args.gdb_path)
    print(f"Using gdb: {gdb_path}")
    print(f"Using ELF: {args.elf}")

    session = GdbSession(gdb_path, args.elf, prompt="(generate-trace-v2) ")
    state = fresh_state()
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")
        print(f"Partition header: {LAYOUT['header_len']} bytes, magic 0x{LAYOUT['magic']:08x} "
              f"(from the ELF and Inc/app/partition_store.h).")

        bp_temp = session.set_checked_breakpoint_by_line("mode_generate.c", 333)
        bp_light = session.set_checked_breakpoint_by_line("mode_generate.c", 349)
        bp_mapping = session.set_checked_breakpoint_by_line("mode_generate.c", 187)
        bp_save = session.set_checked_breakpoint_by_line("mode_generate.c", 387)
        handlers = {bp_temp: handle_temp, bp_light: handle_light,
                    bp_mapping: handle_mapping, bp_save: handle_save}

        print("\nAll breakpoints verified against the loaded ELF.")
        print("Log in over the serial terminal, choose 'Generate password', and answer")
        print("the id/name/class/length prompts first. Round 1, the first byte mapping")
        print("and both save moments pause; the rest fast-forward in place.\n")

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
