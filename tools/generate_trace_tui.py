#!/usr/bin/env python3
"""Live, paced trace of GENERATE_MODE on RUNEIT, over gdb - TUI variant
(rich-rendered fixed dashboard), draft 1.

One mode, one file, by design: this stays separate from the other modes'
TUI files until each is individually proven on real hardware, the same way
tools/mk_auth_trace_tui.py was kept separate from tools/mk_auth_trace.py.

GENERATE_MODE turns sensor noise into a password. A thermistor and a light
sensor are sampled through the ADC; the raw bits are biased (skewed as far
as 83/17 in testing, per README.md), so they are run through Von Neumann
debiasing before being turned into characters via rejection sampling. This
tool shows all of it on one fixed dashboard: the id/name/classes/length you
already typed, the raw samples and bias per channel, each debiased byte and
exactly which character (or rejection) it maps to, and finally the
plaintext going in next to the ciphertext that lands on flash.

4 breakpoints, comfortably under the 6-hardware-comparator ceiling measured
on this board (see tools/mk_auth_trace.md's history for how that was
confirmed):
  - mode_generate.c:333, :349  Entropy_Pool_Absorb() for the temp/light
                                 ADC channels (unchanged from the earlier
                                 plain-text design).
  - mode_generate.c:387         Session_Save() - the commit.
  - mode_generate.c:187         NEW - the if (value < s_reject_threshold)
                                 check inside ProduceChars(), where a
                                 debiased byte is accepted into the
                                 password or discarded. Never breakpoints
                                 inside entropy_pool.c/rng_health.c
                                 (shared with FIRST_MEET/CHANGE_MK's salt
                                 generation, per other_modes_trace.py's
                                 generate mode) or partition_store.c/
                                 xor_cipher.c (shared with RETRIEVE_MODE) -
                                 breaking only inside mode_generate.c's own
                                 static helpers avoids both of those
                                 cross-mode collisions entirely.

Pacing is asymmetric, same reasoning as the plain-text version but
rendered differently now that the dashboard doesn't scroll: round 1's
sampling and the FIRST byte-to-character mapping ever seen both pause for
Enter so a presenter can narrate; every later sampling/mapping event
updates the same panels in place and auto-advances quickly instead of
collapsing into one-line summaries (the thing that forced that compromise
- a scrolling terminal - doesn't apply to a fixed dashboard). The save
step always pauses.

Scope: GENERATE_MODE only. Needs an ALREADY OPEN SESSION, and you must
already be past the id/name/classes/length prompts before starting this
tool - its breakpoints only exist once ProduceChars()/the ADC sampling
loop is reached. Log in over the serial terminal, choose "Generate
password", and answer those prompts first.

Precondition: the ST-LINK GDB server must already be listening on its own,
standalone - not via an open STM32CubeIDE debug session. End any open
CubeIDE debug session for this board first, then in its own terminal:

    python3 tools/start_gdbserver.py

Also rebuild and reflash from STM32CubeIDE first if Src/ has changed since
Debug/runeit.elf was last built - this script checks that its breakpoints
land in the expected lines and refuses to run against a stale ELF.

Usage:
    python3 tools/generate_trace_tui.py
    python3 tools/generate_trace_tui.py --auto
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
ENTROPY_BLOCK_SAMPLES = 512  # Inc/crypto/entropy_pool.h
FAST_DELAY_S = 0.2  # pacing for non-narrated rounds/mappings, ignores --auto
DIM = "grey50"

console = Console()


def fresh_state():
    return {
        "highlight": None,      # "input" / "sample" / "mapping" / "save" / None
        "seen_any": False,      # have we shown the Input panel's cyan highlight yet
        "first_mapping_shown": False,
        "id": None, "name": None, "classes": None, "length": None,
        "channel": None, "samples": None, "bias_pct": None, "yield_pairs": None,
        "sample_loc": None,
        "byte_value": None, "draw_bits": None, "threshold": None, "accepted": None, "mapped_char": None,
        "chars_done": 0, "pwd_so_far": b"",
        "save_id": None, "save_name": None, "save_pwd": None,
        "save_addr": None, "save_cipher": None,
        "g_state": None, "s_authorized": None,
        "active_sector": None, "active_addr": None,
        "flash_a": None, "flash_b": None,
    }


def read_persistent(session, state):
    """Re-reads everything that's always on screen, fresh, every pause -
    never cached. Also re-reads s_chars_done/s_pwd here so "password so
    far" always reflects the real committed buffer, not a Python-side
    running total."""
    state["g_state"] = session.read_value("'app.c'::g_state")
    state["s_authorized"] = session.read_value("'session.c'::s_authorized")

    state["active_addr"] = session.read_int("'partition_store.c'::s_active.addr")
    state["active_sector"] = session.read_value("'partition_store.c'::s_active.sector")

    flash_a_raw = session.read_bytes(hex(PARTITION_A_ADDR), PARTITION_HEADER_LEN)
    flash_b_raw = session.read_bytes(hex(PARTITION_B_ADDR), PARTITION_HEADER_LEN)
    state["flash_a"] = parse_header(flash_a_raw)
    state["flash_b"] = parse_header(flash_b_raw)

    chars_done = int(session.read_value("s_chars_done"))
    state["chars_done"] = chars_done
    state["pwd_so_far"] = bytes(session.read_bytes("s_pwd", chars_done)) if chars_done else b""
    state["length"] = session.read_value("s_length")


def read_input(session, state):
    """s_id/s_name/s_charset are already set by the time any breakpoint in
    this file fires - the prompts for them happen before sampling starts."""
    state["id"] = session.read_value("s_id")
    name = session.read_bytes("s_name", 16)
    state["name"] = bytes(name).split(b"\x00", 1)[0].decode("ascii", errors="replace")
    charset_len = int(session.read_value("s_charset_len"))
    charset = session.read_bytes("s_charset", charset_len) if charset_len else []
    state["classes"] = bytes(charset).decode("ascii", errors="replace")


def parse_header(raw_bytes):
    magic, version, kdf_iter, salt, auth, crc32 = struct.unpack("<3I16s32sI", bytes(raw_bytes))
    return {"valid": magic == PARTITION_MAGIC, "version": version}


def border_for(state, name, color):
    return color if state["highlight"] == name else "grey37"


def panel_input(state):
    border = border_for(state, "input", "cyan")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["id"] is None:
        for label in ("entry id", "name", "classes", "length"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("entry id", state["id"])
        t.add_row("name", f'"{state["name"]}"')
        t.add_row("classes", state["classes"])
        t.add_row("length", state["length"])
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
    border = border_for(state, "save", "green")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    if state["save_pwd"] is None:
        for label in ("password (plain)", "flash address", "on flash (cipher)"):
            t.add_row(label, Text("—", style=DIM))
    else:
        t.add_row("password (plain)", f'"{state["save_pwd"]}"')
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
    t.add_row("g_state", state["g_state"] or Text("—", style=DIM))
    t.add_row("s_authorized", state["s_authorized"] or Text("—", style=DIM))
    return Panel(t, title="[b]Session & FSM[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style="grey37")


def panel_flash(state):
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    a, b = state.get("flash_a"), state.get("flash_b")
    if a is None:
        t.add_row("active", Text("—", style=DIM))
    else:
        t.add_row("active", f"sector {state['active_sector']} @ 0x{state['active_addr']:08x}")
        t.add_row("A (0x08008000)", f"{'valid' if a['valid'] else 'INVALID'} · version {a['version']}")
        t.add_row("B (0x0800c000)", f"{'valid' if b['valid'] else 'INVALID'} · version {b['version']}")
    return Panel(t, title="[b]Flash partitions[/b]", title_align="left",
                 subtitle="live, every pause", subtitle_align="left", border_style="grey37")


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
    console.print(Text("generate_trace (tui)", style="bold"),
                  Text(" — live GENERATE_MODE pipeline, draft 1", style=DIM))
    console.print(grid)


def mark_seen(state):
    if not state["seen_any"]:
        state["seen_any"] = True
        return True
    return False


def handle_absorb(session, state, auto_mode, channel, loc):
    rounds = int(session.read_value("s_rounds"))
    samples = session.read_u16_list("s_samples", ENTROPY_BLOCK_SAMPLES)
    ones = sum(s & 0x1 for s in samples)
    bias_pct = 100.0 * ones / len(samples)
    pairs = [(samples[i] & 0x1, samples[i + 1] & 0x1) for i in range(0, len(samples) - 1, 2)]
    accepted_pairs = sum(1 for a, b in pairs if a != b)

    just_revealed_input = mark_seen(state)
    if just_revealed_input:
        read_input(session, state)

    state["highlight"] = "input" if just_revealed_input else "sample"
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
    handle_absorb(session, state, auto_mode, "temp (ADC_DRV_CH_TEMP)", "mode_generate.c:333")


def handle_light(session, state, auto_mode):
    handle_absorb(session, state, auto_mode, "light (ADC_DRV_CH_LIGHT)", "mode_generate.c:349")


def read_draw_bits(session):
    """Bits per draw for the chosen charset. Firmware older than the
    dynamic-draw change has no s_draw_bits and always drew a full byte."""
    try:
        return int(session.read_value("(unsigned)s_draw_bits"))
    except RuntimeError:
        return 8


def handle_mapping(session, state, auto_mode):
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
    read_persistent(session, state)  # re-reads s_pwd/s_chars_done - NOT yet including this byte
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

    session.cmd("next")  # let Session_Save()/Partition_Store_Commit() finish

    active_addr = session.read_int("'partition_store.c'::s_active.addr")
    addr = active_addr + PARTITION_HEADER_LEN + entry_id * PWD_ENTRY_SIZE
    cipher = session.read_bytes(hex(addr), PWD_ENTRY_SIZE)
    state["save_addr"] = addr
    state["save_cipher"] = cipher

    state["highlight"] = "save"
    read_persistent(session, state)
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

    session = GdbSession(gdb_path, args.elf, prompt="(generate-trace-tui) ")
    state = fresh_state()
    try:
        session.connect(args.host, args.port)
        print(f"Connected to the GDB server at {args.host}:{args.port}.")

        bp_temp = session.set_checked_breakpoint_by_line("mode_generate.c", 333)
        bp_light = session.set_checked_breakpoint_by_line("mode_generate.c", 349)
        bp_mapping = session.set_checked_breakpoint_by_line("mode_generate.c", 187)
        bp_save = session.set_checked_breakpoint_by_line("mode_generate.c", 387)
        handlers = {bp_temp: handle_temp, bp_light: handle_light,
                    bp_mapping: handle_mapping, bp_save: handle_save}

        print("\nAll breakpoints verified against the loaded ELF.")
        print("Log in over the serial terminal, choose 'Generate password', and answer")
        print("the id/name/class/length prompts first. Once sampling starts, round 1 and")
        print("the first byte mapping pause for narration; everything after fast-forwards.\n")

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
