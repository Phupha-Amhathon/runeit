# RUNEIT — hardware test guide (bottom-up, command by command)

Everything you need is in this file: every section says **what to type**,
**why** the test exists, **what you should see**, and **what it means and what to
fix** when you see something else. You never have to open a script to find
instructions. The only files used besides this one are the binary mock tables
in `tools/mock/` (loaded with a single `restore` command).

The tests go **bottom-up**, so a failure in a layer is never caused by a layer
below it that you already verified:

| Layer | What | Depends on |
|---|---|---|
| S | Setup, baseline B0, "is the flashed image really my build?" | — |
| L1 | Clocks, pins, DMA, NVIC, EXTI register configuration | — |
| L2 | Hardware CRC (`crc_drv`) | L1 |
| L3 | Flash program/erase/read (`flash_drv`) | L1, L2 |
| L4 | SHA-256 and XOR cipher (`crypto/`) | L2 |
| L5 | A/B partition store: encryption at rest, toggle, corruption, fallback | L2–L4 |
| L6 | USART2 + DMA driver (`usart_drv`) | L1 |
| L7 | Password table + RETRIEVE_MODE screens with full mock data | L5, L6 |
| L8 | Application state machine, boot/seed, persistence | L5–L7 |
| L9 | Panic button and RAM wipe | L1, L7 |
| L10 | End-to-end regression + progress report | all |

Your three priorities map to: **encryption at rest → T5.2/T5.3**, **partition
toggle → T5.5/T5.6 (+T8.x)**, **RAM wiped by panic → T9.3**.

## How the expected values were produced (and how far to trust them)

The expected numbers for L2–L5, L7 and L8 come from running the **real
firmware source files** (`partition_store.c`, `app.c`, `mode_retrieve.c`,
`password_table.c`, `sha256.c`, `xor_cipher.c`) on a PC with the hardware drivers
replaced by software models; the CRC model was checked against the well-known
STM32 hardware vector (`0x00000000 → 0xC704DD7B`). So **if the board disagrees, the
cause is the hardware, a driver, the configuration or the flashed binary — not
a typo in this document.** Items marked **PREDICTED** are conclusions from
reading the code that no simulation covered; treat those as hypotheses the
test is meant to confirm or refute. Items marked **KNOWN GAP** are behaviours
the code has today that differ from what a finished product should do; the test
exists to show them and the "What next" tells you the fix.

Nothing here has been run on a physical board yet.

## Conventions and pitfalls (read once)

* Commands are typed at the `(gdb)` prompt. Lines starting with `#` are
  comments — don't type them. `'file.c'::name` is required for `static`
  variables (gdb cannot find a file-static by bare name).
* **Symbols may not exist.** The linker drops functions nobody calls
  (`--gc-sections`), so `CRC_Drv_Compute` and `USART_Drv_TxReady` are *not* in
  the image and cannot be called from gdb — this guide uses the ones that are.
  If gdb says `No symbol "X" in current context` for something else, you are
  probably running an old/optimised build; do S3 first.
* Use the **Debug** configuration (`-O0`). With optimisation, small functions get
  inlined and `static` names disappear.
* `find` with a quoted string **includes the trailing NUL**, so `find …, "pw07"`
  will *not* match `pw07!@#…`. This guide always searches with byte lists
  (`'p','w','0','7'`), which match anywhere.
* While the CPU is **halted**, no interrupt handler runs (DMA keeps working).
  Several UART tests rely on this and call the ISRs by hand.
* **Flash erase/program stalls the CPU** for hundreds of ms (the code runs from
  the same flash bank). Don't type into the terminal during a commit — bytes
  are lost. `gdb` just waits for the `call` to return.
* L3, L5 and parts of L7–L9 are **destructive** to sectors 2–3 (the partitions).
  Never call `Flash_Drv_EraseSector(0)` or `(1)` — that erases the firmware.
* **Cleanup** after destructive layers: run
  `call (void)Flash_Drv_EraseSector(2)`, `call (void)Flash_Drv_EraseSector(3)`,
  then reset — the next boot re-seeds the default 2-entry table.

## Reference values

**Hardware CRC** (init `0xFFFFFFFF`, poly `0x04C11DB7`, 32-bit words, no reflection):

| Input | CRC |
|---|---|
| one word `0x00000000` | `0xC704DD7B` |
| `seq64[0:4]` | `0x76DBF7C7` |
| `seq64[0:16]` | `0x081B46CA` |
| `seq64[0:64]` | `0x1125C90E` |
| `seq64[0:3]` (tail zero-padded to a word) | `0x16AC45A9` |
| zero bytes | `0xFFFFFFFF` |
| 16 KB of `0xFF` (a blank sector) | `0x34132F69` |
| 1488 zero bytes (an empty table) | `0xBB034F8E` |
| `table_full.bin` | `0x60085D29` |
| `table_sparse.bin` | `0x26F4D326` |
| `table_holes.bin` | `0x7E5E5310` |
| `table_unterm.bin` | `0xCB09012D` |

`seq64.bin` is the bytes `00 01 02 … 3F`.

**SHA-256** of `seq64[0:N]`:

| N | digest |
|---|---|
| 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 1 | `6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d` |
| 3 | `ae4b3280e56e2faf83f414a6e3dabe9d5fbe18976544c05fed121accb85b53fc` |
| 55 | `463eb28e72f82e0a96c0a4cc53690c571281131f672aa229e0d45ae59b598b59` |
| 56 | `da2ae4d6b36748f2a318f23e7ab1dfdf45acdc9d049bd80e59de82a60895f562` |
| 63 | `29af2686fd53374a36b0846694cc342177e428d1647515f078784d69cdb9e488` |
| 64 | `fdeab9acf3710362bd2658cdc9a29e8f9c757fcf9811603a8c447cd1d9151108` |

`SHA-256("TempMasterKey1")` (= `hash_mk`) =
`783359db1fe6fba9bafa895df9b43184e8923774afb95103ed08cd75a9aa2308`, i.e. as
little-endian words `0xdb593378 0xa9fbe61f 0x5d89faba 0x8431b4f9 0x743792e8 0x0351b9af 0x75cd08ed 0x0823aaa9`.

**XOR cipher**, plaintext `seq64[0:48]`, key `TempMasterKey1`:

```
nonce 1: 09b35edbbc8418d115511f207e3245be caabf1fa1cdcdb39743b89395b3a17f8 d53427903fab91cdd705c4dacacb02ce
nonce 2: ff5eea93912c07c161a01d2c6320cd1b 1192ccdc1d72cb857fdab2ed69d50d5d 788cfbaea6e058f83789d66f9f8ed4eb
key "TempMasterKey" (13 bytes), nonce 1, first 16 bytes: d83163996f3500736593fb023a47233b
```

**Partition, `table_full.bin` committed as first commit (version 1, sector 2):**

| Item | Value |
|---|---|
| header words `x/11xw 0x08008000` | `52554e31 00000001` + the 8 `hash_mk` words above + `43e8c184` |
| ciphertext bytes 0–15 at `0x0800802C` | `5a826cb9dae27ab37b3f7d42185427b1` |
| ciphertext bytes 1472–1487 at `0x080085EC` | `e85295108f3a4df21f607c3f7589d3d8` |
| CRC of the 1488 ciphertext bytes | `0x29562DC9` |
| CRC of whole sector 2 / sector 3 | `0xA199C8F9` / `0x34132F69` (blank) |
| commit #2 (entry 0 name changed to `Z00…`, → sector 3, v2) header crc32 | `0xD70A1EC5`; sector 3 CRC `0x681F2BE5`; table CRC `0x1D72CCE5` |
| commit #3 (→ sector 2, v3) header crc32 | `0x99CA4AB9`; sector 2 CRC `0xEA423917` |
| load with wrong key `TempMasterKey` (13 B) → table CRC | `0xAD1D50B6` (garbage) |
| default seed table (blank-flash boot) header crc32 | `0x81EB34E2` |

**Mock tables** (`tools/mock/`, 31 entries × `name[16]` + `password[32]` = 1488 bytes, NUL-padded):

| File | Content |
|---|---|
| `table_full.bin` | all 31 entries full-length: name `S<id>abcdefghijkl` (15 chars, the maximum), password `pw<id>!@#$%^&*()-_=+[]{};:,.<>/?a` (31 chars, the maximum). e.g. id 7 → `S07abcdefghijkl` / `pw07!@#$%^&*()-_=+[]{};:,.<>/?a` |
| `table_sparse.bin` | only ids 0, 15, 30 (same content as full) — gaps and both boundary ids |
| `table_empty.bin` | all zero |
| `table_holes.bin` | id 3: empty name but password `orphan-password`; id 4: name `nopass`, empty password; id 5: `normal`/`pass5` |
| `table_unterm.bin` | id 0: name is 16 × `X` with **no NUL**, password `secretpw` |
| `seq64.bin` | bytes `00…3F` |

(They are regenerated by `tools/mock/gen_mock.py`; you never need to run it.)

---

# S — Setup

## S1. Connect and load symbols

**Steps**
```
arm-none-eabi-gdb Debug/runeit.elf
(gdb) target remote localhost:61234        # or: start an STM32CubeIDE Debug session and use its Debugger Console
(gdb) cd /home/scenario001/STM32CubeIDE/workspace_2.2.0/runeit
```
The `cd` makes the relative `tools/mock/…` paths below work. Open the serial
terminal at **115200 8N1, no flow control** on the ST-LINK virtual COM port.

**Why** All later checks read/write real memory by symbol name; without the ELF
matching the flashed image, addresses are wrong.

**Expect** `Remote debugging using localhost:61234`, no errors. `print sizeof('mode_retrieve.c'::s_table)` → `1488`.

**If not**
* `Connection refused` → the GDB server isn't running (start the Debug session / `ST-LINK_gdbserver -p 61234`).
* `No symbol table` / `sizeof` fails → you loaded the wrong ELF; use `Debug/runeit.elf`.

## S2. Baseline B0 and helper variables

**Steps**
```
(gdb) tbreak App_Run
(gdb) monitor reset          # OpenOCD: monitor reset halt
(gdb) continue
```
```
set $buf  = (unsigned char*)'password_table.c'::s_line_buf
set $buf2 = (unsigned char*)'partition_store.c'::s_commit_scratch
set $tbl  = (unsigned char*)&'mode_retrieve.c'::s_table
set $h    = $buf + 128
set $z    = $buf + 512
set $mk   = (unsigned char*)'app.c'::s_hardcoded_mk
set $mkl  = sizeof('app.c'::s_hardcoded_mk) - 1
```

**Why** B0 = "stopped at the entry of `App_Run`": every driver `*_Init()` has run,
but `HandleInit()`, the menu and the RX consumer have **not**. Nothing else is
touching UART, flash or the tables, so the layers can be tested in isolation.
`$buf` (1024 B), `$buf2` (1488 B) and `$tbl` (1488 B) are ordinary RAM buffers used as scratch space.
Do not use `$buf` for more than 1024 bytes.

**Expect** `Temporary breakpoint 1, App_Run () at Src/app/app.c:112`. `print $mkl` → `14`.

**If not** it never stops → reset did not happen; press RESET on the board or use the IDE *Restart* button, then `continue`.
The `tbreak` is one-shot; every time a section says "return to B0" repeat the three lines.

## S3. Is the flashed image really my latest build?

**Steps** `compare-sections`

**Why** Reflashing only rewrites sectors 0–1 (code). A stale image silently
runs old code — a common cause of "the fix didn't work".

**Expect** every line ends in `matched.` (`.isr_vector`, `.text`, `.rodata`, `.data`).

**If not** any `MIS-MATCHED!` → rebuild (Project → Clean, Build) and reflash, then redo S2.
This check says nothing about the *data* in sectors 2–3; those survive reflashing (see L5/L8).

---

# L1 — Peripheral configuration (registers)

**Why** Every layer above needs clocks, pins, DMA and interrupts configured. This
is the fastest place to spot "driver never ran" or "wrong bit". Start from **B0**.

**Steps** (all read-only; each prints one number)
```
print/x *(unsigned int*)0x40023830 & 0x00201001      # RCC_AHB1ENR: GPIOA, CRC, DMA1
print/x *(unsigned int*)0x40023840 & 0x00020000      # RCC_APB1ENR: USART2
print/x *(unsigned int*)0x40023844 & 0x00004100      # RCC_APB2ENR: SYSCFG, ADC1
print/x *(unsigned int*)0x40020000 & 0xFF            # GPIOA_MODER PA0..PA3
print/x (*(unsigned int*)0x40020000 >> 20) & 3       # PA10 mode
print/x (*(unsigned int*)0x4002000C >> 20) & 3       # PA10 pull
print/x (*(unsigned int*)0x40020020 >> 8) & 0xFF     # AFRL PA2,PA3 alternate function
print/x (*(unsigned int*)0x40020010 >> 10) & 1       # PA10 level (button released)
print/x *(unsigned int*)0x40004408                   # USART2_BRR
print/x *(unsigned int*)0x4000440C                   # USART2_CR1
print/x *(unsigned int*)0x40004414                   # USART2_CR3
print/x *(unsigned int*)0x40026088                   # DMA1 stream5 CR (RX)
print/x *(unsigned int*)0x4002608C                   # DMA1 stream5 NDTR
print/x *(unsigned int*)0x40026090                   # DMA1 stream5 PAR
print/x *(unsigned int*)0x40026094                   # DMA1 stream5 M0AR
print &'usart_drv.c'::s_rx_buf
print/x *(unsigned int*)0x400260A0                   # DMA1 stream6 CR (TX)
print/x *(unsigned int*)0x400260A8                   # DMA1 stream6 PAR
print/x *(unsigned int*)0xE000E100 & 0x30000         # NVIC_ISER0: IRQ16,17 (DMA1 str5,6)
print/x *(unsigned int*)0xE000E104 & 0x140           # NVIC_ISER1: IRQ38 (USART2), IRQ40 (EXTI15_10)
x/1xb 0xE000E410
x/1xb 0xE000E411
x/1xb 0xE000E426
x/1xb 0xE000E428
print/x *(unsigned int*)0x40013C00 & 0x400           # EXTI_IMR line 10
print/x *(unsigned int*)0x40013C0C & 0x400           # EXTI_FTSR line 10
print/x *(unsigned int*)0x40013C08 & 0x400           # EXTI_RTSR line 10
print/x *(unsigned int*)0xE000E010 & 7               # SysTick CTRL
print/x *(unsigned int*)0xE000E014                   # SysTick LOAD
print/x *(unsigned int*)0x40023C10                   # FLASH_CR
```

**Expect (in order)**
`0x201001` · `0x20000` · `0x4100` · `0xa3` · `0x0` · `0x1` · `0x77` · `0x1` ·
`0x8b` · `0x201c` · `0xc0` · `0x8000411` · `0x40` · `0x40004404` · address printed
by the next line (must be equal) · (that address) · `0x8000450` (stream 6
idle, EN=0) · `0x40004404` · `0x30000` · `0x140` · priorities `0x20`, `0x20`,
`0x10`, `0x00` (DMA5, DMA6, USART2, EXTI — the panic button is the highest
priority) · `0x400` · `0x400` · `0x0` · `0x7` · `0x3e7f` · `0x80000000` (flash locked).

**If not**
| Symptom | Meaning | Fix |
|---|---|---|
| ENR bits `0` | that driver's `*_Init()` never ran / halted before it | check `main.c` init order; re-do S2 |
| PA10 level `0` with the button released | wiring: button not to GND, or pull-up missing | check PUPDR readout (`0x1`) and the board button |
| `0xa3` wrong | wrong pin mode: PA2/PA3 must be AF (`10`), PA0 analog (`11`) | `usart_drv.c` / `adc_drv.c` MODER lines |
| `0x77` wrong | wrong AF number (USART2 is AF7) | `usart_drv.c` AFR line |
| `BRR` ≠ `0x8b` | baud wrong (HSI 16 MHz → 115200 needs `0x8B`) | `usart_drv.c` |
| `CR1` ≠ `0x201c` | UE/TE/RE/IDLEIE missing | `usart_drv.c` |
| stream CR ≠ expected | wrong channel (must be channel 4 for USART2) / direction | `usart_drv.c` `USART_DMA_CHANNEL4`, `DIR_0` |
| priorities not `0x00` for EXTI | the panic button can be delayed by UART traffic | `exti_drv.c` `NVIC_SetPriority` |
| SysTick LOAD ≠ `0x3e7f` | tick is not 1 ms | `systick_drv.c` |
| `FLASH_CR` ≠ `0x80000000` | a previous flash op left it unlocked | `flash_drv.c` `Flash_Lock()` path |

---

# L2 — Hardware CRC (`crc_drv`)

**Why** The partition header/body validity check (and most tests below) use the CRC.
If it is wrong, every later "valid/invalid partition" result is meaningless, so verify it against known answers first.

**Steps** (from B0)
```
restore tools/mock/seq64.bin binary $buf
set {unsigned int}($buf+64) = 0
```
### T2.1 Known vector
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf+64, 4), CRC_Drv_Result())
```
**Expect** `0xc704dd7b`. **If not** — `0x0` or `0xffffffff` constantly: CRC clock off (L1 AHB1ENR bit 12) or `CRC_Drv_Init` not called; any other value: the hardware is fine but the driver feeds the wrong thing (bytes instead of 32-bit words).

### T2.2 Different data, several lengths
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 4),  CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 16), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 64), CRC_Drv_Result())
```
**Expect** `0x76dbf7c7`, `0x81b46ca`, `0x1125c90e`. **If not** — word packing/endianness in `CRC_Drv_Feed` (`memcpy` into a `uint32_t` is little-endian and must stay so — the partition header CRC in flash depends on it).

### T2.3 Streaming equals one shot (used for header + body)
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 32), CRC_Drv_Feed($buf+32, 32), CRC_Drv_Result())
```
**Expect** `0x1125c90e` (same as the 64-byte one-shot). **If not** — `Feed` resets or reorders between calls; the CRC of `header ‖ body` would never match what `Partition_Store_Commit` computes.

### T2.4 Length that is not a multiple of 4 (tail zero-padded)
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 3), CRC_Drv_Result())
```
**Expect** `0x16ac45a9`. **If not** — tail handling is broken (not zero-padded).

### T2.5 Zero length
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 0), CRC_Drv_Result())
```
**Expect** `0xffffffff`. **If not** — a phantom word is being fed.

### T2.6 Reset really restarts (no leakage between computations)
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 4), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 4), CRC_Drv_Result())
```
**Expect** both `0x76dbf7c7`. **If not** — `CRC->CR = CRC_CR_RESET` isn't taking effect; a stale CRC would make a valid partition look corrupt (or vice-versa).

**Next** if all six pass, the CRC is trustworthy: L3 uses it as the "did the sector really become 0xFF" oracle.

---

# L3 — Flash driver (`flash_drv`) — destructive

**Why** All persistence rests on erase / program / read. Test every behaviour the
partition store relies on, including the edge cases (unaligned sizes, sector
boundaries, isolation between sectors, writing zeros over data).
Start from **B0**. This layer wipes both partitions.

## T3.1 Erase gives a fully blank sector
**Steps**
```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
x/4xw 0x08008000
x/4xw 0x0800BFF0
x/4xw 0x0800C000
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
print/x *(unsigned int*)0x40023C10
print/x *(unsigned int*)0x40023C0C & 0xF2
```
**Why** Erase is the only way to turn 0-bits back to 1; a partial erase would give "phantom" data. The CRC of a whole 16 KB sector compared to the CRC of 16 KB of `0xFF` checks all 4096 words in one number.
**Expect** all words `0xffffffff`; both CRCs `0x34132f69`; `FLASH_CR` `0x80000000` (relocked); error flags `0x0`. The call returns after ~0.1–1 s.
**If not**
* words not `ffffffff` → erase did not run: wrong sector number (`SNB`), flash write-protected (`WRPERR`), or VDD out of range.
* SR & 0xF2 ≠ 0 → an error flag: bit 7 `PGSERR` sequence error, bit 6 `PGPERR` parallelism (PSIZE must be ×32, needs VDD ≥ 2.7 V), bit 4 `WRPERR`. The driver never reads these flags (**KNOWN GAP G8**): a failed erase/write would be silent — fix by checking `FLASH->SR` and returning an error.
* `gdb` never returns → `BSY` stuck; `interrupt` and `print/x *(unsigned int*)0x40023C0C`.

## T3.2 Word-aligned program and read-back
**Steps**
```
restore tools/mock/seq64.bin binary $buf
call (void)Flash_Drv_Write(0x08008000, $buf, 16)
x/20xb 0x08008000
call (void)Flash_Drv_Read(0x08008000, $buf2, 16)
x/16xb $buf2
print/x *(unsigned int*)0x40023C0C & 0xF2
```
**Why** The basic building block of `Commit` (header and body are written this way).
**Expect** first line `00 01 … 07`, second `08 … 0f`, then `ff ff ff ff` (nothing written past 16 bytes); the `Read` buffer holds `00…0f`; error flags `0x0`.
**If not** — bytes still `ff`: programming did not happen (see T3.1 flags); shifted/garbled: `PSIZE` not ×32 or `PG` not held per word; correct in flash but wrong in `$buf2`: `Flash_Drv_Read` (a plain `memcpy`).

## T3.3 Size that is not a multiple of 4 (tail padded with 0xFF)
**Steps**
```
call (void)Flash_Drv_Write(0x08008040, $buf, 6)
x/8xb 0x08008040
x/1xw 0x08008044
```
**Why** Header `crc32` etc. are word-multiples, but the driver must not corrupt or run past the tail for any size.
**Expect** `00 01 02 03 04 05 ff ff`, then word `0xffff0504`. **If not** — bytes beyond the 6 got zeros (padding not `0xFF`) → later programming of that word would be impossible without an erase.

## T3.4 A larger block (64 bytes) is intact
**Steps**
```
call (void)Flash_Drv_Write(0x08008100, $buf, 64)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008100, 64), CRC_Drv_Result())
```
**Expect** `0x1125c90e`. **If not** — a word in the middle was dropped/duplicated (busy-wait missing between words).

## T3.5 Writing zero over programmed data is allowed (used by corruption tests)
**Steps**
```
set {unsigned int}$z = 0
call (void)Flash_Drv_Write(0x08008000, $z, 4)
x/1xw 0x08008000
print/x *(unsigned int*)0x40023C0C & 0xF2
```
**Why** Flash can only clear bits without an erase; overwriting with `0` is the one legal in-place change, and L5 relies on it to fake corruption.
**Expect** `0x00000000`, no error flags. **If not** — value unchanged or an error flag set: your part rejects reprogramming; then skip the "zero a word" corruption cases in T5.6 and corrupt by erasing instead.

## T3.6 Sector isolation and boundaries
**Steps**
```
call (void)Flash_Drv_Write(0x0800C000, $buf, 16)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
call (void)Flash_Drv_EraseSector(2)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
call (void)Flash_Drv_Write(0x0800BFFC, $buf, 4)
x/1xw 0x0800BFFC
x/1xw 0x0800C000
```
**Why** Erasing A must never touch B (that is the whole point of A/B) and a write at the last word of a sector must not spill into the next.
**Expect** the two sector-3 CRCs are **equal** (any value, not `0x34132f69`), sector 2 CRC `0x34132f69` (blank again), `0x0800BFFC` = `0x03020100`, `0x0800C000` still `0x03020100` (the first write, not overwritten).
**If not** — sector 3 changed by erasing sector 2 → the `SNB` field is wrong/masked → **the A/B scheme cannot work**; fix `Flash_Drv_EraseSector`.

## T3.7 Erasing an already-blank sector is harmless
**Steps** `call (void)Flash_Drv_EraseSector(2)` then the sector-2 CRC print from T3.6. **Expect** `0x34132f69`, no hang.

**State after L3:** sector 2 blank, sector 3 has junk. Continue with L4 (no flash needed) or go to L5, which erases both first.

---

# L4 — Crypto (`sha256`, `xor_cipher`)

**Why** The password table is only as protected as these two functions are correct. They were unit-tested on a PC; here we prove the *same C code gives the same answers on the Cortex-M4* (word size, endianness, stack usage). Return to **B0**.

## T4.1 SHA-256 known answers, including the padding boundaries
**Steps** (repeat for N = 0, 1, 3, 55, 56, 63, 64)
```
restore tools/mock/seq64.bin binary $buf
call (void)SHA256_Compute($buf, 0, $h)
x/32xb $h
```
**Why** 55/56 and 63/64 bytes are where the padding/length field crosses into a second block — the classic place for off-by-one bugs.
**Expect** the digest bytes equal the table in *Reference values* for that N (read the four `x/32xb` rows left-to-right).
**If not** — all wrong: `sha256.c` differs from the tested one (stale build → S3); only 55/56/63/64 wrong: padding logic; random corruption: stack overflow (increase `_Min_Stack_Size`).

## T4.2 The master-key hash
**Steps**
```
call (void)SHA256_Compute($mk, $mkl, $h)
x/8xw $h
```
**Expect** `0xdb593378 0xa9fbe61f 0x5d89faba 0x8431b4f9 0x743792e8 0x0351b9af 0x75cd08ed 0x0823aaa9`. **If not** — key string or length differs (`$mkl` must be 14); this is the value stored in every partition header and the value Stage C will compare against.

## T4.3 XOR cipher known answer, round trip, nonce and key sensitivity
**Steps**
```
restore tools/mock/seq64.bin binary $buf
call (void)XorCipher_Apply($buf, 48, $mk, $mkl, 1)
x/48xb $buf
call (void)XorCipher_Apply($buf, 48, $mk, $mkl, 1)
x/48xb $buf
call (void)XorCipher_Apply($buf, 48, $mk, $mkl, 2)
x/16xb $buf
restore tools/mock/seq64.bin binary $buf
call (void)XorCipher_Apply($buf, 48, $mk, $mkl - 1, 1)
x/16xb $buf
restore tools/mock/seq64.bin binary $buf
call (void)XorCipher_Apply($buf, 0, $mk, $mkl, 1)
x/8xb $buf
```
**Why** Three properties: deterministic known answer (proves it really transforms data), symmetric (the same call decrypts), and depends on both nonce and key (otherwise a wrong key could still "decrypt"). 48 bytes crosses the 32-byte keystream block boundary. Zero length must be a no-op.
**Expect** (in order) the `nonce 1` row of *Reference values* (48 bytes); then `00 01 02 … 2f` (the round trip returned the plaintext); then, because the buffer now holds plaintext again and is encrypted with nonce 2, the first 16 bytes `ff 5e ea 93 91 2c 07 c1 61 a0 1d 2c 63 20 cd 1b`; then, for the 13-byte key, `d8 31 63 99 6f 35 00 73 65 93 fb 02 3a 47 23 3b`; finally, for length 0, the untouched `00 01 02 03 04 05 06 07`.
**If not** — first block right, second wrong: the block counter; no round trip: key/nonce bytes differ between the two calls (aliasing); same output for different nonce/key: those inputs are ignored.

## T4.4 Whole-table encrypt/decrypt
**Steps**
```
restore tools/mock/table_full.bin binary $tbl
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
call (void)XorCipher_Apply($tbl, 1488, $mk, $mkl, 1)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
call (void)XorCipher_Apply($tbl, 1488, $mk, $mkl, 1)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
```
**Why** 1488 = 46.5 keystream blocks: tests the partial last block and the exact length the partition uses.
**Expect** `0x60085d29`, then `0x29562dc9` (the same ciphertext CRC L5 expects in flash), then `0x60085d29`. **If not** — the ciphertext CRC differs → the partition tests below would fail for a crypto reason, not a flash reason.

---

# L5 — Partition store (`partition_store`) — destructive

**Why** This is the heart of your design: encrypted storage, A/B partitions
with a version, CRC-validated headers, and fallback. Return to **B0**, then:
```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
call (void)SHA256_Compute($mk, $mkl, $h)
```
Shorthand used below (type them out in full):
* **INIT** = `call (void)Partition_Store_Init()`
* **STATE** = `print/x 'partition_store.c'::s_active` and `print 'partition_store.c'::s_have_active`
* **COMMIT** = `call (int)Partition_Store_Commit($mk, $mkl, (unsigned int*)$h, &'mode_retrieve.c'::s_table)` (returns `1`)
* **LOAD** = `call (int)Partition_Store_Load($mk, $mkl, &'mode_retrieve.c'::s_table)`

## T5.1 Blank flash → no active partition
**Steps** INIT, STATE, `print Partition_Store_Active()`
**Why** First boot must be detectable so the application can seed / ask for a master key.
**Expect** `s_have_active = false`, `Partition_Store_Active()` → `0x0`.
**If not** — a blank sector looks valid → the validity test accepts `0xFFFFFFFF` magic; check `PARTITION_MAGIC` and `ReadSlot`.

## T5.2 First commit (full table) and what is physically in flash — encryption at rest
**Steps**
```
restore tools/mock/table_full.bin binary $tbl
COMMIT
STATE
x/11xw 0x08008000
x/16xb 0x0800802C
x/16xb 0x080085EC
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800802C, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
```
**Why** Your priority #1. The header must be self-describing and correct, and the bytes in flash must be **ciphertext** that equals the *known answer* for this exact table, key and version — not the plaintext you hard-coded.
**Expect**
* `STATE`: `valid = 0x1, sector = 0x2, addr = 0x8008000, header = {magic = 0x52554e31, version = 0x1, hash_mk = {0xdb593378, 0xa9fbe61f, 0x5d89faba, 0x8431b4f9, 0x743792e8, 0x351b9af, 0x75cd08ed, 0x823aaa9}, crc32 = 0x43e8c184}`, `s_have_active = true`
* header words `52554e31 00000001 db593378 … 0823aaa9 43e8c184`
* `5a 82 6c b9 da e2 7a b3 7b 3f 7d 42 18 54 27 b1` and `e8 52 95 10 8f 3a 4d f2 1f 60 7c 3f 75 89 d3 d8`
* body CRC `0x29562dc9`; sector 3 CRC `0x34132f69` (untouched)
**If not**
* first bytes `53 30 30 61 …` ("S00a…") → **stored in plaintext** → `Commit` skips `XorCipher_Apply` or encrypts a copy but writes the original; look at `partition_store.c` around `s_commit_scratch`.
* ciphertext differs from the known answer but is not plaintext → wrong nonce (must be `new_version` = 1), wrong key/length passed to `Commit`, or L4 not passing.
* header `crc32` differs → CRC covers different bytes (must be `magic,version,hash_mk` = first 40 bytes, then the 1488 ciphertext bytes) — L2/L4 must pass first.
* sector 3 changed → commit wrote to the wrong sector (A/B selection bug).

## T5.3 Negative and positive control for encryption — search memory for the plaintext
**Steps**
```
find /b 0x08008000, +0x8000, '!','@','#','$','%','^','&','*'
find /b 0x08008000, +0x8000, 'a','b','c','d','e','f','g','h','i','j','k','l'
find /b 0x08008000, +0x8000, 'S','0','0','a','b','c','d','e'
find /b 0x08008000, +0x8000, 'p','w','0','0','!','@','#','$'
find /b $tbl, +1488, 'p','w','0','0','!','@','#','$'
find /b $tbl, +1488, '!','@','#','$','%','^','&','*'
```
**Why** T5.2 proves the bytes match the known ciphertext; this proves the *absence* of any plaintext anywhere in both partitions, and the last two lines prove the search itself works (the RAM copy must be found), so an empty result really means "not there".
**Expect** the first four: `Pattern not found.`; the fifth: one address (`s_table`) and `1 pattern found.`; the sixth: 31 addresses and `31 patterns found.`
**If not** — any hit in `0x0800xxxx` → plaintext in flash (see T5.2); no hit in RAM → `$tbl` was not populated (redo the `restore`).

## T5.4 Decrypt round trip through Init + Load
**Steps**
```
restore tools/mock/table_empty.bin binary $tbl
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
INIT
STATE
LOAD
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
print 'mode_retrieve.c'::s_table.entries[0]
print 'mode_retrieve.c'::s_table.entries[30]
```
**Why** Proves flash → CRC check → decrypt gives back *exactly* the original 1488 bytes (compare one CRC instead of eyeballing 1488 bytes), including the first and last entries at full length.
**Expect** `0xbb034f8e` (table cleared); `STATE` same as T5.2; `LOAD` returns `1`; then `0x60085d29`; `entries[0] = {name = "S00abcdefghijkl", password = "pw00!@#$%^&*()-_=+[]{};:,.<>/?a"}`; `entries[30]` likewise with `30`.
**If not** — CRC differs but `entries[0]` looks right → damage later in the table (length/last-block bug, T4.4); `LOAD` returns `0` → `INIT` found no valid partition (re-check T5.1/T5.2 state).

## T5.5 Wrong key is *not* rejected by Load (design fact you must know)
**Steps**
```
restore tools/mock/table_empty.bin binary $tbl
call (int)Partition_Store_Load($mk, $mkl - 1, &'mode_retrieve.c'::s_table)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
print 'mode_retrieve.c'::s_table.entries[0]
LOAD
```
**Why** `Partition_Store_Load` does not check the master key (**KNOWN GAP G5**): a wrong key returns `1` and fills the table with garbage. The only protection is comparing `hash_mk` first — that is the Stage C `MK_AUTH_CHECK` job. Confirm this so nobody assumes `Load` authenticates.
**Expect** returns `1`, CRC `0xad1d50b6`, garbage bytes in `entries[0]`; the last `LOAD` restores the good table.
**What next** Stage C: compare `SHA256(input_mk)` with `s_active.header.hash_mk` **before** calling `Load`.

## T5.6 Toggle: commit goes to the *other* sector, version +1, old one untouched (priority #2)
**Steps** (table currently good from T5.5's last `LOAD`)
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
set var 'mode_retrieve.c'::s_table.entries[0].name[0] = 'Z'
COMMIT
STATE
x/2xw 0x0800C000
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
restore tools/mock/table_empty.bin binary $tbl
INIT
STATE
LOAD
print 'mode_retrieve.c'::s_table.entries[0].name
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
COMMIT
STATE
x/2xw 0x08008000
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
INIT
STATE
```
**Why** There is no `TOGGLE_PARTITION` command yet, so this exercises the *real* mechanism that toggling, generating a password and changing the key will all use: write to the inactive sector with `version + 1`, leave the active one untouched (power-loss safety), and let `Init` pick the newer one. Calling `Partition_Store_Commit` from gdb goes through the real erase/program code.
**Expect**
1. sector-2 CRC before = `0xa199c8f9`
2. after commit #2: `sector = 0x3, addr = 0x800c000, version = 0x2, crc32 = 0xd70a1ec5`; `0x0800C000` words `0x52554e31 0x00000002`; sector-2 CRC **unchanged** `0xa199c8f9`; sector-3 CRC `0x681f2be5`
3. after `Init` + `Load` from the emptied table: active = sector 3 / version 2; `entries[0].name = "Z00abcdefghijkl"`; table CRC `0x1d72cce5`
4. commit #3 goes **back** to sector 2: `sector = 0x2, version = 0x3, crc32 = 0x99ca4ab9`; `0x08008000` words `0x52554e31 0x00000003`; sector-3 CRC still `0x681f2be5`; `Init` → active = sector 2 / version 3.
**If not**
* commit #2 landed in sector 2 again → the "other sector" logic (`active_is_a`) is inverted or `s_have_active` is stale (call INIT first).
* sector-2 CRC changed → `Commit` erased/wrote the active sector → **a power loss during a commit would destroy the only valid copy**.
* version did not increase / `Init` still picks sector 2 → the version comparison (`>=`) or the version written to flash is wrong.
* `entries[0].name` is not `Z00…` after Init+Load → Init selected the stale partition.

## T5.7 Corruption and fallback matrix (the reason for the CRC + version design)
**Setup** state must be **A v3 active, B v2 valid** — that is where T5.6 ended. Every case below ends with a **recommit** that restores exactly that state (because after a fallback to B the next commit writes A with version 3 again).
Prepare the zero source once: `set {unsigned int}$z = 0`.
Each row: perform the action, run INIT + STATE, check, then run the "restore".

| # | Action (destroys…) | Steps | Expect after INIT | Restore |
|---|---|---|---|---|
| a | active header CRC | `call (void)Flash_Drv_Write(0x08008028, $z, 4)` | active = **sector 3, v2**, `crc32 = 0xd70a1ec5`; `LOAD` gives the (same) table | `LOAD`, `COMMIT` → sector 2 v3, crc `0x99ca4ab9` |
| b | active magic | `call (void)Flash_Drv_Write(0x08008000, $z, 4)` | active = sector 3, v2 | `COMMIT` |
| c | one word of the active ciphertext | `call (void)Flash_Drv_Write(0x08008090, $z, 4)` | active = sector 3, v2 | `COMMIT` |
| e | whole active sector erased | `call (void)Flash_Drv_EraseSector(2)` | active = sector 3, v2 | `COMMIT` |
| f1 | interrupted commit, only the header written | `call (void)Flash_Drv_EraseSector(3)` then `call (void)Flash_Drv_Write(0x0800C000, (unsigned char*)0x08008000, 44)` | active stays **sector 2, v3** (inactive header has a valid magic but its body is blank → CRC mismatch) | none (B is now invalid; that is the point) |
| f2 | interrupted commit, header + part of the body | `call (void)Flash_Drv_Write(0x0800C02C, (unsigned char*)0x0800802C, 700)` | active stays **sector 2, v3** | none |
| d | both partitions corrupt | `call (void)Flash_Drv_Write(0x08008028, $z, 4)` and `call (void)Flash_Drv_Write(0x0800C028, $z, 4)` | `s_have_active = false`, `Partition_Store_Active()` = `0x0` | see below |

For row **d** also run:
```
restore tools/mock/table_empty.bin binary $tbl
set {unsigned int}$tbl = 0x5A5A5A5A
call (int)Partition_Store_Load($mk, $mkl, &'mode_retrieve.c'::s_table)
x/1xw $tbl
```
**Expect** `Load` returns `0` and `x` still shows `0x5a5a5a5a` (a failed load must not touch the output).
Then `continue`: the firmware boots, sees no partition, **re-seeds** the default table and shows the menu (L8.3 verifies the details).

**Why** These are the real failure modes: bit rot / bad write (a, b, c), erased sector (e), and power loss halfway through a commit (f1, f2 — the new partition is half written, so the design must keep using the old one), and total loss (d → first-boot path).
**If not**
* a/b/c: `Init` still returns the corrupted sector → validity test is missing that field (`ReadSlot`/`ValidateCrc`).
* f1/f2: `Init` switches to the half-written sector → **data-loss bug** (CRC must cover header *and* body).
* d: `s_have_active` stays true → a stale `s_active` is kept; or `Load` returns `1` and overwrites the table.
* `COMMIT` returns `1` even if flash writes failed — **KNOWN GAP G4**: `Commit` never re-reads/validates what it wrote. Suggested fix: after writing, run the same validation as `ReadSlot` and return its result.

**State after L5:** partitions hold test data. Do the **Cleanup** (top of file) before L6 if you want the default seed, or continue — L6 does not touch flash.

---

# L6 — USART2 + DMA driver (`usart_drv`)

**Why** Every message and every command goes through this. It is interrupt/DMA driven, so it is the layer with the most timing-dependent behaviour. Most tests use **B0** (the application loop is not running, so nothing else consumes the RX line) and call the ISRs by hand because a halted CPU does not run them.

## T6.1 TX: DMA sends bytes, flag handling
**Steps** (from B0)
```
set {unsigned int}$buf = 0x6c6c6568
set {unsigned int}($buf+4) = 0x0a0d216f
print USART_Drv_Send($buf, 8)
print 'usart_drv.c'::s_tx_ready
print/x *(unsigned int*)0x40026004 & 0x200000
call (void)DMA1_Stream6_IRQHandler()
print 'usart_drv.c'::s_tx_ready
print/x *(unsigned int*)0x40026004 & 0x200000
```
**Why** Confirms the whole TX chain: DMA request wiring (stream 6, channel 4), the transfer, the transfer-complete flag, and the ISR that sets `tx_ready`.
**Expect** `hello!` and a newline appear in the terminal; `Send` → `true`; `s_tx_ready` → `false` (the ISR cannot run while halted); TC flag `0x200000`; after the manual ISR: `s_tx_ready` → `true`, TC flag `0x0`.
**If not** — nothing in the terminal: wrong channel/stream, `DMAT` bit missing (`CR3` in L1), TX pin not AF7, or wrong COM port/baud in the terminal; text appears but TC flag never sets → NDTR/length wrong; flag sets but `s_tx_ready` stays `false` → the ISR does not clear/compare the right bit (`DMA_HISR_TCIF6`).

## T6.2 TX while busy is rejected, length 0 is rejected
**Steps** (`s_tx_ready` must be `true`; if not, `call (void)DMA1_Stream6_IRQHandler()`)
```
restore tools/mock/table_full.bin binary $tbl
print USART_Drv_Send($tbl, 1488) + USART_Drv_Send($tbl, 1488)
call (void)DMA1_Stream6_IRQHandler()
print USART_Drv_Send($tbl, 0)
```
**Why** The application assumes `Send` never interleaves two transfers; the second call must fail cleanly while the first is in flight. 1488 bytes take ~130 ms on the wire, far longer than the gdb round trip (a short message could finish in between and make this test lie).
**Expect** the sum is `1` (first true, second false), the table's text (names and passwords, with NULs between) appears **once**, and `Send(…, 0)` → `0`.
**If not** — sum `2` → the busy flag isn't set/cleared correctly (or your message was too short); garbled text → two DMA configurations overlapped.

## T6.3 RX: one line, CR/LF variants, re-arm
**Steps** (from B0 — the app is not running)
1. In the terminal type `hello` and press Enter.
2. ```
   print/x *(unsigned int*)0x40004400 & 0x10
   call (void)USART2_IRQHandler()
   print 'usart_drv.c'::s_rx_complete
   print 'usart_drv.c'::s_rx_len
   x/8xb 'usart_drv.c'::s_rx_buf
   print USART_Drv_TakeLine((char*)$buf, 128)
   x/s $buf
   print 'usart_drv.c'::s_rx_complete
   print/x *(unsigned int*)0x4002608C
   print/x *(unsigned int*)0x40026088 & 1
   ```
Repeat with the terminal's Enter set to **CR**, **LF** and **CR+LF**.
**Why** Proves DMA captured the bytes without CPU help, the IDLE interrupt marks the line end (no timer), CR/LF is stripped, and RX is re-armed for the next line.
**Expect** IDLE flag `0x10`; `s_rx_complete` `true`; `s_rx_len` `6` (CR or LF) or `7` (CR+LF); bytes `68 65 6c 6c 6f 0d …`; `TakeLine` returns `5`; `"hello"`; `s_rx_complete` `false`; `NDTR` `0x40` and stream enabled `1` again.
**If not** — IDLE flag `0`: `IDLEIE`/receiver not enabled or bytes did not arrive (check RX pin/baud); `s_rx_len` `0`: DMA did not capture (channel/stream, `DMAR`); `TakeLine` returns `6`/`7` → CR/LF stripping broken; `NDTR` not `0x40` → not re-armed, so the next line would be lost.

## T6.4 RX: empty line (just Enter)
**Steps** press Enter only, then repeat the second block of T6.3.
**Expect** `s_rx_len` `1` (or `2`), `TakeLine` returns `0`, `x/s $buf` shows `""`. **Why** the menu/retrieve screens treat an empty line specially. **If not** — a stray byte is delivered as a line.

## T6.5 RX: line longer than the buffer (64 bytes)
**Steps** paste a 70-character line, Enter, then
```
call (void)DMA1_Stream5_IRQHandler()
call (void)USART2_IRQHandler()
print 'usart_drv.c'::s_rx_len
print USART_Drv_TakeLine((char*)$buf, 128)
x/s $buf
print/x *(unsigned int*)0x40004400 & 0x8
```
**Why** The driver has a fixed 64-byte line buffer; overlong input must not crash or overwrite RAM.
**Expect** `s_rx_len` `64`, `TakeLine` returns `64` (the first 64 characters; the rest is dropped). The ORE (overrun) flag `0x8` will be set — **PREDICTED**: the dropped characters leave the USART in overrun.
**What next / then check** type a short line `ok` and repeat T6.3: **PREDICTED** the next line may start with a stale character left in `DR` from the overflow. If it does, fix `USART_Drv_TakeLine`/the TC path: read `USART2->SR` then `USART2->DR` to clear ORE and discard the stale byte before re-arming.

## T6.6 RX: second line typed before the first was taken is lost — **KNOWN GAP G6**
**Steps** type `aaa` Enter, `call (void)USART2_IRQHandler()`, then type `bbb` Enter **without** taking the line, then `print USART_Drv_TakeLine((char*)$buf, 128)`, `x/s $buf`.
**Expect** `"aaa"` only. `bbb` is lost because the RX DMA stream stays disabled until `TakeLine`. In normal operation the application takes each line within microseconds, but it can be missed while the CPU is stalled by a flash erase (T8.1) — don't type during a commit. **Fix if you need it:** run DMA in circular mode and let IDLE just record the write position.

---

# L7 — Password table and RETRIEVE_MODE screens (full mock data)

**Why** This is what the user sees. The expected screens were produced by running the real `mode_retrieve.c` / `password_table.c` on a PC, so every character below is exact.

**Load procedure "LOAD-MOCK X"** (from B0; `X` = full, sparse, empty, holes, unterm):
```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
restore tools/mock/table_X.bin binary $tbl
call (void)SHA256_Compute($mk, $mkl, $h)
call (int)Partition_Store_Commit($mk, $mkl, (unsigned int*)$h, &'mode_retrieve.c'::s_table)
continue
```
The firmware then boots, finds a valid partition (so it does **not** re-seed) and prints the menu. In the terminal send `1` to enter RETRIEVE_MODE.
Line length used in the tables below: input is sent one line at a time — **do not paste several lines at once** (T6.6).

## T7.1 FULL table: listing and every entry at full length
**Steps** LOAD-MOCK full, send `1`, then send each id `0` … `30`, then `q`.
**Why** 31 entries × 15-char names and 31-char passwords is the maximum the data structure holds; it also proves the 1024-byte output buffer and the single DMA transfer are big enough (the listing is 768 bytes).
**Expect** first the header and 31 lines, then one line per id:
```
-- Password table --
   0 - S00abcdefghijkl
   1 - S01abcdefghijkl
   …
  30 - S30abcdefghijkl

Enter id to view (0-30), or 'q' to go back: 
```
and for id `7`: `S07abcdefghijkl : pw07!@#$%^&*()-_=+[]{};:,.<>/?a`. Every id `n` shows `S<nn>abcdefghijkl : pw<nn>!@#$%^&*()-_=+[]{};:,.<>/?a`. `q` returns to the menu.
**If not** — list truncated after N lines → output buffer/`uint16_t` length (`password_table.c`); a wrong entry for one id → table shifted (entry size/`sizeof` mismatch between encrypt and decrypt, L4/L5); characters like `%` missing → format-string misuse; last characters cut off (`…?a`) → password buffer too small.

## T7.2 Ids that must be rejected, and ids that (wrongly) are not
**Steps** on the full table send each line in the *Input* column at the id prompt.

| Input | **Expected by design** | **What the code does today** (simulated) |
|---|---|---|
| `0`, `7`, `30` | that entry | that entry |
| `31`, `99` | `Invalid id.` | `Invalid id.` |
| `-1` | `Invalid id.` | `Invalid id.` |
| ` 5` (leading space), `07` | entry 5 / entry 7 | entry 5 / entry 7 |
| `abc` | `Invalid id.` | **shows entry 0 — KNOWN GAP G2** |
| `4294967296` | `Invalid id.` | **shows entry 0** (wraps to 0) |
| `0x5` | `Invalid id.` | **shows entry 0** (`strtoul` stops at `x`) |
| `q`, `Q`, `quit`, empty line | back to menu | back to menu |

**What next (G2)** in `mode_retrieve.c` parse with an end pointer and reject anything that isn't all digits and `< 31`: `char *end; unsigned long v = strtoul(line, &end, 10); if (end == line || *end != '\0' || v >= PWD_TABLE_MAX_ENTRIES) → "Invalid id."`.

## T7.3 SPARSE table (gaps + both boundary ids)
**Steps** LOAD-MOCK sparse, `1`, then `1`, `15`, `q`.
**Expect** listing shows exactly `0 - S00abcdefghijkl`, ` 15 - S15abcdefghijkl`, ` 30 - S30abcdefghijkl`; id `1` → `(no entry at that id)`; id `15` → `S15abcdefghijkl : pw15!@#$%^&*()-_=+[]{};:,.<>/?a`.
**If not** — an empty slot is listed → `Password_Table_EntryIsUsed` wrong; id 30 missing → off-by-one in the loop bound.

## T7.4 EMPTY table
**Steps** LOAD-MOCK empty, `1`, `0`, `q`. **Expect** `  (empty)` under the header; `0` → `(no entry at that id)`. **If not** — a blank header only, or garbage lines → decrypt of an all-zero table isn't all zero (L4/L5).

## T7.5 HOLES table (used-ness is decided by the name only)
**Steps** LOAD-MOCK holes, `1`, then `3`, `4`, `5`, `q`.
**Expect** listing `   4 - nopass` and `   5 - normal` only; `3` → `(no entry at that id)` (password without a name is treated as unused); `4` → `nopass : ` (empty password shown); `5` → `normal : pass5`.
**Design note** decide whether an entry with a password but no name should be hidden (current) or shown; both are defensible, but a *generator* mode must never create one.

## T7.6 UNTERMINATED name — **KNOWN GAP G3**
**Steps** LOAD-MOCK unterm, `1`, `0`, `q`.
**Expect today** listing `   0 - XXXXXXXXXXXXXXXXsecretpw` and `XXXXXXXXXXXXXXXXsecretpw : secretpw` — the name runs into the password because `%s` stops only at a NUL and the 16-byte name field has none. Corrupted or attacker-controlled data can therefore print other fields.
**What next** print with a precision: `"%.*s"` with `PWD_NAME_LEN - 1` / `PWD_SECRET_LEN - 1`, or force `name[15] = 0; password[31] = 0` after decrypting.

## T7.7 Screen is intact after decrypt (RAM check)
**Steps** LOAD-MOCK full, `1`, then in gdb `interrupt` and
```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
print 'app.c'::g_state
continue
```
**Expect** `0x60085d29`, `APP_STATE_RETRIEVE_MODE`. **If not** — CRC differs while screens look right → part of the table is corrupted in RAM (stack/buffer overlap; check `_Min_Stack_Size`).

---

# L8 — Application state machine, boot, persistence

**Why** Checks that the pieces work together: first boot seeds a table, later boots reuse it, and the menu handles every kind of input. Each boot test starts from **B0**.

## T8.1 First boot on blank flash seeds the default table
**Steps**
```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
continue
```
then in the terminal: `1`, `0`, `1`, `q`; then `interrupt` and `print/x 'partition_store.c'::s_active`.
**Expect** the menu within ~1 s (the seed commit erases and programs flash — the CPU is stalled for that time); listing `   0 - example.com` / `   1 - email`; `example.com : hunter2`; `email : correct-horse-battery`; `s_active`: `sector = 0x2, version = 0x1, crc32 = 0x81eb34e2`.
**If not** — never reaches the menu → the seed commit hangs (flash busy/locked, L3); listing shows only entry 0 → the seed table was written incomplete or an old partition is being reused (erase both sectors and repeat — reflashing does **not** clear sectors 2–3); `crc32` differs → different seed data or key.

## T8.2 Reset keeps the data (persistence, no re-seed)
**Steps** `tbreak App_Run`, `monitor reset`, `continue` (stops at B0), `continue` again (the menu appears immediately, no flash-erase delay), then `1` in the terminal; then `interrupt`, `print/x 'partition_store.c'::s_active.header.version`.
**Expect** same two entries; `version` **still `0x1`** (a re-seed would increase/reset it and delay the menu). **If not** — the table reverts to defaults each boot → `Partition_Store_Init` doesn't accept a valid partition (check T5.x), or `HandleInit` seeds unconditionally.

## T8.3 Both partitions invalid → re-seed (first-boot path after corruption)
**Steps** from B0 with valid data present, corrupt both CRCs (the two `Flash_Drv_Write(…, $z, 4)` calls of T5.7 row d), `continue`, then `1` and `q`, then `interrupt` and `print/x 'partition_store.c'::s_active`.
**Expect** the menu appears after the usual seed delay and the default two-entry table is back; `s_active`: `sector = 0x2, version = 0x1`. (**PREDICTED** from the code: with no valid partition `Commit` starts again at version 1 in sector A; T5.1 covers the "no active partition" half of this.)
**If not** — stuck without a menu → seeding is skipped when a corrupt (but "present") partition exists.

## T8.4 Only B is valid → boot uses B
**Steps** from B0: erase both sectors, `restore tools/mock/table_full.bin binary $tbl`, compute `$h` (`call (void)SHA256_Compute($mk, $mkl, $h)`), run COMMIT twice (A v1, then B v2), then `call (void)Flash_Drv_EraseSector(2)`, `continue`, `1`.
**Expect** the table from B; no re-seed; menu quickly. **If not** — an A-preferring shortcut: the boot logic must accept either sector.

## T8.5 Menu input matrix
**Steps** from the menu send each input; observe, then get back to the menu.

| Input | Expected | Today (simulated) |
|---|---|---|
| `1` | RETRIEVE_MODE screen | RETRIEVE_MODE screen |
| `2` | GENERATE_MODE: `Id to write (0-30), or 'q' to go back:` (see L11) | same |
| `3` | `Not implemented yet.` then the menu again | same |
| empty line | `Unknown option.` + menu | same |
| `x`, `0`, `9` | `Unknown option.` + menu | same |
| `12`, `1abc` | `Unknown option.` | **enters RETRIEVE_MODE — KNOWN GAP G9** (only the first character is checked) |
| ` 1` (leading space) | `Unknown option.` | `Unknown option.` |

**What next (G9)** compare the whole line (`line[0]=='1' && line[1]=='\0'`).
**If a "same" row differs** → `HandleModeSelection`/`HandleNotImplemented` in `app.c` (entry/exit of states; the menu is reprinted once per entry via the `entered` flag).

## T8.6 RETRIEVE_MODE exit and re-entry, repeated
**Steps** enter with `1`, leave with `q`, repeat 20 times; alternate `q`, `Q`, empty line. Afterwards `interrupt`, `print 'app.c'::g_state`, `print 'mode_retrieve.c'::s_sub`, `continue`.
**Expect** every cycle shows the full listing; `g_state = APP_STATE_MODE_SELECTION`. **If not** — output degrades/hangs after N cycles → a resource leak or TX flag left false (L6); listing empty after the first exit → the table was wiped on exit but not reloaded on re-entry.

## T8.7 Line-ending tolerance and typing speed
**Steps** set the terminal's Enter to CR, then LF, then CR+LF and use the menu each time; then type digits slowly (1 s between characters), then quickly.
**Expect** identical behaviour. **If not** — a line-end style is not stripped (T6.3), or a slow typist gets a partial line (IDLE triggers on the pause between characters — **PREDICTED** with very slow typing you may get `1` and `2` as two separate lines; that is a property of using the IDLE line as the terminator).

---

# L9 — Panic button (EXTI) and RAM wipe

**Why** Your priority #3: pressing PA10 must destroy the decrypted table (and any other copy of a secret) in RAM immediately and return the user to a safe screen. PA10 wiring/priority was checked in L1; here we test behaviour.

## T9.1 Button electrical check
**Steps** `print/x (*(unsigned int*)0x40020010 >> 10) & 1` with the button released, then **hold** it and run again.
**Expect** `0x1` released, `0x0` held. **If not** — always `1`: not connected/wrong pin; always `0`: shorted or pull-up missing.

## T9.2 The interrupt actually fires
**Steps** `break EXTI15_10_IRQHandler`, `continue`, press the button.
**Expect** gdb stops in `EXTI15_10_IRQHandler`; `print/x *(unsigned int*)0x40013C14 & 0x400` → `0x400` (pending bit 10 set). Then `delete`, `continue`.
**If not** — no stop: EXTI line not unmasked / wrong `SYSCFG` mapping (`EXTICR3`, PA), falling-edge not selected, or NVIC line 40 not enabled (L1). Stops repeatedly without a press: floating input (pull-up missing).

## T9.3 The decrypted table is wiped and the state is reset (priority #3)
**Steps**
1. LOAD-MOCK full (L7), send `1`, then `7` — the screen shows entry 7, so `s_table` holds plaintext **and** `s_line_buf` holds the formatted line `S07abcdefghijkl : pw07…`.
2. `interrupt`, then check the *before* state:
   ```
   print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
   find /b $tbl, +1488, 'p','w','0','7','!','@','#','$'
   ```
   → `0x60085d29`, `1 pattern found.`
3. ```
   break Mode_Retrieve_Wipe
   continue
   ```
   **press the PA10 button** — gdb stops inside `Mode_Retrieve_Wipe`.
4. ```
   finish
   next
   print 'app.c'::g_state
   print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
   find /b $tbl, +1488, 'p','w','0','7','!','@','#','$'
   x/12xw $tbl
   ```
**Expect** `g_state` → `APP_STATE_MODE_SELECTION`; table CRC `0xbb034f8e` (= all zero); `Pattern not found.`; twelve zero words. Then `delete`, `continue` — the terminal reprints the menu.
**If not**
* CRC not `0xbb034f8e` → `Mode_Retrieve_Wipe` did not zero the whole table (length/pointer) or the wipe ran on a copy.
* breakpoint never hit → see T9.2.
* `g_state` unchanged → `App_PanicHandler` doesn't set it, or the main loop overwrote it.

## T9.4 Nothing readable is left anywhere in RAM (the full RAM search)
**Steps** immediately after step 4 of T9.3 (still stopped):
```
find /b 0x20000000, +0x20000, 'p','w','0','7','!','@','#','$'
find /b 0x20000000, +0x20000, 'S','0','7','a','b','c','d','e'
```
For every address printed: `info symbol <address>`.
**Why** A wiped `s_table` means little if another buffer still holds the same text. `Password_Table_ShowEntry` formats `"name : password"` into `s_line_buf` and only `s_table` is wiped. **PREDICTED — KNOWN GAP G1:** both searches will find the string in `s_line_buf` (`info symbol` → `s_line_buf + …`), i.e. the password is still readable in RAM after the panic button. The stack may also contain fragments (from `snprintf`); those are addresses just below `0x20020000` and cannot be fully cleaned — note them but focus on named buffers.
**Expect (design goal)** `Pattern not found.` for both.
**What next (G1)** add `Password_Table_WipeScratch()` (`memset(s_line_buf, 0, sizeof s_line_buf)`) and call it from `Mode_Retrieve_Wipe()`; also clear `s_rx_buf` if it can hold secrets; in Stage C also wipe `input_mk`. Re-run T9.4 until both searches are empty (except stack residue, which you document).

## T9.5 Panic at each moment; flash must be unharmed
**Steps** with the full table loaded, press the button (a) at the menu, (b) right after sending `1` (during the 770-byte listing), (c) waiting at the id prompt, (d) right after an entry is shown, (e) three separate presses, about a second apart. After each press: check the terminal, then send `1` and `7`.
**Expect** always a fresh `== MODE SELECTION ==`; the in-flight listing may finish printing first (bytes already handed to DMA) but nothing after it; afterwards `1` lists all 31 names again and `7` shows the full password (the wipe only touched RAM; flash is intact). `interrupt` / `print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())` after re-entering retrieve → `0x60085d29`.
**If not** — hang or garbled text after a press → the ISR blocks or the state is left mid-transition; the table gone after re-entry → flash was modified.

## T9.6 Edge: first press right after boot — **PREDICTED gap G7**
`exti_drv.c` compares against `s_last_trigger_ms = 0`, so a press within the first 200 ms after reset is ignored. Not practical to test by hand; fix by initialising the timestamp to `(uint32_t)-200` or using a separate "seen a press" flag.

---

# L10 — End-to-end regression and progress report

## T10.1 Automated smoke test
After **Cleanup** (erase both sectors) and a reset, run on the PC:
```
python3 tools/hw_test.py /dev/ttyACM0
```
It expects the *default* 2-entry table, so it must run on a freshly seeded board. It also saves a generated password at id 30 (L11), so it changes flash; erase both sectors again before repeating a run that must start from the default table. **Expect** `20/20 checks passed`. It is only a wrapper around T8.1/T8.5/T7.x; if it fails, the failing line names the layer.

## T10.2 Progress report checklist
Copy this table into your report and fill **Result** with PASS / FAIL(+gap id) / N/A and the date.

| ID | Test | Result |
|---|---|---|
| S3 | Flashed image matches ELF | |
| L1 | Peripheral registers | |
| T2.1–2.6 | CRC known answers, streaming, tail, empty, reset | |
| T3.1–3.7 | Flash erase/program/read, unaligned, isolation, zero-over-data | |
| T4.1–4.4 | SHA-256 vectors, hash_mk, XOR round trip, whole table | |
| T5.1–5.4 | Blank detect, first commit, **encryption at rest**, decrypt round trip | |
| T5.5 | Wrong key behaviour (G5) | |
| T5.6 | **A/B toggle**, version, old sector untouched | |
| T5.7 a–f2, d | Corruption, interrupted commit, fallback | |
| T6.1–6.6 | UART TX/RX, CR/LF, empty, overflow (G6) | |
| T7.1–7.7 | Full / sparse / empty / holes / unterminated (G2, G3) | |
| T8.1–8.7 | Seed, persistence, corrupt→re-seed, menu matrix (G9) | |
| T9.1–9.5 | Button, interrupt, **RAM wipe**, RAM-wide search (G1), panic timing | |
| T10.1 | hw_test.py | |
| T11.1–11.8 | GENERATE_MODE and ADC DMA | |

## Known gaps these tests expose (with the fix)

| ID | Gap | Shown by | Fix |
|---|---|---|---|
| G1 | plaintext password stays in `s_line_buf` after panic | T9.4 | wipe scratch buffers in `Mode_Retrieve_Wipe` |
| G2 | id parsing accepts `abc`, `0x5`, `4294967296` as entry 0 | T7.2 | strict digit/range check |
| G3 | unterminated name prints into the next field | T7.6 | `%.*s` or force NUL after decrypt |
| G4 | `Commit` returns success without verifying the write | T5.7 | re-validate after writing |
| G5 | `Load` doesn't verify the master key | T5.5 | Stage C: compare `hash_mk` first |
| G6 | RX drops a line typed before the previous is consumed; overflow may leave a stale byte | T6.5/T6.6 | circular RX DMA, clear ORE |
| G7 | first press within 200 ms of boot ignored | T9.6 | init timestamp |
| G8 | flash driver ignores `FLASH->SR` errors | T3.1 | check and return status |
| G9 | menu accepts any line starting with `1`/`2`/`3` | T8.5 | compare the whole line |

## Not covered yet
`FIRST_MEET`/`MK_AUTH` (Stage C), `CHANGE_MK_MODE` and AES are not
implemented, so there is nothing to test. When Stage C
lands, add: wrong/right master key, panic → back to the key prompt, and wiping
`input_mk` in RAM.

---

# L11 — GENERATE_MODE and ADC (DMA)

**Status of this section:** the state machine, the debiasing and the health tests were run on the host against stubbed drivers (every prompt, bad input, overwrite, cancel, panic before commit, entropy fault, failed-verify and a chi-square uniformity check on a simulated 83/17 source). The register values below are computed from RM0383 and the device header, and **have not been observed on the board yet**. Wiring: NTC divider on PA0, LDR divider on PA1.

## T11.1 ADC and DMA registers after init
**Steps** halt at the menu, then read the registers.
```
print/x *(unsigned int*)0x40023830 & 0x00400000     # RCC_AHB1ENR: DMA2EN
print/x *(unsigned int*)0x40012010 & 0x3f            # ADC1_SMPR2: SMP0, SMP1
print/x *(unsigned int*)0x40012008                   # ADC1_CR2
print/x *(unsigned int*)0x40026410                   # DMA2_S0CR
print/x *(unsigned int*)0x40026418                   # DMA2_S0PAR
print/x *(unsigned char*)0xE000E438                  # NVIC IPR for DMA2_Stream0 (IRQ 56)
```
**Expect** `0x400000`, `0x12` (28 cycles on both channels), `0x1` (ADON only, idle), `0x2c10` (MINC, PSIZE and MSIZE 16-bit, TCIE, channel 0, stream disabled), `0x4001204c` (address of `ADC1->DR`), `0x30` (priority 3).
**If not** → `ADC_Drv_Init` in `adc_drv.c`. A priority lower than `0x30` breaks the rule that the panic button and UART outrank the ADC.

## T11.2 Serial flow and input validation
Run `python3 tools/hw_test.py` (T10.1); its generate checks cover: cancel with an empty line, `xyz` rejected as classes, length `32` rejected, a 20-character save at id 30, the entry listed by RETRIEVE_MODE, older entries intact, and retrieve returning exactly the password that was shown.
By hand also try: id `abc`, `31`, `-1` (each `Invalid id`), a 20-character name (`Name must be 1-15`), length `0`, and an id already in use with `n` (returns to the id prompt, nothing written) and with `y`.

## T11.3 Uniformity of the output
**Steps** generate digits-only (`d`), length 31, about 30 times at different ids, and count the digits.
**Expect** all ten digits present with no digit dominating (over 900 characters, each about 90 times; a digit under 50 or over 130 is suspicious).
**If not** → bias in the raw source that debiasing did not remove: re-measure with `tools/` capture scripts on the `adc-entropy` branch, and revisit the 28-cycle sample time.

## T11.4 The save is encrypted at rest and toggles the partition (priority #1 and #2)
**Steps** before saving, note the active sector and version:
```
call (void)Partition_Store_Init()
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
```
Save one password from the menu, halt, repeat the three lines.
**Expect** the sector flips (2 to 3 or 3 to 2), the version is one higher, and `hash_mk` in the header is unchanged. Then search the new sector for the generated password with the T5.3 method, with its positive control.
**Expect** not found in flash.

## T11.5 Reset keeps the new entry
Reset the board, open RETRIEVE_MODE, ask for the saved id. **Expect** the same password as was shown.

## T11.6 Panic during GENERATE_MODE
**Steps** enter mode 2, get to the length prompt, press PA10.
**Expect** the `MODE SELECTION` menu at once (the button forces the state from the ISR). Halt and check `'mode_generate.c'::s_table`, `'mode_generate.c'::s_pwd` and `'mode_generate.c'::s_msg` are all zero, and that the active sector and version are unchanged (nothing was written). Repeat pressing PA10 while the save is running, which is the erase, a second or so of silence after the length: the press is served just after the commit.
**Expect** menu, RAM wiped, and either the old data or a complete new partition, never a half-written one (compare T5.7).

## T11.6b The TOGGLE_PARTITION state
**Steps** save a password from the menu and watch the states: set a breakpoint on `Mode_Generate_Commit` and run the save.
**Expect** it is reached only after the length is entered and sampling finished (`g_state` is `APP_STATE_TOGGLE_PARTITION`, value 5). Before it, `'mode_generate.c'::s_pwd` already holds the password, the active sector and version are still the old ones, and after `continue` the new partition is active. A cancel (empty line) or an entropy fault must never reach this breakpoint.

## T11.7 Disconnected or stuck sensor writes nothing
**Steps** tie PA0 or PA1 to GND or 3V3 with a jumper and generate.
**Expect** `ENTROPY SOURCE FAULT - nothing was saved.` and an unchanged version. Remove the jumper and generate again to see it recover.
A false alarm is also possible with a healthy sensor (the polling build reported 1 fault in about 100000 generated characters); repeat once before suspecting the wiring.
**If instead a password is saved** → the health test is not seeing the stuck value: check `RNG_Health_Check` is fed every sample in `Entropy_Pool_Absorb`.

## T11.8 Timing and interrupts stay healthy
Time a 31-character save (expect well under 3 s including the erase). During a save, type a character on the terminal: it must not corrupt the next prompt, since RX DMA keeps running while the commit masks interrupts.
