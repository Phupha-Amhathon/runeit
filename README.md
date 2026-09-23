# RUNEIT — Pocket Password

A bare-metal password manager for the STM32F411RE (NUCLEO-F411RE): a master
key (MK) authorizes access to an encrypted table of service/password
entries, stored redundantly across two flash partitions so updates can be
applied transactionally. Full design background is in
[`information/RUNEIT_V1.pdf`](information/RUNEIT_V1.pdf); the class
requirements are in [`information/image.png`](information/image.png).

This is a class project (1-month timebox). Register-level CMSIS only — no
STM32 HAL — per the course constraint, and the code is split into
`Application` and `Driver` layers as a graded requirement.

## Status

| Area | State |
|---|---|
| `INIT` → `MODE_SELECTION` → `RETRIEVE_MODE` | **Working**, reachable over USART2 |
| `FIRST_MEET_*` / `MK_AUTH_*` (real MK entry + verification) | **Not implemented yet.** `INIT` currently auto-provisions a hardcoded placeholder `input_mk` (see `s_hardcoded_mk` in `Src/app/app.c`) so `RETRIEVE_MODE` could be built and tested first |
| `GENERATE_MODE` | Stub only — undesigned in RUNEIT_V1 ("to be continued") |
| `CHANGE_MK_MODE` | Stub only — needs real auth (above) first |
| Panic button (PA10, EXTI) | **Working** — wipes the decrypted table in RAM and returns to `MODE_SELECTION` immediately from the ISR, at the highest interrupt priority in the system |
| ADC | Peripheral clock/pin initialized only; no sampling yet (lands with `GENERATE_MODE`) |
| Crypto | Placeholder: software SHA-256 for hashing, and a SHA-256-expanded XOR keystream cipher for the table. **Not real security** — an explicit "swap for AES if time remains" TODO, not a finished design |

None of this has been run on physical hardware yet — see [`TESTING.md`](TESTING.md).

## Architecture

```
Inc/  drivers/   usart_drv.h  flash_drv.h  crc_drv.h  exti_drv.h  systick_drv.h  adc_drv.h
      crypto/    sha256.h  xor_cipher.h
      app/       app.h  app_types.h  partition_store.h  password_table.h  mode_retrieve.h
Src/  drivers/   (implementations, mirrors Inc/drivers/)
      crypto/    (implementations, mirrors Inc/crypto/)
      app/       (implementations, mirrors Inc/app/)
      main.c     syscalls.c  sysmem.c
```

- **`drivers/`** — register-level CMSIS wrappers only, no business logic:
  - `systick_drv` — 1 ms tick used for delays and button debounce.
  - `exti_drv` — PA10 "panic" button. Falling-edge EXTI at the **highest
    NVIC priority** in the system, so it always preempts USART/DMA
    activity. The ISR calls a registered callback immediately (the app
    layer wires this to wipe RAM and reset state) — a software debounce
    guard only stops one physical press from re-triggering the callback
    multiple times, it never delays the wipe itself.
  - `usart_drv` — USART2 (PA2=TX, PA3=RX), 115200 8N1, **fully
    interrupt/DMA-driven, no register polling**: TX uses DMA1 Stream6, RX
    uses DMA1 Stream5 into a line buffer, and a line boundary is detected
    by the USART **IDLE-line interrupt** rather than a software timeout
    timer (matching the course's "UART, no timer" material). Callers get a
    simple line-based API (`USART_Drv_Send`, `USART_Drv_TakeLine`, ready
    flags) without touching any register directly.
  - `flash_drv` — unlock/erase-sector/write/read primitives, parameterized
    by sector and address (used for both partitions).
  - `crc_drv` — thin wrapper over the F411's hardware CRC-32 unit
    (fixed polynomial `0x04C11DB7`), with a streaming
    `Reset`/`Feed`/`Result` API so a CRC can be accumulated over more than
    one buffer (header fields + table) without re-assembling them
    contiguously in RAM first.
  - `adc_drv` — clock/pin init only for now; see Status above.

- **`crypto/`** — pure algorithms, no hardware or app-state dependency:
  - `sha256` — self-contained SHA-256 (FIPS 180-4), verified in this
    session against the three NIST test vectors on the host before ever
    running on-device.
  - `xor_cipher` — a keystream cipher: block *i* of the keystream is
    `SHA256(key ‖ nonce ‖ i)`, XORed into the buffer. The same call both
    encrypts and decrypts. This is the lightweight option from the
    project's open "research crypto" item — swappable for AES later
    without touching any caller, since callers only rely on it being
    deterministic and symmetric.

- **`app/`** — the state machine and password-table logic; only calls into
  `drivers/` and `crypto/`, never touches a register directly:
  - `partition_store` — the A/B flash layout and its read/select/commit
    logic. Each partition (sector 2 = A, sector 3 = B) starts with a
    self-describing header (`magic`, `version`, `hash_mk`, `crc32`)
    immediately followed by the encrypted table. On `INIT`, whichever
    partition has a valid magic + CRC and the *higher* version number is
    authoritative — there is no RAM-only "active partition" pointer to
    lose on reset. `Partition_Store_Commit()` always writes to the
    *inactive* sector with `version + 1`, so a power loss mid-write
    leaves the previously-committed partition intact.
  - `password_table` — the in-RAM table layout (31 entries, id `0..30`)
    and the USART text formatting for listing/showing entries.
  - `mode_retrieve` — the `RETRIEVE_MODE` sub-state machine
    (`RETRIEVE_PWD_MODE` → `SHOW_TABLE_ENTRIES` → `PWD_ID_SELECTION` →
    `SHOW_PWD_ENTRIES`, looping until the user sends `q`).
  - `app` — the top-level state machine (`APP_STATE_*` in `app_types.h`)
    tying everything together, plus the panic-button callback.

- **`main.c`** — initializes every driver, then calls `App_Init()` +
  `App_Run()` (never returns).

## Flash memory map

| Region | Address range | Size | Contents |
|---|---|---|---|
| Sectors 0–1 | `0x08000000`–`0x08007FFF` | 32 KB | Firmware code. The linker script (`STM32F411RETX_FLASH.ld`) caps the `FLASH` region here on purpose — a build that grows past 32 KB **fails to link** instead of silently letting code overwrite partition data at runtime. |
| Sector 2 (Partition A) | `0x08008000`–`0x0800BFFF` | 16 KB | `partition_header_t` + encrypted password table |
| Sector 3 (Partition B) | `0x0800C000`–`0x0800FFFF` | 16 KB | Same layout, the other slot of the A/B pair |

## Serial protocol

USART2, 115200 8N1, line-based (send a line ending in Enter; the terminal's
CR/LF is stripped by the driver).

```
== MODE SELECTION ==
  1) Retrieve password
  2) Generate password (not implemented yet)
  3) Change master key (not implemented yet)
Select: 1

-- Password table --
   0 - example.com
   1 - email

Enter id to view (0-30), or 'q' to go back: 0

example.com : hunter2

Enter id to view (0-30), or 'q' to go back: q

== MODE SELECTION ==
...
```

Pressing the PA10 button at any point immediately wipes the decrypted table
from RAM and returns to `MODE_SELECTION`.

## Building

**STM32CubeIDE (GUI):** open the existing project and build as normal — the
`Inc/{app,drivers,crypto}` and `Src/{app,drivers,crypto}` folders and their
include paths are already registered in `.cproject`. If the IDE doesn't
pick up files added outside it, right-click the project → *Refresh* (F5),
then clean/rebuild.

**Command line**, using the toolchain bundled with STM32CubeIDE (adjust the
`GCC_BIN`/`CMSIS_*` paths to match your STM32CubeIDE install and Library
workspace):

```sh
GCC_BIN=/opt/st/stm32cubeide_2.2.0/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/tools/bin
CMSIS_CORE=<workspace>/Library/CMSIS/Core/Include
CMSIS_DEV=<workspace>/Library/CMSIS-DEVICE-F4/Include

$GCC_BIN/arm-none-eabi-gcc \
  -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard \
  -I Inc -I Inc/app -I Inc/drivers -I Inc/crypto -I "$CMSIS_CORE" -I "$CMSIS_DEV" \
  -Wall -Wextra -O1 -g -std=gnu11 -ffunction-sections -fdata-sections \
  $(find Src -name "*.c") Startup/startup_stm32f411retx.s \
  -T STM32F411RETX_FLASH.ld -Wl,--gc-sections -specs=nano.specs \
  -o runeit.elf
```

Flash `runeit.elf` with ST-LINK (via STM32CubeIDE's debugger/programmer, or
`st-flash`/`STM32_Programmer_CLI` if you have those installed).

## Known limitations

- `input_mk` is a hardcoded placeholder (`Src/app/app.c`) until Stage C
  (`FIRST_MEET`/`MK_AUTH`) is implemented — **do not** treat the current
  build as representing real access control.
- The XOR keystream cipher is a placeholder, not a vetted encryption
  scheme; swapping it for AES is an open, explicitly deferred item.
- `GENERATE_MODE` is unspecified in the source design (RUNEIT_V1.pdf marks
  it "to be continued") and is out of scope until that design exists.

## Testing

See [`TESTING.md`](TESTING.md): a bottom-up, command-by-command gdb guide for
the real board (flash, crypto, A/B partition and encryption at rest, UART/DMA,
application screens with full mock tables, panic-button RAM wipe), with the
expected result and the fix for every failure. `tools/mock/` holds the mock
tables it loads, and `tools/hw_test.py` is an optional end-to-end serial smoke
test.
