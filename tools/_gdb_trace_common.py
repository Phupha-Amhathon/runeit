"""Shared plumbing for RUNEIT's live gdb trace tools.

Not a standalone script. `mk_auth_trace.py`, `first_meet_trace.py`,
`change_mk_trace.py`, `retrieve_trace.py` and `generate_trace.py` all import
from this module: the `GdbSession` wrapper around an interactive
`arm-none-eabi-gdb` CLI (driven with pexpect, same read/send-until-pattern
model `tools/hw_test.py` uses for the serial port, applied to gdb's prompt
instead), breakpoint self-checks, output formatting helpers, and the base
persistent-state block every tool shows at every pause.

Each tool's breakpoint table, per-step narration, and main loop are its own
- only the mechanical parts live here.
"""
import glob
import os
import re
import shutil
import struct
import sys
import time
from pathlib import Path

try:
    import pexpect
except ImportError:
    print("This script needs pexpect: pip install pexpect", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ELF = REPO_ROOT / "Debug" / "runeit.elf"
PARTITION_A_ADDR = 0x08008000
PARTITION_B_ADDR = 0x0800C000
PARTITION_HEADER_LEN = 64
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
PARTITION_MAGIC = 0x52554E32


def die(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def hexstr(byte_values):
    return " ".join(f"{b:02x}" for b in byte_values)


def locate_gdb(explicit):
    if explicit:
        return explicit
    env = os.environ.get("RUNEIT_GDB")
    if env:
        return env
    found = shutil.which("arm-none-eabi-gdb")
    if found:
        return found
    candidates = sorted(glob.glob(
        "/opt/st/stm32cubeide_*/plugins/*gnu-tools-for-stm32*/tools/bin/arm-none-eabi-gdb"))
    if candidates:
        return candidates[-1]
    die("could not find arm-none-eabi-gdb.\n"
        "       Pass --gdb-path, or set RUNEIT_GDB=/path/to/tools/bin/arm-none-eabi-gdb\n"
        "       (the same toolchain bin/ directory README.md's Building section uses "
        "for arm-none-eabi-gcc).")


def parse_flash_header(raw_bytes):
    magic, version, kdf_iter, salt, auth, crc32 = struct.unpack(
        "<3I16s32sI", bytes(raw_bytes))
    valid = "valid" if magic == PARTITION_MAGIC else "UNRECOGNISED"
    return (f"magic=0x{magic:08x} ({valid}) version={version} kdf_iter={kdf_iter} "
            f"crc32=0x{crc32:08x}\n"
            f"      salt={hexstr(salt)}\n"
            f"      auth={hexstr(auth)}")


def banner(step, title):
    line = f" [{step}] {title} "
    pad = max(0, 78 - len(line))
    return "\n" + "=" * (pad // 2) + line + "=" * (pad - pad // 2)


def pace(auto_mode):
    if auto_mode:
        time.sleep(1.5)
        return
    try:
        input("\nPress Enter to continue...\n")
    except EOFError:
        pass


def build_arg_parser(description):
    import argparse
    ap = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elf", default=str(DEFAULT_ELF),
                     help=f"path to the built ELF (default: {DEFAULT_ELF})")
    ap.add_argument("--gdb-path", default=None,
                     help="path to arm-none-eabi-gdb (default: $RUNEIT_GDB, "
                          "then PATH, then the usual STM32CubeIDE install location)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=61234)
    ap.add_argument("--auto", action="store_true",
                     help="pace with a timer instead of waiting for Enter")
    return ap


class GdbSession:
    def __init__(self, gdb_path, elf_path, prompt="(gdb-trace) ", timeout=30):
        self.PROMPT = prompt
        self.child = pexpect.spawn(
            gdb_path, ["-q", "--nx", str(elf_path)],
            encoding="utf-8", timeout=timeout)
        # Local pty echo would otherwise echo "set prompt (foo) " back at us,
        # and since that echo contains the prompt string itself as a
        # substring, expect_exact(PROMPT) matches the echo instead of the
        # real prompt - leaving every later command one step behind for the
        # rest of the session (confirmed on real hardware: every read came
        # back as the previous command's output).
        self.child.setecho(False)
        self.child.expect_exact("(gdb) ")
        self.child.sendline(f"set prompt {self.PROMPT}")
        self.child.expect_exact(self.PROMPT)
        self.cmd("set pagination off")
        self.cmd("set confirm off")
        self.cmd("set height 0")
        self.cmd("set width 0")
        # Recent gdb auto-colors its output when it thinks it's on a
        # terminal (which a pty is), e.g. "Breakpoint 1 at \x1b[34m0x...".
        # Confirmed on real hardware: that broke the "at (0x...)" regex
        # below, since the color code sits between "at " and the address.
        self.cmd("set style enabled off")

    def cmd(self, text, timeout=30):
        self.child.sendline(text)
        self.child.expect_exact(self.PROMPT, timeout=timeout)
        out = ANSI_RE.sub("", self.child.before)
        lines = out.splitlines()
        if lines and lines[0].strip() == text.strip():
            lines = lines[1:]
        return "\n".join(lines).strip()

    def connect(self, host, port):
        out = self.cmd(f"target remote {host}:{port}", timeout=15)
        if "Remote debugging using" not in out:
            die(f"could not connect to the GDB server at {host}:{port}.\n\n{out}\n\n"
                "       Make sure the ST-LINK GDB server is already listening on its own\n"
                "       (end any open STM32CubeIDE debug session for this board first, "
                "then run e.g.\n"
                f"         ST-LINK_gdbserver -p {port} -e\n"
                "       in its own terminal), then rerun this tool.")
        return out

    def set_checked_breakpoint_by_func(self, func_name):
        """Break at a function's entry. Only safe for globally unique,
        non-static function names (no risk of a same-named static helper
        in a different file)."""
        out = self.cmd(f"break {func_name}")
        m = re.search(r"Breakpoint (\d+) at (0x[0-9a-fA-F]+)", out)
        if not m:
            die(f"failed to set a breakpoint at {func_name}:\n{out}")
        bp_num, addr = m.group(1), m.group(2)
        sym_out = self.cmd(f"info symbol {addr}")
        resolved = sym_out.split()[0] if sym_out.strip() else ""
        if func_name not in resolved:
            die(f"breakpoint at {func_name} resolved inside '{resolved}', not "
                f"'{func_name}'.\n\n"
                "       Debug/runeit.elf is likely stale relative to the current source.\n"
                "       Rebuild and reflash from STM32CubeIDE, then rerun this tool.")
        return bp_num

    def set_checked_breakpoint_by_line(self, file_name, line_no):
        """Break at file:line and verify, via gdb's own DWARF line table,
        that the address really is inside that file at that line. This
        survives both a stale ELF and the compiler inlining a static
        helper (the Debug build uses -Og), and also tells apart two
        different files that happen to define a same-named static
        function."""
        location = f"{file_name}:{line_no}"
        out = self.cmd(f"break {location}")
        m = re.search(r"Breakpoint (\d+) at (0x[0-9a-fA-F]+)", out)
        if not m:
            die(f"failed to set a breakpoint at {location}:\n{out}")
        bp_num, addr = m.group(1), m.group(2)
        line_out = self.cmd(f"info line *{addr}")
        lm = re.search(r'Line (\d+) of "([^"]+)"', line_out)
        if not lm:
            die(f"could not verify breakpoint at {location}:\n{line_out}")
        actual_line, actual_file = int(lm.group(1)), lm.group(2)
        if actual_line != line_no or not actual_file.endswith(file_name):
            sym_out = self.cmd(f"info symbol {addr}")
            die(f"breakpoint at {location} resolved to {actual_file}:{actual_line}, "
                f"not the expected location.\n"
                f"(info symbol: {sym_out.strip()})\n\n"
                "       This means either Debug/runeit.elf is stale relative to the\n"
                "       current source (rebuild and reflash), or the compiler\n"
                "       optimized/inlined this spot differently than expected (the\n"
                "       Debug build uses -Og) and this tool's breakpoint table needs\n"
                "       updating to match current source.")
        return bp_num

    def read_value(self, expr):
        out = self.cmd(f"print {expr}")
        m = re.search(r"\$\d+\s*=\s*(.*)", out, re.DOTALL)
        if not m:
            raise RuntimeError(f"unexpected output for 'print {expr}': {out!r}")
        return m.group(1).strip()

    def read_int(self, expr):
        """Reads an integer expression in hex and returns it as a Python
        int, for use in address arithmetic (e.g. partition base + offset)."""
        out = self.cmd(f"print/x {expr}")
        m = re.search(r"\$\d+\s*=\s*(0x[0-9a-fA-F]+)", out)
        if not m:
            raise RuntimeError(f"unexpected output for 'print/x {expr}': {out!r}")
        return int(m.group(1), 16)

    def _read_x(self, expr, n, unit):
        if n == 0:
            return []
        out = self.cmd(f"x/{n}x{unit} {expr}")
        values = []
        for line in out.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            _, rest = line.split(":", 1)
            for tok in rest.split():
                if tok.startswith("0x"):
                    values.append(int(tok, 16))
        if len(values) < n:
            raise RuntimeError(f"expected {n} items from '{expr}', got {len(values)}: {out!r}")
        return values[:n]

    def read_bytes(self, expr, n):
        return self._read_x(expr, n, "b")

    def read_u16_list(self, expr, n):
        return self._read_x(expr, n, "h")

    def continue_and_wait(self):
        self.child.sendline("continue")
        self.child.expect_exact(self.PROMPT, timeout=None)
        out = ANSI_RE.sub("", self.child.before)
        lines = out.splitlines()
        if lines and lines[0].strip() == "continue":
            lines = lines[1:]
        return "\n".join(lines).strip()

    def close(self):
        try:
            self.child.sendintr()
            self.child.expect_exact(self.PROMPT, timeout=10)
        except Exception:
            pass
        for c in ("delete", "detach", "quit"):
            try:
                self.child.sendline(c)
                self.child.expect_exact([self.PROMPT, pexpect.EOF], timeout=10)
            except Exception:
                pass
        try:
            self.child.close(force=True)
        except Exception:
            pass


def dump_base_state(session):
    """The block every tool re-reads fresh at every single pause: which
    menu state the board is in, which flash partition is active, both raw
    flash headers, and the session key/authorized flag. Returns a list of
    lines; callers append or prepend mode-specific lines and join them."""
    g_state = session.read_value("'app.c'::g_state")
    have_active = session.read_value("'partition_store.c'::s_have_active")
    valid = session.read_value("'partition_store.c'::s_active.valid")
    sector = session.read_value("'partition_store.c'::s_active.sector")
    addr = session.read_value("'partition_store.c'::s_active.addr")
    version = session.read_value("'partition_store.c'::s_active.header.version")
    s_key = session.read_bytes("'session.c'::s_key", 32)
    s_authorized = session.read_value("'session.c'::s_authorized")
    flash_a = session.read_bytes(hex(PARTITION_A_ADDR), PARTITION_HEADER_LEN)
    flash_b = session.read_bytes(hex(PARTITION_B_ADDR), PARTITION_HEADER_LEN)

    return [
        "  -- persistent state, read fresh just now --",
        f"  g_state                 : {g_state}",
        f"  active partition        : sector {sector} @ {addr} "
        f"(valid={valid}, have_active={have_active}, version={version})",
        f"  s_key (session, RAM)    : {hexstr(s_key)}",
        f"  s_authorized            : {s_authorized}",
        f"  flash A @0x{PARTITION_A_ADDR:08x} (raw): {parse_flash_header(flash_a)}",
        f"  flash B @0x{PARTITION_B_ADDR:08x} (raw): {parse_flash_header(flash_b)}",
    ]
