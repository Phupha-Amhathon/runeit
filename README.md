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
| `INIT` → `FIRST_MEET` / `MK_AUTH` → `MODE_SELECTION` | **Implemented (Stage C).** A blank device asks for a master key twice; every later boot asks for it before anything else. Hardcoded key removed |
| `RETRIEVE_MODE` | Working; needs the open session, strict id parsing |
| `CHANGE_MK_MODE` | Implemented: re-encrypts the table with a fresh salt and writes header + table to the *other* partition in one verified commit |
| `GENERATE_MODE` | Menu stub only — being built on a separate branch (with the RNG). It plugs in through `Session_IsAuthorized()`, `Session_LoadTable()` and `Session_Save()` (`Inc/app/session.h`) |
| Panic button (PA10, EXTI) | **Working** — from the ISR: wipes the session key, the decrypted table, every buffer that held a key or a formatted password, and the UART receive buffer, then returns to the login screen. Highest interrupt priority in the system |
| ADC | Peripheral clock/pin initialized only; no sampling yet (lands with `GENERATE_MODE`) |
| Crypto | SHA-256, HMAC-SHA256, PBKDF2 key stretching and a SHA-256-keystream XOR cipher for the table. Verified against RFC/NIST vectors on a PC. The cipher is still the lightweight placeholder — "swap for AES if time remains" |

Stage B (data path) was tested on the board; Stage C is verified by the PC
suite in `tests/host/` and has **not yet been run on the board** — see
[`TESTING.md`](TESTING.md) (written for Stage B, see the note at its top).

## Architecture

```
Inc/  drivers/   usart_drv.h  flash_drv.h  crc_drv.h  exti_drv.h  systick_drv.h  adc_drv.h  uid_drv.h  critical_drv.h
      crypto/    sha256.h  hmac_sha256.h  kdf.h  xor_cipher.h  secure_zero.h
      app/       app.h  app_types.h  session.h  partition_store.h  password_table.h
                 mode_first_meet.h  mode_mk_auth.h  mode_retrieve.h  mode_change_mk.h
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
  - `uid_drv` — reads the 96-bit factory unique ID (used to make the salt
    unique per device).
  - `critical_drv` — interrupts off/on with the previous state saved; used
    to publish a new session atomically against the panic button.

- **`crypto/`** — pure algorithms, no hardware or app-state dependency:
  - `sha256` — self-contained SHA-256 (FIPS 180-4), verified in this
    session against the three NIST test vectors on the host before ever
    running on-device.
  - `hmac_sha256`, `kdf` — HMAC-SHA256 and PBKDF2 (`Kdf_DeriveKeys`). The
    master key is stretched into `K` (`KDF_ITERATIONS` rounds, seconds on
    this MCU), then `auth = HMAC(K,"RUNEIT-auth-v1")` is stored and
    `enc = HMAC(K,"RUNEIT-enc-v1")` is the cipher key and is **never
    stored**. Because the cipher key comes out of the same slow derivation,
    guessing through the stored hash or through the ciphertext costs the
    same. See the note in `kdf.h` on choosing the iteration count.
  - `secure_zero` — `Secure_Zero()`, a memset that the compiler may not
    optimise away; used on every buffer that held a key.
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
    self-describing 64-byte header (`magic`, `version`, `kdf_iter`,
    `salt[16]`, `auth[32]`, `crc32`) immediately followed by the encrypted
    table. On `INIT`, whichever
    partition has a valid magic + CRC and the *higher* version number is
    authoritative — there is no RAM-only "active partition" pointer to
    lose on reset. `Partition_Store_Commit()` always writes to the
    *inactive* sector with `version + 1`, so a power loss mid-write
    leaves the previously-committed partition intact, and it reads back
    and validates what it wrote before switching (returns `false`
    otherwise). This is also what "toggle partition" means: every commit
    (new key, saved entry) lands in the other sector.
  - `password_table` — the in-RAM table layout (31 entries, id `0..30`)
    and the USART text formatting for listing/showing entries.
  - `session` — the authorized session: the derived cipher key in RAM, the
    only place that can open it (`Session_Authenticate` for the login,
    `Session_SetNewKey` for first setup / key change). A *generation
    counter* is bumped by the panic button, so a derivation that was in
    flight when the button was pressed can never publish its result
    afterwards. Master-key rule: 8–31 printable ASCII characters.
  - `mode_first_meet`, `mode_mk_auth`, `mode_retrieve`, `mode_change_mk` —
    one sub-state machine per mode of the design document.
  - `app` — the top-level state machine (`APP_STATE_*` in `app_types.h`).
    Every state after the login screens is guarded: if the session is gone
    the machine goes back to `INIT`, which chooses `FIRST_MEET` or `MK_AUTH`.
    Also holds the panic-button callback.

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
CR/LF is stripped by the driver). **A line ends only when an Enter arrives**
(CR, LF or CR+LF — set your terminal's line ending to one of them; a terminal
that sends text with no line ending will appear to hang). Typing one key at a
time works and Backspace edits the line; the board does not echo, so turn on
your terminal's local echo if you want to see what you type (a master key is
then visible on screen — there is no way to hide it over a plain serial link). Each key derivation prints its duration, e.g. `(4010 ms)`.

```
== FIRST TIME SETUP ==                 (only when no partition exists)
Choose a master key (8-31 printable characters).
Master key: ********
Confirm master key: ********
Master key set (4010 ms).

== LOCKED ==                           (every later boot, and after the panic button)
Master key: ********
Access granted (4012 ms).              (or: Wrong master key (4009 ms). -- unlimited tries,
                                         the slow derivation is the throttle)
== MODE SELECTION ==
  1) Retrieve password
  2) Generate password (not implemented yet)
  3) Change master key
Select: 1

-- Password table --
   0 - example.com
Enter id to view (0-30), or 'q' to go back: 0
example.com : hunter2
```

Menu choices must be exactly `1`, `2` or `3`; ids exactly one or two digits.
An empty line at the "New master key" prompt cancels.

Pressing the PA10 button at any point destroys the session and every secret
in RAM and returns to the login screen (`FIRST_MEET` if the device has no
data).

## PC tests

`tests/host/run.sh` (needs only `gcc` and `bash`) runs the crypto vectors
and a simulation of the whole application — the real `app.c`, `session.c`,
modes, `partition_store.c` and crypto — against software models of the flash,
CRC and UART: first setup, wrong/right key, change key, failed flash write,
panic during and just before publishing a session, RAM scans after the panic
button. Run it after every change, and after merging the `GENERATE_MODE`
branch.

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

- A blank device with **no** flash data offers `FIRST_MEET` to whoever
  reaches it first; the first person to connect chooses the key. That is
  inherent to a device that has no key yet.
- The salt is built from the unique device ID and a tick counter, not from a
  random source; it only has to be unique. Replace it with the RNG when the
  generator branch lands.
- Stretching slows each guess but cannot save a short or common master key
  from someone who copies the flash and guesses on a PC; use a long
  passphrase. Raising `KDF_ITERATIONS` (or the CPU clock) raises the cost.
- The XOR keystream cipher is a placeholder, not a vetted encryption
  scheme; swapping it for AES is an open, explicitly deferred item.
- Secrets can remain in the CPU stack/registers after use; only named
  buffers are wiped (and checked in the PC tests).
- An RX line typed while the CPU is stalled by a flash write is lost (do not
  type during "Re-encrypting" / "Deriving").
- `GENERATE_MODE` and RNG are on a separate branch; expect a small merge
  conflict in `app.c` (menu text and the `GENERATE_MODE` case).

## Testing

See [`TESTING.md`](TESTING.md) (Part 1 = Stage B data path, Part 2 = Stage C: login, key change, integrity, panic in every mode; the note at its top says what is current): a bottom-up, command-by-command gdb guide for
the real board (flash, crypto, A/B partition and encryption at rest, UART/DMA,
application screens with full mock tables, panic-button RAM wipe), with the
expected result and the fix for every failure. `tools/mock/` holds the mock
tables it loads, and `tools/hw_test.py` is an optional end-to-end serial smoke
test.
