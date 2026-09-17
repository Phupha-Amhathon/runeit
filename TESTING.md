# Testing RUNEIT on real hardware

This project hasn't run on physical hardware yet — everything so far has
only been compiled/linked and, for the crypto module, unit-tested on the
host. This doc is how to actually exercise it on the NUCLEO-F411RE, and how
to debug it when something doesn't match the expected output.

## 1. Flash the board

Build and flash as described in the [README's "Building"
section](README.md#building), either via STM32CubeIDE's debug/run button
or by flashing the built `.elf`/`.bin` with ST-LINK tooling. Connect the
board's USB (the ST-LINK port, usually labeled `CN1`) to your machine —
this is the same USB connection used for programming, and it also carries
the USART2 signals as a virtual COM port (NUCLEO boards route PA2/PA3 to
the ST-LINK VCP by default, so you don't need a separate USB-serial
adapter or extra wiring).

Find the port name:
- Linux: `ls /dev/ttyACM*` (usually `/dev/ttyACM0`)
- macOS: `ls /dev/tty.usbmodem*`
- Windows: Device Manager → Ports (COM & LPT) → look for "STMicroelectronics
  STLink Virtual COM Port"

Open it at **115200 8N1** in any terminal (`screen /dev/ttyACM0 115200`,
PuTTY, the STM32CubeIDE built-in terminal, etc.) to watch it manually, or
use the automated script below.

## 2. Automated checks — `tools/hw_test.py`

```sh
pip install pyserial   # one-time
python3 tools/hw_test.py /dev/ttyACM0   # or COM5, /dev/tty.usbmodemXXXX, ...
```

**Reset the board (press the black RESET button, or unplug/replug USB)
immediately before running this** — the firmware only prints the
`MODE_SELECTION` menu when it *enters* that state, so the script needs to
observe a fresh boot; it has no "please repeat the prompt" command to fall
back on.

It drives the menu/retrieve protocol end to end (boot → menu → list the
two auto-seeded entries → look up a valid and an invalid id → exit back to
the menu → confirm the unimplemented modes say so) and prints a per-check
`PASS`/`FAIL` line. On failure it prints the actual bytes received, e.g.:

```
[FAIL] retrieve: id 0 shows seeded password 'hunter2'
       expected to contain: 'example.com : hunter2'
       actual response:      '\r\nInvalid id.\r\n'
```

That specific shape of failure (id 0 rejected as invalid) would point at
`partition_store.c`'s CRC/version selection or `password_table.c`'s
`Password_Table_EntryIsUsed()` check, not at the UART link — the actual
response tells you which stage of the pipeline to look at.

## 3. Manual checks

These can't be scripted:

- **Panic button, mid-menu.** At the `MODE_SELECTION` prompt, press the
  button on PA10. Expected: nothing visibly changes (you're already at the
  menu it would return you to) — this mainly checks the board doesn't
  crash/hang on a spurious press.
- **Panic button, mid-listing.** Select `1` to enter `RETRIEVE_MODE` so the
  table listing and id prompt are showing, then press the button. Expected:
  immediately jumps back to `== MODE SELECTION ==`, with no leftover output
  from the id prompt. Then select `1` again — the table should re-decrypt
  and list normally (confirms the wipe cleared RAM but didn't corrupt the
  flash partition).
- **Panic button, mid id-prompt.** Same as above but press while the
  `Enter id to view` prompt is waiting for input rather than right after
  the listing — same expected result.
- **Power-cycle persistence.** After `tools/hw_test.py` (or any manual
  session) has run at least once, power-cycle the board (unplug/replug, or
  RESET) and watch the boot output *without* sending anything: it should
  go straight to `== MODE SELECTION ==` without any noticeably longer
  pause (no flash erase happening this time), and selecting `1` should
  list the *same* two entries as before. This specifically exercises
  `partition_store.c`'s header/CRC/version selection logic — on the very
  first boot ever it auto-seeds partition A from scratch, but every boot
  after that should find that same partition still valid and skip
  re-seeding. If the table looks re-seeded (e.g. reset to defaults) or the
  board goes back into a long pause, that logic has a bug worth revisiting
  before Stage C builds on it.

## What this doesn't cover

- Any of `FIRST_MEET`/`MK_AUTH` — not implemented yet (see README "Status").
- `GENERATE_MODE`/`CHANGE_MK_MODE` beyond confirming they say "not
  implemented yet".
- ADC — init-only, nothing to observe yet.
