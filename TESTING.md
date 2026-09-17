# Testing RUNEIT on real hardware

This project hasn't run on physical hardware yet — everything so far has
only been compiled/linked and, for the crypto module, unit-tested on the
host. This doc is how to actually exercise it on the NUCLEO-F411RE, in
priority order, and how to debug it when something doesn't match.

The first three checks specifically need the **debugger**, not just the
serial link — the serial protocol only ever shows you decrypted data and
"the menu reappeared," which isn't enough to prove flash is actually
encrypted at rest, that the A/B partition logic is really doing what it
claims, or that RAM was genuinely zeroed rather than just "forgotten."

## 0. Flash the board (prerequisite)

Build and flash as described in the [README's "Building"
section](README.md#building), either via STM32CubeIDE's debug/run button
or by flashing the built `.elf`/`.bin` with ST-LINK tooling. Connect the
board's USB (the ST-LINK port, usually labeled `CN1`) — same connection
used for programming, and it also carries USART2 as a virtual COM port
(NUCLEO boards route PA2/PA3 to the ST-LINK VCP by default, no extra
wiring needed).

Find the serial port name:
- Linux: `ls /dev/ttyACM*` (usually `/dev/ttyACM0`)
- macOS: `ls /dev/tty.usbmodem*`
- Windows: Device Manager → Ports (COM & LPT) → "STMicroelectronics STLink
  Virtual COM Port"

Open it at **115200 8N1** in any terminal (`screen /dev/ttyACM0 115200`,
PuTTY, STM32CubeIDE's built-in terminal, etc.). Confirm the
`== MODE SELECTION ==` menu shows up on boot before going further — no
point debugging deeper checks if this fails.

## Getting a `gdb` prompt on the board

Checks 1–3 below run through `arm-none-eabi-gdb` attached to the target.
Two ways to get that:

- **Easiest — STM32CubeIDE's own debugger.** Debug As → STM32 C/C++
  Application; it already knows how to start the right GDB server for
  your ST-LINK. Once the Debug perspective is up, open the **Debugger
  Console** view (Window → Show View → Debugger Console, or it may already
  be docked) — this accepts raw `gdb` commands, including `source`.
- **Advanced — command line.** The toolchain bundles both an ST-LINK GDB
  server and OpenOCD (under
  `.../stm32cube.ide.mcu.externaltools.stlink-gdb-server.*/tools/bin/ST-LINK_gdbserver`
  and `.../stm32cube.ide.mcu.externaltools.openocd.*/tools/bin/openocd`).
  Exact flags depend on your ST-LINK/CubeProgrammer install (run
  `ST-LINK_gdbserver -h` to see them), so if this doesn't connect cleanly,
  fall back to the STM32CubeIDE path above rather than fighting flags.
  Once a server is running on some port, e.g.:
  ```sh
  arm-none-eabi-gdb runeit.elf -ex "target remote localhost:61234"
  ```

Either way, halt the target (`monitor reset halt`, or just hit the pause
button in the IDE) before running the checks below.

## 1–3. Debugger checks — `tools/hw_debug_checks.gdb`

```
(gdb) source tools/hw_debug_checks.gdb
```

This is **not** a single unattended run — read the echoed instructions as
you go. A couple of steps depend on something you do *on the serial
terminal* (which `gdb` can't see or wait for), and one step
(`Section 3`) deliberately blocks in `gdb` until you physically press the
PA10 button. The sections, in order:

- **Section 1a/1b — flash-at-rest encryption** (user priority #1). 1a
  searches the partition flash region for the known seed plaintext
  (`"example.com"`, `"hunter2"`, etc.) — every result should be *"Pattern
  not found."* If any of them **are** found, the table is being written
  to flash un-encrypted; look at `partition_store.c`'s `Commit()`/seed
  path. 1b is the positive control proving that absence is meaningful: the
  same search over the RAM copy (`s_table`, once you've sent `1` on the
  serial terminal to enter `RETRIEVE_MODE`) **should** find a match.
- **Section 2 — partition A/B commit + reload after reset** (user
  priority #2). There's no `TOGGLE_PARTITION` UI yet, so instead of one,
  this calls the real `Partition_Store_Commit()` on the halted target —
  the actual flash erase/program path, not a simulation. It mutates the
  first entry's name, commits, and shows `s_active.sector`/`.version`
  flipping. Then it resets the board for real and you confirm *over the
  serial link* (`1`, then `0`) that the mutated entry is what comes back
  — proving both the write-to-the-inactive-sector logic and that
  `Partition_Store_Init()` correctly re-selects it after an actual reset,
  not just inside the live debugger call.
- **Section 2b — CRC fault injection** (added: cheap extension of the
  same idea). Corrupts the *active* partition's stored CRC and resets,
  expecting the firmware to detect the mismatch and fall back to the
  other, still-valid (one version older) partition instead of trusting
  corrupted data or crashing — this is what actually validates the
  self-describing header/CRC design, not just the happy path. Depends on
  your GDB server passing plain memory writes through to flash; the script
  tells you how to check, and it's fine to skip if it doesn't.
- **Section 3 — panic-button RAM wipe** (user priority #3). With
  `RETRIEVE_MODE` active and `s_table` holding plaintext, this sets a
  breakpoint on `Mode_Retrieve_Wipe()` and blocks — press the physical
  button, `gdb` stops right after the wipe runs, and the script prints
  `s_table` (every byte should be zero) plus re-runs the same `find` from
  1b (should now say "not found").

If a check fails, the printed value tells you which module to look at —
e.g. Section 1a finding plaintext points at `partition_store.c`; Section 3
showing non-zero bytes after the breakpoint points at `mode_retrieve.c`'s
`Mode_Retrieve_Wipe()` or `app.c`'s `App_PanicHandler()` not calling it.

## 4. Full protocol regression — `tools/hw_test.py`

Once 1–3 pass individually, run this as a broader regression pass:

```sh
pip install pyserial   # one-time
python3 tools/hw_test.py /dev/ttyACM0   # or COM5, /dev/tty.usbmodemXXXX, ...
```

**Reset the board immediately before running this** — the firmware only
prints the `MODE_SELECTION` menu when it *enters* that state, so the
script needs to observe a fresh boot. It drives the menu/retrieve protocol
end to end and prints a per-check `PASS`/`FAIL` line with the actual bytes
received on failure, e.g.:

```
[FAIL] retrieve: id 0 shows seeded password 'hunter2'
       expected to contain: 'example.com : hunter2'
       actual response:      '\r\nInvalid id.\r\n'
```

If you ran the debugger checks above first, entries may now read
`TOGGLED-A` instead of `example.com` — re-flash (or re-run Section 2 in
reverse) if you want `hw_test.py`'s original assertions to pass again.

## 5. Manual UX checks

Quick black-box sanity passes that complement 2/3 above:

- **Panic button, mid-menu.** At `MODE_SELECTION`, press the button.
  Expected: nothing visibly changes — checks it doesn't crash/hang on a
  spurious press.
- **Panic button, mid id-prompt.** Enter `RETRIEVE_MODE`, wait at the
  `Enter id to view` prompt, press the button. Expected: immediately jumps
  back to `== MODE SELECTION ==`. Select `1` again — the table should
  re-decrypt and list normally (confirms the wipe cleared RAM but didn't
  corrupt the flash partition).
- **Power-cycle persistence.** Power-cycle the board and watch the boot
  output without sending anything: should go straight to
  `== MODE SELECTION ==` with no extra pause (no flash erase happening
  this time), and `1` should list the same entries as before the cycle.

## What this doesn't cover

- Any of `FIRST_MEET`/`MK_AUTH` — not implemented yet (see README "Status").
- `GENERATE_MODE`/`CHANGE_MK_MODE` beyond confirming they say "not
  implemented yet".
- ADC — init-only, nothing to observe yet.
