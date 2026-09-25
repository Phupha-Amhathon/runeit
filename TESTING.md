# RUNEIT — hardware test guide (bottom-up, command by command)

> **Note (Stage C).** This guide was written for the Stage B firmware and
> has been verified against it. Since then the hardcoded key, the auto-seed
> and the 44-byte partition header were replaced by real authorization
> (`FIRST_MEET`/`MK_AUTH`, PBKDF2, 64-byte header, `Session_*`). Still valid
> as written: S (except that `$mk`/`$mkl` in S2 point at `s_hardcoded_mk`,
> which no longer exists — for L4 put any 14 key bytes into `$buf2` and use
> that), L1, L2, L3, L4 (SHA/XOR). **L6 RX tests are out of date too:** a received line now
> completes only when an Enter (CR/LF) arrives, not at the first pause (T6.3–T6.6 expectations
> about IDLE-only completion, `s_rx_len` and the 'second line lost' case change). **Out of date until
> regenerated:** L5 (every constant, and `Partition_Store_Commit/Load` now take
> a derived key + salt/auth), L7 (tables are now loaded through a session, not
> a hardcoded key), L8, L9, and `tools/hw_test.py` steps that assume the seeded
> `example.com` table. **Part 2 at the end of this file** (`# Part 2 — Stage C`)
> is the guide for the current firmware: first setup, login, change of master
> key, partition integrity, and the panic button in every mode. The PC suite
> `tests/host/run.sh` covers Stage C as well.

Everything you need is in this file: every section says **what to type**,
**why** the test exists, **what you should see**, and **what it means and what to
fix** when you see something else. You never have to open a script to find
instructions. The only files used besides this one are the binary mock tables
in `tools/mock/` (loaded with a single `restore` command; create them once with
`python3 tools/mock/gen_mock.py`, they are not stored in git).

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

(The `.bin` files are ignored by git: after cloning run `python3 tools/mock/gen_mock.py` once to create them.)

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
| `2` | `Not implemented yet.` then the menu again | same |
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
It expects the *default* 2-entry table, so it must run on a freshly seeded board. **Expect** `8/8 checks passed`. It is only a wrapper around T8.1/T8.5/T7.x; if it fails, the failing line names the layer.

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
`GENERATE_MODE`, real ADC sampling and AES are not implemented, so there is
nothing to test. (`FIRST_MEET`/`MK_AUTH`/`CHANGE_MK_MODE` and the panic button
in the Stage C firmware are covered by **Part 2** below.)

---

# Part 2 — Stage C hardware tests (login, change of master key, integrity, panic button)

Part 1 tested the firmware that used one fixed key. That firmware is gone. The
current firmware asks the user for a master key. This part tests the new
features.

* First setup, which is where the user chooses the master key.
* Login.
* Changing the master key.
* The two flash partitions (A and B) and their integrity.
* The panic button, in every mode.

Every test has the same layout as in Part 1. It says what the test checks, it
gives the steps, it shows the result you should see, and it says what to do when
the result is different. The tests go from the bottom layer to the top layer. If
a low layer fails, fix it before you run the layers above it.

| Layer | What it tests | What it needs |
|---|---|---|
| C0 | Setup of the board and gdb | Part 1, section S |
| C1 | The key calculation (PBKDF2), and how long it takes on your board | Part 1, L2 and L4 |
| C2 | The flash partitions. Header, encryption, integrity, A/B switching, damage | Part 1, L3, and C1 |
| C3 | The session. Key rules and the login check | C1 and C2 |
| C4 | The application on the terminal. First setup, login, change key, retrieve | C1 to C3, Part 1 L6 |
| C5 | The panic button in every mode and during every step of a flash write. Power loss | C1 to C4 |
| C6 | Damage and wipe. Is any secret left in RAM? What happens when flash is damaged? | C1 to C5 |
| C7 | Progress report, timing table and list of known problems | all |

Your four main questions are answered in these places.

* Panic button in every mode, with the active and the written partition
  recorded, is in **C5**.
* Is the data really wiped when something is damaged, is in **C6**.
* Is a partition intact, is in **C2**.
* Does the application work, is in **C4**.

## How to read this part

* Every test has five parts. **What this test checks** says what the test does
  and why it matters. **Before you start** says what state the board must be
  in. **Steps** are numbered and each step has a command, an explanation and a
  sample of the output. **Result** says what a pass looks like. **If the result
  is different** lists what you might see, the most likely cause and what to do
  next.
* Type the commands in the `(gdb)` window, one line at a time. A line that
  starts with `#` is a comment. Do not type it.
* The sample outputs are examples. Your numbers can be different. In gdb the
  number in front of an answer (`$1`, `$2` and so on) grows every time, so
  ignore it. Addresses and key values are different on every board.
* "Terminal" means the serial terminal window at 115200 baud. "gdb" means the
  debugger window.
* The expected numbers come from a PC. I ran the real firmware code on a PC,
  with the hardware replaced by simple software models, and I checked the
  results with Python. If the board shows something different, the cause is
  usually the hardware, a driver, a setting, or an old program on the board.
  It is usually not a typing mistake in this document.
* **PREDICTED** means that I read the code and I expect this result, but I did
  not run it.
* **KNOWN PROBLEM** means that the firmware does this today and a finished
  product should not. The test shows the problem and the text says how to fix
  it.
* **Nothing in this part has been run on a real board yet.**

## Words used in this part

| Word | Meaning |
|---|---|
| Master key | The password the user types to open the device. It has 8 to 31 normal characters. |
| Login screen | The screen that shows `== LOCKED ==` and asks for the master key. |
| Session | The state "you are logged in". |
| Session key | The key that decrypts the table while you are logged in. It is kept only in RAM and it is never written to flash. |
| Key calculation | A slow calculation (PBKDF2) that turns the master key into keys. It takes seconds on purpose, so that guessing passwords is slow. |
| Salt | 16 bytes stored in flash. They make the key calculation give a different result on every device. |
| Iterations | How many times the slow calculation repeats. The firmware uses 2000. |
| `auth` value | A check value stored in flash. The firmware uses it to see if the typed key is right. It cannot decrypt anything. |
| Partition A and B | Two copies of the data in flash. A is sector 2 (address `0x08008000`). B is sector 3 (address `0x0800C000`). |
| Header | The first 64 bytes of a partition. It holds the magic number, the version, the iterations, the salt, the `auth` value and the CRC. |
| Version | A number in the header. It grows by 1 with every write. Of two valid copies, the one with the higher version is in use. |
| Active partition | The copy the firmware uses now. The other copy is the inactive partition. |
| Commit | Writing a new copy of the data into the inactive partition. |
| CRC | A check number over the header and the data. If even one bit is damaged, the CRC does not match. |
| Plaintext and ciphertext | Readable data and encrypted data. |
| Wipe | Writing zeros over a secret in RAM, so nobody can read it later. |
| Panic button | The button on pin PA10. It wipes the secrets in RAM and goes back to the login screen. |
| Halted | The processor is stopped. gdb can read memory only when the board is halted. Type `interrupt` to halt it and `continue` to run it again. |
| B0 | The place where we stop the program at the start of many tests. It is explained in C0.1. |

## Things to know before you start

1. **Create the test files once.** The `.bin` files in `tools/mock/` are not
   stored in git. Run `python3 tools/mock/gen_mock.py` one time to create them.
   In gdb, first type `cd /home/scenario001/STM32CubeIDE/workspace_2.2.0/runeit`
   (your project folder). Then the `restore tools/mock/...` commands can find
   the files.
2. **The key calculation is slow.** One login takes about 4 seconds in the Debug
   build. Every login, every first setup and every key change waits this long.
   While the terminal shows `Checking...`, `Deriving...` or `Re-encrypting...`,
   **do not type**. The processor is busy and it can lose your characters.
   Writing to flash also stops the processor for some hundreds of milliseconds.
3. **`Session_Save` is not in the program on the board.** Nothing calls it yet,
   so the linker removes it. It will be there after the GENERATE_MODE branch is
   merged. When a test needs "save this table with the session key", it calls
   `Partition_Store_Commit` with the key of the open session. This is exactly
   what `Session_Save` does. The function `memcmp` is also missing, so we
   compare data with CRC numbers.
4. **Use the Debug build.** Some tests stop the program inside small internal
   functions such as `OpenIfCurrent` and `ReadSlot`. gdb can do this only in the
   Debug build, which has no optimisation. In breakpoint conditions we use the
   names of the function parameters, for example `address` and `sector`.
5. **You can press the panic button from gdb.** The button sets bit 10 of a
   register named `EXTI_SWIER`. If you write the same bit from gdb, the
   processor behaves as if the button was pressed. The commands are
   `set {unsigned int}0x40013C10 = 0x400` and then `continue`. We use this to
   press the button at an exact moment, for example when a breakpoint is
   reached. The processor can run a few more instructions before it reacts. Two
   presses within 200 ms count as one press.
6. **A reset does not clear RAM.** Old keys can stay in the stack area after a
   reset. Before you search RAM for secrets, clear the unused RAM as shown in
   C0.3, or switch the board off and on.
7. **`find` and text.** In gdb, `find` with a quoted text also searches for the
   invisible zero byte at the end of the text. That misses most matches. In this
   document we always search with a list of bytes, for example
   `'v','a','l','i','d'`.

## Test data

### Master keys and files

These files are made by `tools/mock/gen_mock.py`.

| File | Content |
|---|---|
| `mk_a.bin` | The text `validpassword1` (14 characters). We call this key **A**. |
| `mk_b.bin` | The text `newpassword22` (13 characters). We call this key **B**. |
| `salt_a.bin` and `salt_b.bin` | 16 fixed bytes each. The bytes are `(i*17+3)` and `(i*29+101)`. |
| `mk_long.bin` | 40 normal characters, for the length tests. |
| `table_full.bin` | The password table from Part 1, with 31 full entries. |

The number of iterations is **2000**, which is `0x7d0` in hex.

### Results of the key calculation

These are the PBKDF2 test values. The password is `password` and the salt is
`salt`. The result has 32 bytes.

| Iterations | Result |
|---|---|
| 1 | `120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b` |
| 2 | `ae4d0c95af6b46d32d0adff928f06dd02a303f8ef3c251dfd6e2d85a95474c43` |

These are the two keys made from a master key and a salt. The `auth` value is
stored in flash. The `enc` value is the key that encrypts the table and it is
never stored.

| Value | Result | CRC of these 32 bytes |
|---|---|---|
| Key A, `auth` | `c999d66aa74285578baf3f646286fd3a283475415bea461ff0bda418a49885f4` | `0xd830889e` |
| Key A, `enc` | `cc6abcb45ffcba4f80fc1b03b90f9c6fe3eb4698a52923a59f125e9bf4402eba` | `0xa65e6f2d` |
| Key B, `auth` | `bb1ba52910e402c46986315d2f03c76496af3b607b8a43375ba2458e59699ff0` | `0xc83b8b8a` |
| Key B, `enc` | `ceded5a59e7a6d402e50bf5f5a93f9f333a8d035733593bdb1b47622d886a16e` | not needed |

The CRC of `salt_a` (16 bytes) is `0xc932e854`. The CRC of the full table
(`table_full`) is `0x60085d29`. The CRC of the same table when entry 0 starts
with the letter `Z` is `0x1d72cce5`. We call that table **table_Z**.

### The partition header (64 bytes)

| Offset | Field | Address in A |
|---|---|---|
| +0 | `magic`, which is `0x52554e32` (the text "RUN2") | `0x08008000` |
| +4 | `version` | `0x08008004` |
| +8 | `kdf_iter`, the number of iterations | `0x08008008` |
| +12 | `salt` (16 bytes) | `0x0800800C` |
| +28 | `auth` (32 bytes) | `0x0800801C` |
| +60 | `crc32`, over bytes 0 to 59 and the 1488 table bytes | `0x0800803C` |
| +64 | encrypted table (1488 bytes) | `0x08008040` |

For partition B, add `0x4000` to each address. The header of B starts at
`0x0800C000`.

### Three writes used in C2

In C2 we write the partitions ourselves with fixed keys. Then the results are
the same on every board.

| Write | Data | Goes to | Header as 16 words (`x/16xw`) |
|---|---|---|---|
| 1 | `table_full`, key A | A, version 1 | `52554e32 00000001 000007d0 36251403 7a695847 bead9c8b 02f1e0cf 6ad699c9 578542a7 643faf8b 3afd8662 41753428 1f46ea5b 18a4bdf0 f48598a4 99cd22a4` |
| 2 | table_Z, key A | B, version 2 | `52554e32 00000002 000007d0 36251403 7a695847 bead9c8b 02f1e0cf 6ad699c9 578542a7 643faf8b 3afd8662 41753428 1f46ea5b 18a4bdf0 f48598a4 ef084ab1` |
| 3 | table_Z, **key B** | A, version 3 | `52554e32 00000003 000007d0 bc9f8265 3013f6d9 a4876a4d 18fbdec1 29a51bbb c402e410 5d318669 64c7032f 603baf96 37438a7b 8e45a25b f09f6959 f5e62408` |

More numbers for these writes.

| Item | Value |
|---|---|
| First 16 bytes of the encrypted table of write 1 (at `0x08008040`) | `83d6b3d29e3d2dd2ce745fbeae843630` |
| Last 16 bytes of the encrypted table of write 1 (at `0x08008600`) | `f6a6fdf6d66d9f149b88fc27e673646d` |
| CRC of the 1488 encrypted bytes of write 1 | `0xabfa007` |
| First 16 encrypted bytes of write 2 (at `0x0800C040`) and of write 3 (at `0x08008040`) | `b1a7138024ca277173074a7cf1ac3d44` and `48bd2bcf7b8ceb35b2c4894bc1ad8159` |
| CRC of a blank (erased) 16 KB sector | `0x34132f69` |
| CRC of sector A after write 1, of sector B after write 2, of sector A after write 3 | `0x20ae075f`, `0x382f0110`, `0xa9922a39` |
| Table CRC after decrypting write 1 with the right key, and with the wrong key (B) | `0x60085d29` and `0x4a58e448` |
| Table CRC after decrypting write 3 with key B, and with the old key A | `0x1d72cce5` and `0x478fa52f` |

### Master keys you type on the terminal

Use `validpassword1` as key A and `newpassword22` as key B. These other strings
test the key rules.

| String | Characters | Should be |
|---|---|---|
| `abcdefg` | 7 | too short |
| `abcdefgh` | 8 | accepted |
| `abcdefghijklmnopqrstuvwxyz01234` | 31 | accepted |
| `abcdefghijklmnopqrstuvwxyz012345` | 32 | too long |
| `pässword123` (with the accent) | 11 | refused, because it is not plain ASCII |
| `my long pass 1` | 14 | accepted, because spaces are allowed |

---

# C0 — Setup

This section explains how to start the board for a test, how to prepare gdb, and
what the most common commands do. The tests repeat the commands they need. This
section explains them in more detail, so read it once.

## C0.1 Start the board at B0

**B0** means that the program has started and all the drivers are ready, but the
application loop has not begun yet. The board is halted at the first line of
`App_Run`. Nothing else uses the flash or the UART at this point, so you can
test one thing at a time.

```
tbreak App_Run
monitor reset
continue
```

* `tbreak App_Run` sets a one-time breakpoint at the start of `App_Run`.
* `monitor reset` resets the board. With OpenOCD type `monitor reset halt`
  instead. In the STM32CubeIDE debugger you can press the "Restart" button.
* `continue` runs the program until it reaches the breakpoint.

The output looks like this.

```
Temporary breakpoint 1, App_Run () at Src/app/app.c:126
```

The line number can be different.

## C0.2 Helper variables

Type these four commands once for each gdb session. After a new `target remote`
type them again. They give names to RAM areas that the tests use as scratch
space.

```
set $buf  = (unsigned char*)'password_table.c'::s_line_buf
set $buf2 = (unsigned char*)'partition_store.c'::s_commit_scratch
set $tbl  = (unsigned char*)&'mode_retrieve.c'::s_table
set $z    = $buf + 900
```

* `$buf` is a scratch buffer of 1024 bytes. The tests put keys and salts in it.
* `$buf2` is a scratch buffer of 1488 bytes.
* `$tbl` is the place where the firmware keeps the decrypted table (1488 bytes).
* `$z` is a 4-byte place inside `$buf`. The damage tests use it as a source of
  zeros.

These commands print nothing. The quotes in `'file.c'::name` are needed for
variables that belong to one file only. gdb cannot find such variables by name
alone.

## C0.3 What the common commands do

You will see the same groups of commands in many tests. This is what they do.

**Ask the firmware which partition is active.**

```
print 'partition_store.c'::s_have_active
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
```

The output looks like this. In this example partition A is active with version 1.

```
$1 = true
$2 = 2
$3 = 1
```

`true` means the firmware found a valid partition. The sector is 2 for partition
A and 3 for partition B. This is a copy that the firmware keeps in RAM. It is
filled by `Partition_Store_Init`, which runs before the first prompt appears.

**Read the first two words of each partition directly from flash.**

```
x/2xw 0x08008000
x/2xw 0x0800C000
```

The output looks like this. In this example A has version 1 and B is erased.

```
0x8008000:	0x52554e32	0x00000001
0x800c000:	0xffffffff	0xffffffff
```

The first number is the magic value `0x52554e32`. The second number is the
version. The value `0xffffffff` means that the flash is erased. This command
reads flash itself. It does not depend on what the firmware remembers.

**Make a fingerprint of a whole sector.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
```

The first line gives the CRC of the whole 16 KB sector A. The second line does
the same for sector B. The output looks like this.

```
$1 = 0x20ae075f
$2 = 0x34132f69
```

If one byte in a sector changes, its number changes. So you can run these
commands before and after a test and see exactly which sector was written. The
number `0x34132f69` belongs to a blank sector.

**Check that a partition is intact.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
```

The first line calculates the CRC over the first 60 header bytes and the 1488
table bytes of partition A. The second line reads the CRC that is stored in the
header. The output looks like this.

```
$1 = 0x99cd22a4
0x800803c:	0x99cd22a4
```

If the two numbers are equal, the partition is intact. If they are different,
the partition is damaged or the sector is blank. For partition B use the
addresses `0x0800C000`, `0x0800C040` and `0x0800C03C`.

**Check the decrypted table in RAM.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
```

The answer is `0x60085d29` for the full table, `0x1d72cce5` for table_Z, and
`0xbb034f8e` for a table that is all zeros.

**Check that all secret places are empty.**

```
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
x/8xw 'mode_first_meet.c'::s_first
x/8xw 'mode_change_mk.c'::s_new
print/x (CRC_Drv_Reset(), CRC_Drv_Feed(&'mode_change_mk.c'::s_work, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 1024), CRC_Drv_Result())
x/16xw 'usart_drv.c'::s_rx_buf
```

These eight commands show the following things.

1. Whether a session is open. `0x1` means yes and `0x0` means no.
2. The session key (32 bytes).
3. The master key that was typed first during first setup.
4. The new master key that was typed during a key change.
5. The working copy of the table that is used during a key change.
6. The decrypted table.
7. The line buffer that the firmware uses to print passwords.
8. The UART receive buffer, which holds the last thing that was typed.

Everything is **wiped** when command 1 shows `0x0`, the commands 2, 3, 4 and 8
show only `0x00000000`, the commands 5 and 6 show `0xbb034f8e`, and command 7
shows `0x8b0a5208`. That last number is the CRC of 1024 zero bytes.

**Press the panic button from gdb.** The board must be halted first. Type
`interrupt` if it is running.

```
set {unsigned int}0x40013C10 = 0x400
continue
```

The first line sets the "button pressed" flag. The second line lets the
processor run, and it reacts to the flag at once. To press the button at an
exact place, first set a breakpoint and type `continue`. Wait until gdb stops at
the breakpoint. Then type these two lines.

**Clear the unused RAM.** Use this only at B0, where the stack is still small.

```
call (void*)memset(&_ebss, 0, (unsigned int)$sp - 256 - (unsigned int)&_ebss)
```

This fills the unused RAM between the variables and the stack with zeros. The
output looks like `$1 = (void *) 0x200018c4`.

**Search all of RAM for a pattern.** This example looks for the text
`validpass`.

```
find /b 0x20000000, +0x20000, 'v','a','l','i','d','p','a','s','s'
```

This searches all 128 KB of RAM and takes a few seconds. The answer is either
`Pattern not found.` or a list of addresses followed by `N patterns found.`.
To find out which variable owns an address, type `info symbol` and then the
address, without the `<` and `>` signs. The answer names the variable, for
example `s_first + 3`. An address just below `0x20020000` that has no name is in
the stack.

## C0.4 Situations that many tests start from

Many tests need the board to be in a certain situation. Each test describes its
situation again in "Before you start". This table only lists the four common
ones, so you know what they mean.

| Situation | What it means |
|---|---|
| Blank device | Both partitions are erased. The terminal shows `== FIRST TIME SETUP ==`. |
| Logged in, empty table | Partition A has version 1 and holds an empty table with key A. You are logged in. |
| Logged in, full table | Partition B has version 2 and holds the full table with key A. Partition A still has version 1. You are logged in. |
| Key changed | Partition A has version 3 and holds the full table with key B. Partition B has version 2 with key A. |

---

# C1 — The key calculation on the board (`hmac_sha256` and `kdf`)

The master key is only as safe as this calculation. The code was tested on a PC
against published test values. In this layer you check that the same code gives
the same answers on the Cortex-M4 processor. You also measure how long one
calculation takes on your board. That time decides how many iterations the
firmware should use.

## T-C1.1 HMAC and PBKDF2 give the known answers

### What this test checks

PBKDF2 is the standard slow calculation that the design uses. It is built from
HMAC. In this test you give the board a few inputs with a known answer. You
also check two edge cases. An iteration count of 0 must behave like a count of 1,
and a salt that is too long must be refused, so that no buffer overflows.

### Before you start

* The board is halted at B0 (see C0.1).
* You typed the four helper variables from C0.2.

### Steps

**Step 1. Put the text `password` and the text `salt` into RAM.**

```
set {unsigned int}($buf2)      = 0x73736170
set {unsigned int}($buf2+4)    = 0x64726f77
set {unsigned int}($buf2+16)   = 0x746c6173
```

The first two commands write the eight letters of `password` (`pass` and `word`).
The third command writes the four letters of `salt`. A word is written with the
last letter first, because the processor stores the smallest byte first. gdb
prints nothing.

**Step 2. Run PBKDF2 with 1 iteration and look at the result.**

```
call (int)Kdf_Pbkdf2Sha256($buf2, 8, $buf2+16, 4, 1, 0, $buf2+64)
x/32xb $buf2+64
```

The arguments are the password, its length (8), the salt, its length (4), the
number of iterations (1), a cancel function (0 means none), and the place for the
result. The first line returns 1 when it works. The second line prints the 32
result bytes. The output looks like this.

```
$1 = 1
0x20000a80:	0x12	0x0f	0xb6	0xcf	0xfc	0xf8	0xb3	0x2c
0x20000a88:	0x43	0xe7	0x22	0x52	0x56	0xc4	0xf8	0x37
0x20000a90:	0xa8	0x65	0x48	0xc9	0x2c	0xcc	0x35	0x48
0x20000a98:	0x08	0x05	0x98	0x7c	0xb7	0x0b	0xe1	0x7b
```

**Step 3. Run it with 2 iterations.**

```
call (int)Kdf_Pbkdf2Sha256($buf2, 8, $buf2+16, 4, 2, 0, $buf2+64)
x/32xb $buf2+64
```

With 2 iterations the result changes. This proves that the rounds are chained
together correctly. The output looks like this.

```
$2 = 1
0x20000a80:	0xae	0x4d	0x0c	0x95	0xaf	0x6b	0x46	0xd3
0x20000a88:	0x2d	0x0a	0xdf	0xf9	0x28	0xf0	0x6d	0xd0
0x20000a90:	0x2a	0x30	0x3f	0x8e	0xf3	0xc2	0x51	0xdf
0x20000a98:	0xd6	0xe2	0xd8	0x5a	0x95	0x47	0x4c	0x43
```

**Step 4. Run it with 0 iterations.**

```
call (int)Kdf_Pbkdf2Sha256($buf2, 8, $buf2+16, 4, 0, 0, $buf2+64)
x/8xb $buf2+64
```

The design says that 0 iterations means "one round". So the first 8 bytes must be
the same as in step 2 of the 1-iteration result.

```
$3 = 1
0x20000a80:	0x12	0x0f	0xb6	0xcf	0xfc	0xf8	0xb3	0x2c
```

**Step 5. Give it a salt that is too long.**

```
set {unsigned int}($buf2+96) = 0xEEEEEEEE
call (int)Kdf_Pbkdf2Sha256($buf2, 8, $buf2+16, 17, 1, 0, $buf2+96)
x/1xw $buf2+96
```

The salt length is 17 but the salt field holds only 16 bytes. The function must
refuse. The first line marks the result place with `0xEEEEEEEE`. The output
looks like this.

```
$4 = 0
0x20000ae0:	0xeeeeeeee
```

**Step 6. Test HMAC alone with an official test case.**

This is case 2 of RFC 4231. The key is `Jefe` and the message is
`what do ya want for nothing?`. First put the key and the message in RAM.

```
set {unsigned int}($buf2+128) = 0x6566654a
set {unsigned int}($buf2+160) = 0x74616877
set {unsigned int}($buf2+164) = 0x206f6420
set {unsigned int}($buf2+168) = 0x77206179
set {unsigned int}($buf2+172) = 0x2074616e
set {unsigned int}($buf2+176) = 0x20726f66
set {unsigned int}($buf2+180) = 0x68746f6e
set {unsigned int}($buf2+184) = 0x3f676e69
```

Then run HMAC with a 4-byte key and a 28-byte message, and look at the result.

```
call (void)HmacSha256($buf2+128, 4, $buf2+160, 28, $buf2+224)
x/32xb $buf2+224
```

The output looks like this.

```
0x20000b40:	0x5b	0xdc	0xc1	0x46	0xbf	0x60	0x75	0x4e
0x20000b48:	0x6a	0x04	0x24	0x26	0x08	0x95	0x75	0xc7
0x20000b50:	0x5a	0x00	0x3f	0x08	0x9d	0x27	0x39	0x83
0x20000b58:	0x9d	0xec	0x58	0xb9	0x64	0xec	0x38	0x43
```

### Result

The test **passes** when all of these are true.

* Step 2 gives the bytes `12 0f b6 cf ...` and step 3 gives `ae 4d 0c 95 ...`.
* Step 4 gives `12 0f b6 cf fc f8 b3 2c`.
* Step 5 returns 0 and the marker `0xeeeeeeee` is unchanged.
* Step 6 gives `5b dc c1 46 ... 64 ec 38 43`.

### If the result is different

* **Every answer is wrong.** The crypto files on the board are not the ones that
  were tested. The program on the board is probably old. Go back to Part 1,
  section S3, and load the program again.
* **HMAC is right but step 3 is wrong.** The chaining of the rounds in
  `Kdf_Pbkdf2Sha256` has a problem. Look at how `u` and `t` are updated in each
  round in `Src/crypto/kdf.c`.
* **Step 4 differs from step 2.** A count of 0 is not handled as one round.
* **Step 5 returns 1 or the marker changed.** The check `salt_len > KDF_SALT_LEN`
  is missing. A long salt can overflow a buffer.
* **The values look random and change from run to run.** The stack may be
  overflowing. See test T-C4.11.

## T-C1.2 The two keys have the expected values

### What this test checks

`Kdf_DeriveKeys` makes two 32-byte values from a master key and a salt. The
`auth` value is stored in flash. The `enc` value encrypts the table. These two
values must have the documented values, and they must be different from each
other. If `enc` were equal to `auth`, then the value stored in flash would give
away the encryption key.

### Before you start

* The board is halted at B0.
* The helper variables from C0.2 are typed.
* This test takes some seconds for each calculation.

### Steps

**Step 1. Load key A and salt A into RAM.**

```
restore tools/mock/mk_a.bin binary $buf
restore tools/mock/salt_a.bin binary $buf+64
```

The first command puts the 14 characters of `validpassword1` at `$buf`. The
second command puts the salt at `$buf+64`. gdb prints a line such as
`Restoring binary file tools/mock/mk_a.bin into memory (0x... to 0x...)`.

**Step 2. Make the keys for key A.**

```
call (int)Kdf_DeriveKeys($buf, 14, $buf+64, 2000, 0, $buf+128, $buf+160)
```

The arguments are the master key and its length, the salt, the iterations (2000),
the cancel function (none), the place for `auth` (`$buf+128`), and the place for
`enc` (`$buf+160`). It returns 1 when it works. This takes some seconds.

```
$1 = 1
```

**Step 3. Check the two results with CRC numbers.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf+128, 32), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf+160, 32), CRC_Drv_Result())
```

The output looks like this.

```
$2 = 0xd830889e
$3 = 0xa65e6f2d
```

The first number is the CRC of `auth` and the second is the CRC of `enc`.

**Step 4. Look at the bytes.**

```
x/32xb $buf+128
x/32xb $buf+160
```

`auth` must start with `c9 99 d6 6a a7 42 85 57` and `enc` must start with
`cc 6a bc b4 5f fc ba 4f`. The full values are in the test data section.

**Step 5. Do the same for key B.**

```
restore tools/mock/mk_b.bin binary $buf+256
restore tools/mock/salt_b.bin binary $buf+320
call (int)Kdf_DeriveKeys($buf+256, 13, $buf+320, 2000, 0, $buf+384, $buf+416)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf+384, 32), CRC_Drv_Result())
```

The last line must print `0xc83b8b8a`. This is the CRC of the `auth` value of
key B.

Keep the board and gdb as they are. Tests C2 and C3 use these values. Key A has
its `auth` at `$buf+128` and its `enc` at `$buf+160`, and its salt is at
`$buf+64`. Key B has its `auth` at `$buf+384`, its `enc` at `$buf+416`, and its
salt is at `$buf+320`.

### Result

The test **passes** when both calls return 1, the CRC of `auth` A is
`0xd830889e`, the CRC of `enc` A is `0xa65e6f2d`, and the CRC of `auth` B is
`0xc83b8b8a`.

### If the result is different

* **The CRC numbers are wrong but test T-C1.1 passed.** The hash functions are
  fine, so the problem is in how the two keys are made. The text labels
  `RUNEIT-auth-v1` and `RUNEIT-enc-v1` in `Src/crypto/kdf.c` may differ. The
  iteration count or the salt length in your call may also differ from the
  firmware.
* **`auth` and `enc` are the same.** Both `HmacSha256` calls use the same label.
  This is serious. Fix it before you continue.

## T-C1.3 How long does one key calculation take?

### What this test checks

Every wrong guess costs an attacker this much time on this processor. Every
login costs the user the same time. The estimate from instruction counts is
about 4 seconds in the Debug build and about 1.4 seconds in an optimised build,
for 2000 iterations at 16 MHz. Only a measurement on your board gives the real
number.

### Before you start

* You finished T-C1.2, so key A and salt A are in RAM.

### Steps

**Step 1. Measure 2000 iterations.**

```
set $t0 = 'systick_drv.c'::s_ms_ticks
call (int)Kdf_DeriveKeys($buf, 14, $buf+64, 2000, 0, $buf+128, $buf+160)
print 'systick_drv.c'::s_ms_ticks - $t0
```

The first line remembers the millisecond counter. The second line runs the
calculation. The third line prints how many milliseconds passed. The output
looks like this.

```
$1 = 1
$2 = 4012
```

**Step 2. Measure 1000 iterations.**

```
set $t0 = 'systick_drv.c'::s_ms_ticks
call (int)Kdf_DeriveKeys($buf, 14, $buf+64, 1000, 0, $buf+192, $buf+224)
print 'systick_drv.c'::s_ms_ticks - $t0
```

The result should be about half of step 1.

### Result

The test **passes** when step 1 shows about 4000 ms (Debug build) or about 1400
ms (optimised build), and step 2 shows about half of that.

Write both numbers in the timing table in C7. Then decide the number of
iterations. A good login time is between 1 and 3 seconds. Use this formula.

```
new iterations = 2000 x wanted milliseconds / measured milliseconds
```

Then change `KDF_ITERATIONS` in `Inc/crypto/kdf.h`. Partitions that are already
written keep their own iteration count in the header, so old data still opens.

### If the result is different

* **The time is ten times too long or too short.** The processor clock is
  probably not 16 MHz. The firmware assumes 16 MHz when it sets up the 1 ms tick.
  If the clock is different, then this number and the times printed on the
  screens are both wrong. Check with a stopwatch on the terminal.
* **Step 2 is not about half of step 1.** Something other than the calculation
  takes most of the time. It can be flash wait states or the debugger.

## T-C1.4 The calculation stops when the panic button is pressed

### What this test checks

When the panic button is pressed during a calculation, the calculation must stop
quickly. The function `KdfCancelled` compares a panic counter with the value that
the calculation started with. In this test you make the two values different, so
the function asks the calculation to stop. The calculation looks at the cancel
function every 32 iterations. So it should stop after about 32 of 2000
iterations, and it must not write its result.

### Before you start

* You finished T-C1.2, so key A and salt A are in RAM.

### Steps

**Step 1. Make the counters different and run a cancelled calculation.**

```
set var 'session.c'::s_op_generation = 5
set {unsigned int}($buf+192) = 0xEEEEEEEE
set $t0 = 'systick_drv.c'::s_ms_ticks
call (int)Kdf_Pbkdf2Sha256($buf, 14, $buf+64, 16, 2000, &'session.c'::KdfCancelled, $buf+192)
print 'systick_drv.c'::s_ms_ticks - $t0
x/1xw $buf+192
```

The first line sets the "start" counter to 5. The real panic counter is 0, so
`KdfCancelled` returns true. The second line puts a marker in the result place.
The next lines measure the time and run the calculation with the cancel function.
The output looks like this.

```
$1 = 0
$2 = 80
0x20000ac0:	0xeeeeeeee
```

The answer 0 means that the calculation was cancelled. It took about 80 ms. The
marker is unchanged, so nothing was written.

**Step 2. Run a short calculation that must finish.**

```
set var 'session.c'::s_op_generation = 0
call (int)Kdf_Pbkdf2Sha256($buf, 14, $buf+64, 16, 64, &'session.c'::KdfCancelled, $buf+192)
```

Now both counters are 0, so the calculation must not be cancelled. It returns 1.

```
$3 = 1
```

### Result

The test **passes** when step 1 returns 0, takes about 2 percent of the time of
T-C1.3 (tens of milliseconds), and leaves the marker `0xeeeeeeee`. Step 2 must
return 1.

### If the result is different

* **Step 1 returns 1 or takes the full time.** The cancel function is not called,
  or it is not called often enough. Look at the constant `KDF_CANCEL_POLL_MASK`
  in `Src/crypto/kdf.c`.
* **The marker changed in step 1.** A cancelled calculation leaked part of a key
  into the caller's buffer. It must write nothing when it is cancelled.
* **Step 2 returns 0.** The two counters are not really equal. Type
  `print 'session.c'::s_generation` and check that it is 0.

## T-C1.5 The compare function has no early exit

### What this test checks

The login compares two 32-byte `auth` values. A normal compare stops at the first
byte that differs. That leaks how many bytes were right, because a longer compare
takes more time. `Kdf_ConstTimeEqual` must look at every byte. You cannot measure
time with gdb, so this test checks that the function answers correctly. Read the
loop in `Src/crypto/kdf.c` to confirm that it has no early exit.

### Before you start

* You finished T-C1.2, so `auth` A is at `$buf+128` and `enc` A is at `$buf+160`.

### Steps

**Step 1. Compare a value with itself, and with a different value.**

```
call (int)Kdf_ConstTimeEqual($buf+128, $buf+128, 32)
call (int)Kdf_ConstTimeEqual($buf+128, $buf+160, 32)
```

The first answer must be 1 (equal). The second must be 0 (`auth` and `enc` are
different).

**Step 2. Make a copy that differs only in the last byte.**

```
call (void*)memcpy($buf+192, $buf+128, 32)
set var $buf[223] = $buf[223] ^ 1
call (int)Kdf_ConstTimeEqual($buf+128, $buf+192, 32)
call (int)Kdf_ConstTimeEqual($buf+128, $buf+192, 31)
call (int)Kdf_ConstTimeEqual($buf+128, $buf+192, 0)
```

The first line copies `auth` A. The second line flips one bit in the last byte
of the copy. Then three compares run. The first compares all 32 bytes, the second
compares only 31 bytes, and the third compares 0 bytes.

The answers must be as follows.

```
$3 = 0
$4 = 1
$5 = 1
```

### Result

The test **passes** when the answers are 1, 0, 0, 1 and 1 in this order.

### If the result is different

* **The compare with 32 bytes says "equal" for a copy that differs in the last
  byte.** The loop stops early or does not look at the last byte.

---

# C2 — Flash partitions (`partition_store`)

The partitions hold everything the user owns. Each partition has a header that
describes itself. The header holds the salt, the number of iterations and the
`auth` value. It also holds a CRC over the header and the data. The data is
encrypted with a key that is not stored anywhere. Two partitions exist so that a
new write never damages the copy that is in use. This layer checks all of that.

Most tests in this layer **erase both sectors**. Do not use them on a device
with data that you want to keep.

Every test in this layer needs the key values from test T-C1.2 in RAM. If you
reset the board or restarted gdb after C1, run T-C1.2 again first. The tests use
these places in RAM.

| Place | Content |
|---|---|
| `$buf` | master key A (14 bytes) |
| `$buf+64` | salt A |
| `$buf+128` | `auth` of key A |
| `$buf+160` | `enc` of key A |
| `$buf+320` | salt B |
| `$buf+384` | `auth` of key B |
| `$buf+416` | `enc` of key B |

## T-C2.1 A blank device has no partition

### What this test checks

A new device has erased flash. The firmware must see that no valid partition
exists. Then the application can offer the first setup. An old partition from the
Stage B firmware has a different magic value (`RUN1`) and must also be refused.

### Before you start

* The board is halted at B0.
* Helper variables from C0.2 are typed.

### Steps

**Step 1. Erase both sectors.**

```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
```

Each command erases one 16 KB sector. Sector 2 is partition A and sector 3 is
partition B. Each command takes a moment and prints nothing.

**Step 2. Put the full table into RAM.**

```
restore tools/mock/table_full.bin binary $tbl
```

The later tests write this table.

**Step 3. Let the firmware look at the flash.**

```
call (void)Partition_Store_Init()
```

This is the function that the firmware runs at start. It reads both headers and
decides which partition is active.

**Step 4. Ask what the firmware found.**

```
print 'partition_store.c'::s_have_active
print Partition_Store_Active()
```

The output looks like this.

```
$1 = false
$2 = (const partition_info_t *) 0x0
```

### Result

The test **passes** when `s_have_active` is `false` and `Partition_Store_Active()`
returns `0x0`.

### If the result is different

* **`s_have_active` is `true`.** A blank sector is accepted as valid. The check
  of the magic value is missing or wrong. Look at `ReadSlot` in
  `Src/app/partition_store.c`.
* **An old Stage B partition is accepted.** The magic value must be `0x52554e32`
  (`RUN2`). A device with the old format must start again with the first setup.

## T-C2.2 The first write. Header and encrypted table have the exact expected bytes

### What this test checks

After a write, the flash must hold the documented 64-byte header followed by the
**encrypted** table. The encrypted bytes must be exactly the known-good bytes for
this table, this key and this version. They must not be the plaintext that you
put in RAM.

### Before you start

* You finished T-C2.1. Both sectors are erased and `$tbl` holds the full table.

### Steps

**Step 1. Write the table with key A.**

```
call (int)Partition_Store_Commit($buf+160, $buf+64, 2000, $buf+128, &'mode_retrieve.c'::s_table)
```

The five arguments are the encryption key (`enc` of key A), the salt, the number
of iterations, the `auth` value, and the table. The function picks the inactive
partition. The device is blank, so it uses A. It returns 1 when the write worked.

```
$1 = 1
```

**Step 2. Ask which partition is active.**

```
print 'partition_store.c'::s_have_active
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
```

The output looks like this.

```
$2 = true
$3 = 2
$4 = 1
```

**Step 3. Read the header from flash.**

```
x/16xw 0x08008000
```

The output must look exactly like this. The first three words are the magic
value, the version 1, and the iterations `0x7d0`. The next four words are the
salt. The next eight words are the `auth` value. The last word is the CRC.

```
0x8008000:	0x52554e32	0x00000001	0x000007d0	0x36251403
0x8008010:	0x7a695847	0xbead9c8b	0x02f1e0cf	0x6ad699c9
0x8008020:	0x578542a7	0x643faf8b	0x3afd8662	0x41753428
0x8008030:	0x1f46ea5b	0x18a4bdf0	0xf48598a4	0x99cd22a4
```

**Step 4. Read the first and the last bytes of the encrypted table.**

```
x/16xb 0x08008040
x/16xb 0x08008600
```

The table starts at `0x08008040` and its last 16 bytes are at `0x08008600`. The
output must look like this.

```
0x8008040:	0x83	0xd6	0xb3	0xd2	0x9e	0x3d	0x2d	0xd2
0x8008048:	0xce	0x74	0x5f	0xbe	0xae	0x84	0x36	0x30
0x8008600:	0xf6	0xa6	0xfd	0xf6	0xd6	0x6d	0x9f	0x14
0x8008608:	0x9b	0x88	0xfc	0x27	0xe6	0x73	0x64	0x6d
```

**Step 5. Calculate the CRC of all 1488 encrypted bytes.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
```

The answer must be `$5 = 0xabfa007`.

**Step 6. Check that partition B is still blank.**

```
x/4xw 0x0800C000
```

The output must show four times `0xffffffff`.

**Step 7. Fingerprint both sectors.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
```

The answers must be `0x20ae075f` for A and `0x34132f69` for B (blank).

**Step 8. Check that the partition is intact.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
```

Both numbers must be `0x99cd22a4`.

### Result

The test **passes** when every output in steps 2 to 8 matches the sample above.

### If the result is different

* **The table bytes start with `53 30 30 61`.** These are the letters `S00a`. The
  table was written as plaintext. `Partition_Store_Commit` is not encrypting the
  copy that it writes. This is serious.
* **The header is right but the encrypted bytes differ, and they are not
  readable text.** The key or the version number used for the encryption is
  wrong. The version number of the new partition must be used (here 1). Test
  T-C1.2 must pass first.
* **The salt, the iterations or the `auth` words in the header are different.**
  The arguments were given in the wrong order.
* **The CRC in the header differs.** The CRC must cover exactly the first 60 header
  bytes and the 1488 encrypted table bytes. A header of the old size (44 bytes)
  would move everything.
* **Partition B is not blank.** The function wrote to the wrong sector.

## T-C2.3 No secret text is in flash, and the search really works

### What this test checks

Test T-C2.2 shows that the encrypted bytes are the expected ones. This test
looks for secrets in another way. It searches both sectors for the table text, the
master key and the encryption key. None of them may be there. It also checks that
the `auth` value is stored, as it must be. To make sure that the search itself
works, it repeats the search in RAM, where the text must be found.

### Before you start

* You finished T-C2.2. Partition A holds the full table with key A.
* Key A, `auth` A and `enc` A are still in RAM at `$buf`, `$buf+128` and `$buf+160`.

### Steps

**Step 1. Search flash for the table text.**

```
find /b 0x08008000, +0x8000, '!','@','#','$','%','^','&','*'
find /b 0x08008000, +0x8000, 'S','0','0','a','b','c','d','e'
find /b 0x08008000, +0x8000, 'p','w','0','0','!','@','#','$'
```

The first line looks for the symbols that every password starts with. The other
two look for the start of a name and of a password. The address range covers both
sectors (32 KB). Each command must print this.

```
Pattern not found.
```

**Step 2. Search flash for the master key and for the encryption key.**

```
find /b 0x08008000, +0x8000, 'v','a','l','i','d','p','a','s','s'
find /b 0x08008000, +0x8000, $buf[160], $buf[161], $buf[162], $buf[163], $buf[164], $buf[165], $buf[166], $buf[167]
```

The first line looks for the start of the master key text. The second line looks
for the first 8 bytes of `enc`, which are taken from RAM. Both must print
`Pattern not found.`

**Step 3. Search flash for the `auth` value.**

```
find /b 0x08008000, +0x8000, $buf[128], $buf[129], $buf[130], $buf[131], $buf[132], $buf[133], $buf[134], $buf[135]
```

This time the value **must** be found, once, in the header. The output looks like
this.

```
0x800801c
1 pattern found.
```

**Step 4. Run the same search in RAM to prove that the search works.**

```
find /b $tbl, +1488, 'S','0','0','a','b','c','d','e'
find /b $buf, +64, 'v','a','l','i','d','p','a','s','s'
```

The first line searches the table in RAM. The second searches the key in RAM.
Each must find exactly one place and print `1 pattern found.`.

### Result

The test **passes** when all of this is true.

* Step 1 and step 2 print `Pattern not found.` for all five searches.
* Step 3 finds the `auth` value once, at `0x0800801c`.
* Step 4 finds one place for each search.

### If the result is different

* **Step 1 or step 2 finds something in flash.** A secret was written to flash in
  readable form. If the encryption key or the master key is found, this is
  serious. Look at what `Partition_Store_Commit` and the calling code write.
* **Step 3 does not find the `auth` value.** The header does not hold it. The
  login could then never work.
* **Step 4 finds nothing.** The data in RAM was overwritten. Run T-C1.2 again and
  type `restore tools/mock/table_full.bin binary $tbl` again.

## T-C2.4 Decrypt gives the original table, and the wrong key gives garbage

### What this test checks

Reading the table back must give exactly the original 1488 bytes. The test also
shows that `Partition_Store_Load` cannot tell a wrong key from the right key. It
returns success and fills the table with garbage. The only protection is that the
session compares the `auth` value first. That is tested in layer C3.

### Before you start

* You finished T-C2.2. Partition A holds the full table with key A.

### Steps

**Step 1. Clear the table in RAM.**

```
restore tools/mock/table_empty.bin binary $tbl
call (void)Partition_Store_Init()
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
```

The table is now all zeros. The answer must be `0xbb034f8e`.

**Step 2. Decrypt with the right key.**

```
call (int)Partition_Store_Load($buf+160, &'mode_retrieve.c'::s_table)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
```

The first line returns 1. The CRC of the table must be `0x60085d29`.

**Step 3. Decrypt with the wrong key.**

```
call (int)Partition_Store_Load($buf+416, &'mode_retrieve.c'::s_table)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
```

This uses the encryption key of key B on data that belongs to key A. The first
line still returns 1. The CRC is `0x4a58e448`, which is the CRC of garbage.

**Step 4. Decrypt with the right key again and look at the last entry.**

```
call (int)Partition_Store_Load($buf+160, &'mode_retrieve.c'::s_table)
print 'mode_retrieve.c'::s_table.entries[30]
```

The output looks like this.

```
$5 = 1
$6 = {name = "S30abcdefghijkl", password = "pw30!@#$%^&*()-_=+[]{};:,.<>/?a"}
```

### Result

The test **passes** when the four CRC numbers are `0xbb034f8e`, `0x60085d29`,
`0x4a58e448` and `0x60085d29` (after step 4), and every load returns 1.

### If the result is different

* **Step 2 does not give `0x60085d29`.** The decryption does not match the
  encryption. The nonce of the keystream is the version of the partition. Check
  test T-C1.2 and test T-C2.2 first.
* **Step 3 returns 0.** `Partition_Store_Load` now checks something that the
  design leaves to the session. That is not wrong, but the test text and layer C3
  must then be updated.

## T-C2.5 A new write goes to the other sector, and the active sector is not touched

### What this test checks

This is the main safety rule of the two-partition design. A write must go to the
**inactive** partition. The active partition must not change at all. Then a power
failure during a write can never destroy the copy that is in use. After the write,
the new partition becomes active with the version one higher.

There is no menu command for this yet, so the test calls
`Partition_Store_Commit` from gdb. This is the same function that first setup,
key change and `Session_Save` use.

### Before you start

* You finished T-C2.2. Partition A is active with version 1.
* Key A and key B values are in RAM (T-C1.2).

### Steps

**Step 1. Change the table a little and write it again with key A. Stop when the sector is erased.**

```
set var 'mode_retrieve.c'::s_table.entries[0].name[0] = 'Z'
break Flash_Drv_EraseSector
call (int)Partition_Store_Commit($buf+160, $buf+64, 2000, $buf+128, &'mode_retrieve.c'::s_table)
```

The first line changes the first letter of entry 0 to `Z`. The table is now
table_Z. The second line sets a breakpoint. The third line starts the write.
gdb stops in `Flash_Drv_EraseSector` and says that the program stopped inside a
function called from gdb.

**Step 2. Look at which sector will be erased and which sector is active.**

```
print sector
print 'partition_store.c'::s_active.sector
```

The output looks like this.

```
$1 = 3
$2 = 2
```

The sector that will be erased (3) is not the active one (2). This is correct.

**Step 3. Remove the breakpoint and let the write finish.**

```
delete
continue
```

gdb may print a message that the called function finished. This is normal.

**Step 4. Look at the result.**

```
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
x/16xw 0x0800C000
```

The active sector is now 3 and the version is 2. The header of B must look like
this.

```
$3 = 3
$4 = 2
0x800c000:	0x52554e32	0x00000002	0x000007d0	0x36251403
0x800c010:	0x7a695847	0xbead9c8b	0x02f1e0cf	0x6ad699c9
0x800c020:	0x578542a7	0x643faf8b	0x3afd8662	0x41753428
0x800c030:	0x1f46ea5b	0x18a4bdf0	0xf48598a4	0xef084ab1
```

**Step 5. Check that sector A did not change.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
```

The output must be `0x20ae075f` for A (the same as in T-C2.2) and `0x382f0110`
for B.

**Step 6. Check that partition B is intact.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
```

Both numbers must be `0xef084ab1`.

**Step 7. Now write the table again, this time with key B. The write must go back to A.**

```
break Flash_Drv_EraseSector
call (int)Partition_Store_Commit($buf+416, $buf+320, 2000, $buf+384, &'mode_retrieve.c'::s_table)
print sector
print 'partition_store.c'::s_active.sector
delete
continue
```

gdb stops in `Flash_Drv_EraseSector` again. The sector to be erased must be 2,
and the active sector is 3.

```
$7 = 2
$8 = 3
```

**Step 8. Look at the result.**

```
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
x/16xw 0x08008000
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
```

The active sector is 2 with version 3. The sector numbers must be `0xa9922a39` for A
and `0x382f0110` for B. B did not change this time. The header of A must look like
this.

```
0x8008000:	0x52554e32	0x00000003	0x000007d0	0xbc9f8265
0x8008010:	0x3013f6d9	0xa4876a4d	0x18fbdec1	0x29a51bbb
0x8008020:	0xc402e410	0x5d318669	0x64c7032f	0x603baf96
0x8008030:	0x37438a7b	0x8e45a25b	0xf09f6959	0xf5e62408
```

**Step 9. Read the table back with key B.**

```
restore tools/mock/table_empty.bin binary $tbl
call (void)Partition_Store_Init()
call (int)Partition_Store_Load($buf+416, &'mode_retrieve.c'::s_table)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
```

The answer must be `0x1d72cce5`, which is the CRC of table_Z.

### Result

The test **passes** when all of these are true.

* At each write, the sector that is erased is not the active sector.
* After write 2 the active partition is B with version 2, and sector A did not
  change.
* After write 3 the active partition is A with version 3, and sector B did not
  change.
* Both partitions are intact and the numbers match the sample outputs.
* The table read with key B has the CRC `0x1d72cce5`.

### If the result is different

* **The sector to be erased is the same as the active sector.** This is a
  serious problem. A power failure at that moment destroys the only valid copy.
  The choice of the target sector in `Partition_Store_Commit` is wrong, or
  `s_have_active` is not up to date. Type `call (void)Partition_Store_Init()`
  and try again.
* **The fingerprint of the active sector changed.** The write touched the copy in
  use.
* **The version did not grow, or the active sector did not change.** The
  function did not switch to the new partition. It only switches when the data
  read back from flash is valid. Run the integrity check of step 6.
* **The table CRC in step 9 is wrong.** The key or the salt was mixed up between
  the writes.

## T-C2.6 Both partitions are intact after a series of writes

### What this test checks

This test creates a known situation and checks the integrity of both partitions.
The situation is used again by tests T-C2.7 to T-C2.9. Partition A holds version
3 with key B and partition B holds version 2 with key A.

### Before you start

* The board is halted at B0.
* Helper variables are typed and the key values from T-C1.2 are in RAM.

### Steps

**Step 1. Erase both sectors and start from a blank device.**

```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
call (void)Partition_Store_Init()
restore tools/mock/table_full.bin binary $tbl
```

**Step 2. Make three writes.**

```
call (int)Partition_Store_Commit($buf+160, $buf+64, 2000, $buf+128, &'mode_retrieve.c'::s_table)
set var 'mode_retrieve.c'::s_table.entries[0].name[0] = 'Z'
call (int)Partition_Store_Commit($buf+160, $buf+64, 2000, $buf+128, &'mode_retrieve.c'::s_table)
call (int)Partition_Store_Commit($buf+416, $buf+320, 2000, $buf+384, &'mode_retrieve.c'::s_table)
```

The first write goes to A (version 1, full table, key A). The second write goes
to B (version 2, table_Z, key A). The third write goes to A (version 3, table_Z,
key B). Each command returns 1.

**Step 3. Check the integrity of A.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
```

Both numbers must be `0xf5e62408`.

**Step 4. Check the integrity of B.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
```

Both numbers must be `0xef084ab1`.

**Step 5. Look at the first two words of each partition.**

```
x/2xw 0x08008000
x/2xw 0x0800C000
```

The output must look like this.

```
0x8008000:	0x52554e32	0x00000003
0x800c000:	0x52554e32	0x00000002
```

### Result

The test **passes** when the two integrity checks show equal numbers and the
versions are 3 and 2.

### If the result is different

* **The two numbers are different for a partition that you just wrote.** The
  write ended with an error that the flash driver did not report. The driver does
  not check the flash status register. This is known problem G8 from Part 1.

## T-C2.7 Any damaged field makes the partition invalid

### What this test checks

The header and the data are protected by one CRC. So a change to any field must
make that partition invalid. This includes the magic value, the version, the
iterations, the salt, the `auth` value and the CRC itself. It also includes one
changed word in the data and one changed bit in the data. When the active
partition is invalid, the firmware must use the other partition. If a field were
not covered by the CRC, someone could change it without being noticed. For
example, someone could lower the number of iterations.

### Before you start

* You finished T-C2.6. Partition A is active (version 3, key B) and B has version
  2 with key A.
* You typed `set {unsigned int}$z = 0`. This makes `$z` a source of zero bytes.

### Steps

Do these four steps for **each row** of the table. After each row, go back to
the first step of the procedure to rebuild the situation.

**Step 1. Rebuild the situation of T-C2.6.**

```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
call (void)Partition_Store_Init()
restore tools/mock/table_full.bin binary $tbl
call (int)Partition_Store_Commit($buf+160, $buf+64, 2000, $buf+128, &'mode_retrieve.c'::s_table)
set var 'mode_retrieve.c'::s_table.entries[0].name[0] = 'Z'
call (int)Partition_Store_Commit($buf+160, $buf+64, 2000, $buf+128, &'mode_retrieve.c'::s_table)
call (int)Partition_Store_Commit($buf+416, $buf+320, 2000, $buf+384, &'mode_retrieve.c'::s_table)
set {unsigned int}$z = 0
```

This is the same as in T-C2.6. The last line makes `$z` point to four zero bytes.

**Step 2. Do the damage of the row.**

| Row | What you damage in partition A | Command |
|---|---|---|
| a | the magic value | `call (void)Flash_Drv_Write(0x08008000, $z, 4)` |
| b | the version | `call (void)Flash_Drv_Write(0x08008004, $z, 4)` |
| c | the iterations | `call (void)Flash_Drv_Write(0x08008008, $z, 4)` |
| d | one word of the salt | `call (void)Flash_Drv_Write(0x0800800C, $z, 4)` |
| e | one word of the `auth` value | `call (void)Flash_Drv_Write(0x0800801C, $z, 4)` |
| f | the stored CRC | `call (void)Flash_Drv_Write(0x0800803C, $z, 4)` |
| g | one word of the encrypted table | `call (void)Flash_Drv_Write(0x080080A4, $z, 4)` |
| h | one single bit of the encrypted table | see below |
| i | the whole sector | `call (void)Flash_Drv_EraseSector(2)` |

Each command writes four zero bytes over one word. Flash can change bits from 1
to 0 without an erase, so this works. For row h use these three commands. They
read one word and clear its lowest bit.

```
set $w = *(unsigned int*)0x080080A4
set {unsigned int}$z = $w & ($w - 1)
call (void)Flash_Drv_Write(0x080080A4, $z, 4)
```

After row h, type `set {unsigned int}$z = 0` again.

**Step 3. Let the firmware look at the flash again and ask which partition is active.**

```
call (void)Partition_Store_Init()
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
```

The output must look like this for every row.

```
$1 = 3
$2 = 2
```

**Step 4. Check that partition A is damaged.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
```

The two numbers must be different. For row i the stored value is `0xffffffff`.

### Result

The test **passes** when, for every row from a to i, the active partition is B
with version 2 and the two integrity numbers of A are different.

### If the result is different

* **A row leaves A as the active partition.** The damaged field is not covered by
  the CRC, or the firmware does not check it. Check that the CRC covers 60 header
  bytes and 1488 table bytes. Check `ReadSlot` and `ValidateCrc` in
  `Src/app/partition_store.c`.
* **Row h (one bit) is not noticed.** The CRC is not really checking the whole
  table. This is serious.
* **The active partition is B but the version is not 2.** The rebuild in step 1
  did not finish. Repeat it.

Note that a fallback to B means that the newest data is lost. It also means that
the older key is in use again. Test T-C6.5 looks at that problem.

## T-C2.8 A write that stops halfway never replaces the good partition

### What this test checks

A write has three parts. The sector is erased, then the header is written, then
the table is written. If the power fails after any of these parts, the active
partition must stay in use. This test simulates the failures by doing the parts
one by one.

The header alone is the interesting case. It has a valid magic value and the same
version, but the table is missing. Only the CRC can reject it. At the end the
table is completed. Now both partitions are valid copies with the same version.
Partition A must still win.

### Before you start

* You finished T-C2.6. Partition A is active (version 3) and B has version 2.

### Steps

**Step 1. Erase B and look at the active partition.**

```
call (void)Flash_Drv_EraseSector(3)
call (void)Partition_Store_Init()
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
```

The active partition must still be A with version 3.

```
$1 = 2
$2 = 3
```

**Step 2. Copy only the header of A into B.**

```
call (void)Flash_Drv_Write(0x0800C000, (unsigned char*)0x08008000, 64)
call (void)Partition_Store_Init()
print 'partition_store.c'::s_active.sector
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
```

Now B has a valid magic value and version 3, but no table. A must still be active.
The two integrity numbers of B must be different.

```
$3 = 2
$4 = 0x3015996f
0x800c03c:	0xf5e62408
```

**Step 3. Copy the first 700 bytes of the table.**

```
call (void)Flash_Drv_Write(0x0800C040, (unsigned char*)0x08008040, 700)
call (void)Partition_Store_Init()
print 'partition_store.c'::s_active.sector
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
```

A is still active. The two integrity numbers of B are still different. The first one
has changed, because more of the table is present.

```
$5 = 2
$6 = 0x8ec61ea9
0x800c03c:	0xf5e62408
```

**Step 4. Copy the rest of the table.**

```
call (void)Flash_Drv_Write(0x0800C040 + 700, (unsigned char*)0x08008040 + 700, 788)
call (void)Partition_Store_Init()
print 'partition_store.c'::s_active.sector
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
```

Now B is a complete copy of A. Both numbers of B are equal to `0xf5e62408`. A must
still be the active partition.

```
$7 = 2
$8 = 0xf5e62408
0x800c03c:	0xf5e62408
```

### Result

The test **passes** when the active sector is 2 (partition A) after every step
and the integrity numbers of B are different in steps 2 and 3 and equal in step 4.

### If the result is different

* **B becomes active after step 2 or step 3.** The firmware accepts a partition
  that has only a header. The CRC does not cover the table. A power failure
  during a write could then destroy the data. This is serious.
* **B becomes active in step 4.** When two valid partitions have the same
  version, the firmware must choose A. The comparison must be "greater than or
  equal to" in `Partition_Store_Init`.

## T-C2.9 If both partitions are invalid, nothing is decrypted

### What this test checks

When no valid partition exists, the firmware must offer the first setup. No
function may decrypt anything or change the caller's table.

### Before you start

* You finished T-C2.6. Partition A is active (version 3) and B has version 2.
* You typed `set {unsigned int}$z = 0`.

### Steps

**Step 1. Damage the CRC of both partitions.**

```
call (void)Flash_Drv_Write(0x0800803C, $z, 4)
call (void)Flash_Drv_Write(0x0800C03C, $z, 4)
```

Each command writes zeros over the stored CRC of one partition.

**Step 2. Let the firmware look at the flash.**

```
call (void)Partition_Store_Init()
print 'partition_store.c'::s_have_active
print Partition_Store_Active()
```

The output looks like this.

```
$1 = false
$2 = (const partition_info_t *) 0x0
```

**Step 3. Try to decrypt into a table that holds a marker.**

```
restore tools/mock/table_empty.bin binary $tbl
set {unsigned int}$tbl = 0x5A5A5A5A
call (int)Partition_Store_Load($buf+160, &'mode_retrieve.c'::s_table)
x/1xw $tbl
```

The marker `0x5A5A5A5A` is written at the start of the table. The load must
refuse and leave the marker alone.

```
$3 = 0
0x20000a10 <s_table>:	0x5a5a5a5a
```

### Result

The test **passes** when `s_have_active` is `false`, `Partition_Store_Active()`
returns `0x0`, the load returns 0, and the marker is unchanged.

### If the result is different

* **`s_have_active` stays `true`.** An old copy in RAM survives
  `Partition_Store_Init`. The firmware might then trust a partition that no longer
  exists.
* **The load returns 1 or the marker changed.** The load decrypts blank or damaged
  flash into the caller's table.

After this test, continue the board with `continue`. The firmware sees that no
partition is valid and shows the first setup screen.

---

# C3 — The session (`session`)

The functions in `session.c` are the only code that turns a typed master key
into an open session. They check the key rules, they run the key calculation,
they compare the `auth` value, and they open or close the session. In this layer
you call these functions directly from gdb, with the fixed test partition from C2.
Then you know the session code works before you use the terminal in layer C4.

The functions return a result code. This is what the numbers mean.

| Number | Name | Meaning |
|---|---|---|
| 0 | OK | The key is right. The session is open. |
| 1 | WRONG_KEY | The key is wrong, or it breaks the key rules. |
| 2 | INVALID_KEY | The new key breaks the key rules (used when a key is set). |
| 3 | CANCELLED | The panic button was pressed during the work. |
| 4 | STORAGE_ERROR | Writing to flash did not work. |
| 5 | NO_PARTITION | No valid partition exists. |

## T-C3.1 The master key rules

### What this test checks

A master key must have 8 to 31 characters, and every character must be a normal
printable ASCII character (from `0x20` to `0x7E`). The limits fit into the
32-byte buffers and they are what a terminal can type. Mistakes usually happen at
the edges of the rules. So the test tries the edges. These are the lengths 7 and
8, the lengths 31 and 32, and the characters `0x1f`, `0x20`, `0x7e`, `0x7f`,
`0xc3` and `0x00`.

### Before you start

* The board is halted at B0.
* Helper variables are typed.

### Steps

**Step 1. Load a long key into RAM.**

```
restore tools/mock/mk_long.bin binary $buf+512
```

The file holds 40 normal characters (`abcdefghijklmnopqrstuvwxyz0123456789ABCD`).
They are stored at `$buf+512`.

**Step 2. Check the length rule.**

```
call (int)Session_MkPolicyOk($buf+512, 7)
call (int)Session_MkPolicyOk($buf+512, 8)
call (int)Session_MkPolicyOk($buf+512, 31)
call (int)Session_MkPolicyOk($buf+512, 32)
call (int)Session_MkPolicyOk($buf+512, 40)
call (int)Session_MkPolicyOk($buf+512, 0)
```

Each line asks if the first N characters are a valid master key. The answer is 1
for yes and 0 for no. The output must be as follows.

```
$1 = 0
$2 = 1
$3 = 1
$4 = 0
$5 = 0
$6 = 0
```

**Step 3. Check the character rule.**

Each pair of lines below changes the fourth character and asks again with the
length 31.

```
set var $buf[515] = 0x1f
call (int)Session_MkPolicyOk($buf+512, 31)
set var $buf[515] = 0x20
call (int)Session_MkPolicyOk($buf+512, 31)
set var $buf[515] = 0x7e
call (int)Session_MkPolicyOk($buf+512, 31)
set var $buf[515] = 0x7f
call (int)Session_MkPolicyOk($buf+512, 31)
set var $buf[515] = 0xc3
call (int)Session_MkPolicyOk($buf+512, 31)
set var $buf[515] = 0
call (int)Session_MkPolicyOk($buf+512, 31)
```

The answers must be 0, 1, 1, 0, 0 and 0 in this order.

```
$7 = 0
$8 = 1
$9 = 1
$10 = 0
$11 = 0
$12 = 0
```

**Step 4. Put the original text back.**

```
restore tools/mock/mk_long.bin binary $buf+512
```

### Result

The test **passes** when the six answers of step 2 are 0, 1, 1, 0, 0, 0 and the
six answers of step 3 are 0, 1, 1, 0, 0, 0.

### If the result is different

* **A length of 32 is accepted.** The buffers are one byte too small for the
  longest key. Look at `MK_MAX_LEN` in `Inc/app/session.h`.
* **The values `0x7f` or `0xc3` are accepted.** The comparison in
  `Session_MkPolicyOk` treats the characters as signed numbers. It must compare
  them as unsigned bytes.
* **A zero byte inside the key is accepted.** The key would end early when it is
  used as text.

## T-C3.2 Login with the right key, a wrong key and an invalid key

### What this test checks

With the right key, the session must open and the session key must be the `enc`
value of key A. With a wrong key, the login must be refused and the session key
must stay all zeros. A key that breaks the rules must be refused at once,
without a key calculation. The right key and a wrong key must take the same time.
If a wrong key were faster, a correct guess could be found by measuring the time.

### Before you start

* The board is halted at B0.
* Helper variables are typed.
* The key values of T-C1.2 are in RAM.

### Steps

**Step 1. Create a partition that holds the full table with key A.**

```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
call (void)Partition_Store_Init()
restore tools/mock/table_full.bin binary $tbl
call (int)Partition_Store_Commit($buf+160, $buf+64, 2000, $buf+128, &'mode_retrieve.c'::s_table)
call (void)Partition_Store_Init()
restore tools/mock/mk_a.bin binary $buf
```

The first four lines create the blank state and the table. The fifth line writes
partition A with key A. The last two lines let the firmware find the partition and
put the master key text `validpassword1` at `$buf`.

**Step 2. Log in with the right key and measure the time.**

```
print/x 'session.c'::s_authorized
set $t0 = 'systick_drv.c'::s_ms_ticks
call (int)Session_Authenticate($buf, 14)
print 'systick_drv.c'::s_ms_ticks - $t0
print/x 'session.c'::s_authorized
print/x (CRC_Drv_Reset(), CRC_Drv_Feed('session.c'::s_key, 32), CRC_Drv_Result())
```

The commands show if a session is open, run the login, print how many
milliseconds it took, show if a session is open now, and print the CRC of the
session key. The output looks like this.

```
$1 = 0x0
$2 = 0
$3 = 4012
$4 = 0x1
$5 = 0xa65e6f2d
```

The login returns 0 (OK). The session is now open. The CRC of the session key is
`0xa65e6f2d`, which is the CRC of the `enc` value of key A.

**Step 3. Read the table with the session key.**

```
call (int)Session_LoadTable(&'mode_retrieve.c'::s_table)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
```

The first line decrypts the table with the key of the open session. It returns 1.
The CRC of the table must be `0x60085d29`.

**Step 4. Close the session and try a wrong key.**

```
call (void)Session_Wipe()
set var $buf[13] = '2'
set $t0 = 'systick_drv.c'::s_ms_ticks
call (int)Session_Authenticate($buf, 14)
print 'systick_drv.c'::s_ms_ticks - $t0
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
```

The second line changes the last letter of the key to `2`, so the key is now
`validpassword2`. The output looks like this.

```
$6 = 1
$7 = 4010
$8 = 0x0
0x20000a40 <s_key>:	0x00000000	0x00000000	0x00000000	0x00000000
0x20000a50 <s_key+16>:	0x00000000	0x00000000	0x00000000	0x00000000
```

The login returns 1 (WRONG_KEY). It took almost the same time as the right key.
The session is still closed and the session key is all zeros.

**Step 5. Try keys that break the rules.**

```
set $t0 = 'systick_drv.c'::s_ms_ticks
call (int)Session_Authenticate($buf, 7)
print 'systick_drv.c'::s_ms_ticks - $t0
call (int)Session_Authenticate($buf, 32)
```

A key with 7 characters and a key with 32 characters must be refused at once.
The output looks like this.

```
$9 = 1
$10 = 0
$11 = 1
```

### Result

The test **passes** when all of these are true.

* The right key returns 0, opens the session, gives the key CRC `0xa65e6f2d`, and
  the table CRC is `0x60085d29`.
* The wrong key returns 1 after about the same time as the right key, and the key
  stays all zeros.
* The keys with 7 and 32 characters return 1 in about 0 ms.

### If the result is different

* **The CRC of the session key is not `0xa65e6f2d`.** The session holds a value
  other than `enc`. Look at `OpenIfCurrent` and at what `Session_Authenticate`
  passes to it.
* **The wrong key is much faster than the right key.** The code leaves early
  before the calculation is finished. Someone could measure the time to find the
  right key. Fix this.
* **The session key is not zero after a wrong key.** A key is stored before it is
  checked.
* **The wrong key returns 0.** The comparison of the `auth` values is inverted, or
  it compares the wrong values.
* **The rule-breaking keys take seconds.** The key rules are checked after the
  calculation instead of before it.

## T-C3.3 No partition, and the stored number of iterations is used

### What this test checks

On a blank device `Session_Authenticate` must answer 5 (NO_PARTITION). It must not
compare the key with random data. The test also checks that the login uses the
number of iterations that is **stored in the header**. It must not use the value
that is compiled into the program. If it used the compiled value, then raising
`KDF_ITERATIONS` in the future would lock out every existing device.

### Before you start

* The board is halted at B0.
* Helper variables are typed and the key values of T-C1.2 are in RAM.

### Steps

**Step 1. Erase everything and try to log in.**

```
restore tools/mock/mk_a.bin binary $buf
call (void)Session_Wipe()
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
call (void)Partition_Store_Init()
call (int)Session_Authenticate($buf, 14)
```

The last line must return 5.

```
$1 = 5
```

**Step 2. Make keys with only 500 iterations and write a partition with them.**

```
call (int)Kdf_DeriveKeys($buf, 14, $buf+64, 500, 0, $buf+128, $buf+160)
restore tools/mock/table_full.bin binary $tbl
call (int)Partition_Store_Commit($buf+160, $buf+64, 500, $buf+128, &'mode_retrieve.c'::s_table)
call (void)Partition_Store_Init()
```

The first line makes new `auth` and `enc` values with 500 iterations. The
partition header now says that 500 iterations were used.

**Step 3. Log in and measure the time.**

```
set $t0 = 'systick_drv.c'::s_ms_ticks
call (int)Session_Authenticate($buf, 14)
print 'systick_drv.c'::s_ms_ticks - $t0
```

The login must return 0. It must take about one quarter of the time of a login
with 2000 iterations.

```
$2 = 0
$3 = 1004
```

### Result

The test **passes** when step 1 returns 5, and step 3 returns 0 after about a
quarter of the normal time.

### If the result is different

* **Step 3 returns 1 (WRONG_KEY).** The login calculates with the compiled
  iteration count instead of the count in the header. Look at the call of
  `Kdf_DeriveKeys` in `Session_Authenticate`. It must use `header.kdf_iter`.
* **Step 3 takes as long as a normal login.** This has the same cause.
* **Step 1 returns something other than 5.** The function does not check whether a
  partition exists.

---

# C4 — The application on the terminal

This layer tests the program the way a user uses it. You type on the terminal.
You choose a master key, you log in, you change the key and you read a password.
After some steps you also look inside the board with gdb. Some things cannot be
seen on the terminal. For example, the terminal cannot show if a key was wiped
from RAM.

## What you need first

* Layers C1, C2 and C3 pass. If they fail, the results here can be wrong.
* The board runs the Debug build.
* The terminal is open at 115200 baud and gdb is connected.

## Rules for every test in this layer

1. **Do not type while the board is busy.** The screens `Checking, please
   wait...`, `Deriving key, please wait...` and `Re-encrypting, please wait...`
   mean that the processor is doing a slow calculation. It takes about 4 seconds.
   If you type during this time, the board can lose your characters.
2. **The numbers of the application states.** Some tests print the variable
   `g_state`. This is the meaning of its value.

   | Value | State | What the user sees |
   |---|---|---|
   | 0 | `APP_STATE_INIT` | nothing, only for a moment |
   | 1 | `APP_STATE_FIRST_MEET` | `== FIRST TIME SETUP ==` |
   | 2 | `APP_STATE_MK_AUTH` | `== LOCKED ==` |
   | 3 | `APP_STATE_MODE_SELECTION` | the menu |
   | 4 | `APP_STATE_RETRIEVE_MODE` | the password table |
   | 5 | `APP_STATE_GENERATE_MODE` | `Not implemented yet.` |
   | 6 | `APP_STATE_CHANGE_MK_MODE` | `== CHANGE MASTER KEY ==` |

3. **Every test shows all of its commands.** There are no short names for groups
   of commands. This makes the tests longer, but you never need to look at
   another page.

## T-C4.1 First setup on a blank device

### What this test checks

A blank device must ask the user to choose a master key. The user types it twice,
so that a typing mistake cannot lock the owner out of their own data. The key
rules must be applied, and a mismatch between the two entries must start the
setup again. At the end the user must be logged in and see the menu.

### Before you start

* The board is halted at B0 and the terminal is open.
* Erase both partitions and let the program run. In gdb type these commands.

```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
continue
```

* The terminal shows this screen.

```
== FIRST TIME SETUP ==
Choose a master key (8-31 printable characters).
Master key:
```

### Steps

**Step 1. Type the strings of the table, one at a time, and press Enter after each one.**

After each string, look at the screen. The terminal shows the text you type.

| You type | You should see |
|---|---|
| `abcdefg` (7 characters) | `Invalid master key: use 8-31 printable characters. Try again.` and then `Master key:` |
| `abcdefghijklmnopqrstuvwxyz012345` (32 characters) | the same `Invalid master key` message |
| `pässword123` (with the accent) | the same `Invalid master key` message |
| an empty line | the same `Invalid master key` message |
| `abcdefgh` (8 characters) | `Confirm master key:` |
| `abcdefgi` (different from the first entry) | `The two entries do not match. Start again.` and then the first screen again |

**Step 2. Do the real setup.**

Type `validpassword1` and press Enter. The screen asks `Confirm master key:`.
Type `validpassword1` again and press Enter. Wait about 4 seconds. Do not type.
The screen looks like this.

```
Deriving key, please wait...
Master key set (4012 ms).

== MODE SELECTION ==
  1) Retrieve password
  2) Generate password (not implemented yet)
  3) Change master key
Select:
```

The time in brackets is different on your board. It is about the same as the time
you measured in T-C1.3.

**Step 3. Look inside the board.**

```
interrupt
print 'app.c'::g_state
print 'partition_store.c'::s_have_active
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
x/2xw 0x08008000
x/2xw 0x0800C000
```

The first line halts the board. The next lines show the application state, if a
partition is active, and which one. The last two lines read the first two words
of each partition. The output looks like this.

```
$1 = APP_STATE_MODE_SELECTION
$2 = true
$3 = 2
$4 = 1
0x8008000:	0x52554e32	0x00000001
0x800c000:	0xffffffff	0xffffffff
```

Type `continue` to run the board again.

### Result

The test **passes** when the screens match the table, the setup ends with the
menu, the state is `APP_STATE_MODE_SELECTION`, partition A is active with version
1, and partition B is still erased.

### If the result is different

* **A key with 7 or 32 characters is accepted, or a good key is refused.** The key
  rules are wrong. Run test T-C3.1.
* **Two different entries are accepted.** The comparison of the two entries in
  `Src/app/mode_first_meet.c` has a problem.
* **The screen stays at `Deriving key` for a long time.** The calculation or the
  flash write did not finish. If the screen shows `Storage error`, check the flash
  driver with the tests of Part 1, layer L3.
* **Partition B is not erased.** The first write went to both sectors.

## T-C4.2 What the first setup stored in flash

### What this test checks

You should not trust the firmware only because it says "Master key set". This
test calculates the `auth` value again with a separate command, using the salt
that is in flash and the master key you typed. The result must be the same as the
value in flash. The test also checks four other things. The header must hold the
number of iterations and a salt that comes from the chip's unique ID. The
encryption key and the key text must not be in flash. The session key in RAM must
be the `enc` value.

### Before you start

* You finished T-C4.1. You set the master key `validpassword1` and the menu is
  shown.
* The helper variables are typed. In gdb type `interrupt`.

### Steps

**Step 1. Read the header and the chip's unique ID.**

```
x/16xw 0x08008000
x/3xw 0x1FFF7A10
```

The first line shows the header of partition A. The second line shows the unique
ID of the chip (three words). The output looks like this. Your numbers will be
different.

```
0x8008000:	0x52554e32	0x00000001	0x000007d0	0x0047003b
0x8008010:	0x3033511a	0x30373636	0x0000d3a5	0x9c11e7b2
0x8008020:	0x4f0a6d13	0x81c5f092	0x2be7a4d8	0x6603b1ce
0x8008030:	0xd1e0492f	0x5a87c3b4	0x730f92aa	0x1d64be05
0x1fff7a10:	0x0047003b	0x3033511a	0x30373636
```

The first three words are the magic value, version 1 and `0x7d0` iterations. The
next three words (the start of the salt) must be equal to the three ID words. The
fourth salt word is different on every write.

**Step 2. Check that the partition is intact.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
```

The two numbers must be equal.

**Step 3. Calculate the `auth` value again from the salt in flash.**

```
restore tools/mock/mk_a.bin binary $buf
call (int)Kdf_DeriveKeys($buf, 14, (unsigned char*)0x0800800C, 2000, 0, $buf+128, $buf+160)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf+128, 32), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800801C, 32), CRC_Drv_Result())
```

The first line puts the master key text in RAM. The second line runs the key
calculation with the salt from flash (address `0x0800800C`). The last two lines
show the CRC of the new `auth` value and the CRC of the `auth` value in flash.
The two numbers must be equal, for example `0x7be3a1c4` and `0x7be3a1c4`.

**Step 4. Search flash for the encryption key and for the key text.**

```
find /b 0x08008000, +0x8000, $buf[160], $buf[161], $buf[162], $buf[163], $buf[164], $buf[165], $buf[166], $buf[167]
find /b 0x08008000, +0x8000, 'v','a','l','i','d','p','a','s','s'
```

The first line searches for the first 8 bytes of the `enc` value that you just
calculated. The second line searches for the start of the master key text. Both
must print `Pattern not found.`

**Step 5. Compare the session key in RAM with `enc`.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed('session.c'::s_key, 32), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf+160, 32), CRC_Drv_Result())
```

The two numbers must be equal. Then type `continue` to run the board again.

### Result

The test **passes** when all of this is true.

* The header has the right format.
* The first three salt words are the ID words.
* The two `auth` numbers are equal.
* Both searches find nothing.
* The two session key numbers are equal.

### If the result is different

* **The two `auth` numbers are different.** The stored value is not the expected
  function of your key and the stored salt. The salt or the number of iterations
  that was used differs from what is stored.
* **The first salt words are not the ID words.** The salt is made in another way.
  Salts can then repeat on different devices.
* **One of the searches finds something.** Key material is stored in flash. This
  is serious.
* **The session key is not equal to `enc`.** The session was opened with a wrong
  value.

## T-C4.3 Login. Wrong keys, the right key and the time

### What this test checks

The user can try as many keys as they want. The slow key calculation is the only
brake. So a wrong key and the right key must cost the same time. A key that
breaks the rules may be refused quickly, because the rules are public. Nothing
that you type at the login screen may ever be taken as a menu choice.

### Before you start

* The device holds a partition with the master key `validpassword1`. It is the
  result of T-C4.1.
* Reset the board so it starts fresh. In gdb type `monitor reset` and `continue`.
* The terminal shows this screen.

```
== LOCKED ==
Master key:
```

### Steps

**Step 1. Type each line of the table and press Enter. Write down the time.**

| Row | You type | You should see |
|---|---|---|
| 1 | `validpassword2` | `Wrong master key (4010 ms).` and then `Master key:` |
| 2 | `Validpassword1` (capital V) | `Wrong master key` |
| 3 | `validpassword1 ` (one space at the end) | `Wrong master key`, because a space is part of the key |
| 4 | `alidpassword1` | `Wrong master key` |
| 5 | `abcdefg` (7 characters) | `Wrong master key (0 ms)` at once, without a calculation |
| 6 | an empty line | only `Master key:` again and no message |
| 7 | `1`, then `2`, then `3`, then `12` | each time `Wrong master key (0 ms)`. The menu does not open. |
| 8 | `validpassword1` | `Access granted (4012 ms).` and then the menu |

The screen for a login looks like this.

```
Checking, please wait...
Wrong master key (4010 ms).
Master key:
```

**Step 2. Repeat rows 1 and 8 three times each.**

Reset the board between the tries. Calculate the average of the wrong-key times
and the average of the right-key times.

### Result

The test **passes** when all of this is true.

* The times of rows 1 to 4 and row 8 are within about 1 or 2 percent of each other.
* Rows 5 to 7 show about 0 ms.
* Only row 8 opens the menu.

The number of guesses per hour that an attacker can make on this board is
`3600 divided by the seconds per attempt`. Write it in the timing table in C7.

### If the result is different

* **Wrong keys are much faster than the right key.** The firmware leaves early. An
  attacker can find the right key by measuring time.
* **A digit at the login screen opens the menu.** The input is taken as a menu
  choice. This is a serious problem.
* **A key is accepted although the case or the last space is different.** The
  input is changed somewhere before it is used.

## T-C4.4 A forced state cannot skip the login

### What this test checks

Every state after the login screens needs an open session. If the session is
closed, the program must go back to the login, no matter who wrote the state
variable. This protects against a bug, against a race with the panic button, and
against someone who writes to memory with a debugger.

### Before you start

* The terminal shows the login screen `== LOCKED ==`.

### Steps

**Step 1. Halt the board and force the menu state.**

```
interrupt
set var 'app.c'::g_state = 3
continue
```

The value 3 is `APP_STATE_MODE_SELECTION`. Wait one second and look at the
terminal. The menu must not appear. The login screen must be shown again.

**Step 2. Look at the state.**

```
interrupt
print 'app.c'::g_state
```

The output must be `$1 = APP_STATE_MK_AUTH`.

**Step 3. Repeat with the states 4, 5 and 6.**

```
set var 'app.c'::g_state = 4
continue
```

Wait one second, then type `interrupt` and `print 'app.c'::g_state`. Repeat the
same three lines with the value 5 and then with the value 6. Type `continue` at
the end.

### Result

The test **passes** when none of the four values shows a menu, a table, the text
`Not implemented yet.` or a change-key screen. The state is `APP_STATE_MK_AUTH`
each time.

### If the result is different

* **A screen appears without a login.** The check in `App_Run()` in
  `Src/app/app.c` is missing or it tests the wrong variable. Access to the data is
  not protected. Fix this before you continue.

## T-C4.5 Closing the session from gdb sends you back to the login screen

### What this test checks

The panic button runs a function named `Session_Wipe()`. This function closes the
session. In this test you run the function yourself from gdb. Then you can see
what it changes in memory. The test also checks that the program goes back to the
login screen by itself. This matters because after the panic button nobody may
use the menu without the master key.

### Before you start

* You are logged in. The terminal shows the menu.

```
== MODE SELECTION ==
  1) Retrieve password
  2) Generate password (not implemented yet)
  3) Change master key
Select:
```

* If it does not, reset the board and log in first.

### Steps

**Step 1. Halt the board.**

```
interrupt
```

This command stops the processor. gdb can read memory only when the board is
halted. The output shows where the program stopped, for example this.

```
0x08001a2c in USART_Drv_RxComplete ()
```

The place can be different. It does not matter.

**Step 2. Look at the session before the wipe.**

```
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
print 'session.c'::s_generation
```

The first line shows if the session is open. The value `0x1` means "logged in".
The second line shows the session key. The key has 32 bytes and gdb prints it as
8 numbers of 4 bytes each. The third line shows the panic counter. The counter
grows by 1 every time the panic function runs. It is 0 if the panic button was
not used since the last reset. The output looks like this.

```
$1 = 0x1
0x20000a40 <s_key>:	0x8f3c21d7	0x0b99e4a2	0x51c0aa17	0xd2f03b6e
0x20000a50 <s_key+16>:	0x7e114c90	0xa93d0855	0x6b2fe1c3	0x04d9aa38
$2 = 0
```

The key numbers are different on your board. It is important that the key is not
all zeros. Write down the value of the panic counter. Here it is 0.

**Step 3. Run the wipe function.**

```
call (void)Session_Wipe()
```

This runs the same function that the panic button runs. gdb prints nothing.

**Step 4. Look at the same values again.**

```
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
print 'session.c'::s_generation
```

The output looks like this.

```
$3 = 0x0
0x20000a40 <s_key>:	0x00000000	0x00000000	0x00000000	0x00000000
0x20000a50 <s_key+16>:	0x00000000	0x00000000	0x00000000	0x00000000
$4 = 1
```

The session is closed. The key is all zeros. The counter is one higher than
before.

**Step 5. Let the program run.**

```
continue
```

The program sees that there is no session and goes back to the login screen. The
terminal shows this.

```
== LOCKED ==
Master key:
```

**Step 6. Check that the menu does not open.**

On the terminal, type `1` and press Enter. The screen shows this.

```
Checking, please wait...
Wrong master key (0 ms).
Master key:
```

The firmware reads your `1` as a master key. It is too short, so it is refused at
once. The menu does not open.

### Result

The test **passes** when all of this is true. After step 4, `s_authorized` is
`0x0`, the key is all zeros, and the panic counter is exactly 1 higher than in
step 2. After step 5 the terminal shows `== LOCKED ==`. After step 6 the menu does
not open.

### If the result is different

* **The key is not all zeros after step 3.** `Session_Wipe()` did not erase the
  key. Open `Src/app/session.c`. The function must call `Secure_Zero` on `s_key`.
  Build the program again and load it on the board.
* **`s_authorized` is still `0x1` after step 3.** The function does not close the
  session. Look at the same function.
* **The panic counter did not grow.** The counter protects against this case. A
  login that was already running could finish after the panic button and log you
  in. Look for the line `s_generation++` in `Session_Wipe()`.
* **After step 5 the menu is shown again, or another screen appears instead of
  `== LOCKED ==`.** This is a security problem. The check "is a session open?" in
  `App_Run()` in `Src/app/app.c` is missing or it checks the wrong variable. Fix
  this before you run other tests.
* **After step 5 nothing appears on the terminal for more than 2 seconds.** Type
  `interrupt` and then `print 'app.c'::g_state`. The answer should be
  `APP_STATE_MK_AUTH`. If it is not, the program is stuck in another state. Then
  type `continue` again.

## T-C4.6 Reading passwords, and the check of the id

### What this test checks

This is the full test from the flash to the screen. A table is stored under the
derived key. The device is restarted, the user logs in (a new key calculation),
and the table is read back. The test also checks that the id you type is checked
strictly. An id such as `abc` or `0x5` must be refused and must never show entry
0.

### Before you start

* You are logged in with the master key `validpassword1` and the menu is shown.
* Write the full table into the inactive partition. In gdb type these commands.

```
interrupt
restore tools/mock/table_full.bin binary $tbl
call (int)Partition_Store_Commit('session.c'::s_key, 'partition_store.c'::s_active.header.salt, 'partition_store.c'::s_active.header.kdf_iter, 'partition_store.c'::s_active.header.auth, &'mode_retrieve.c'::s_table)
continue
```

The second line loads the full table into RAM. The third line writes it to the
inactive partition (B), with the key of the open session. It returns 1. This is
the same thing that `Session_Save` will do later. Partition B now has version 2
and partition A still has version 1.

### Steps

**Step 1. Show the table.**

On the terminal type `1` and press Enter. The screen shows the list of names.

```
-- Password table --
   0 - S00abcdefghijkl
   1 - S01abcdefghijkl
   ...
  30 - S30abcdefghijkl

Enter id to view (0-30), or 'q' to go back:
```

There must be 31 lines, from id 0 to id 30.

**Step 2. Show three entries.**

Type `0`, then `7`, then `30`, and press Enter after each. The screen shows the
name and the password.

```
S07abcdefghijkl : pw07!@#$%^&*()-_=+[]{};:,.<>/?a
```

For id 0 and id 30 the numbers in the text are `00` and `30`.

**Step 3. Try ids that must be refused or accepted.**

Type each line of the table and press Enter.

| You type | You should see |
|---|---|
| `31` | `Invalid id.` |
| `-1` | `Invalid id.` |
| `abc` | `Invalid id.` |
| `0x5` | `Invalid id.` |
| `4294967296` | `Invalid id.` |
| `07` | the entry of id 7 |
| ` 5` (a space and then 5) | the entry of id 5 |

Then type `q` to go back to the menu.

**Step 4. Look at the partitions.**

```
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
x/2xw 0x08008000
x/2xw 0x0800C000
continue
```

The output looks like this.

```
$1 = 3
$2 = 2
0x8008000:	0x52554e32	0x00000001
0x800c000:	0x52554e32	0x00000002
```

**Step 5. Restart the board and read the table again.**

In gdb type `monitor reset` and `continue`. On the terminal log in with
`validpassword1`. Then type `1` and `7`. The entry of id 7 is shown and the program
waits for the next id. Now check the table in RAM. Do not type `q` before this.

```
interrupt
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
continue
```

The answer must be `$3 = 0x60085d29`. Then type `q` on the terminal.

### Result

The test **passes** when all of this is true.

* The list has 31 lines.
* The entries show the names and passwords in the sample.
* The ids `31`, `-1`, `abc`, `0x5` and `4294967296` are refused.
* The table has the CRC `0x60085d29` after the restart.

### If the result is different

* **A refused id shows entry 0.** The check of the id has a problem. Look at
  `ParseId` in `Src/app/mode_retrieve.c`.
* **The table is empty or garbled after the restart.** The key that was used to
  write the table is not the key that the login makes. Run test T-C4.2 again.

## T-C4.7 The menu needs the whole line

### What this test checks

The menu accepts a choice only when the line is exactly `1`, `2` or `3`. A line
such as `12` or `1abc` must not open a mode.

### Before you start

* You are logged in and the menu is shown.

### Steps

**Step 1. Type each line of the table and press Enter.**

| You type | You should see |
|---|---|
| `2` | `Not implemented yet.` and then the menu again |
| `3` | the screen `== CHANGE MASTER KEY ==` |
| an empty line (at the change key screen) | `Cancelled.` and then the menu |
| `12` | `Unknown option.` and then the menu |
| `1abc` | `Unknown option.` and then the menu |
| ` 1` (a space and then 1) | `Unknown option.` and then the menu |
| `Q` | `Unknown option.` and then the menu |
| `x` | `Unknown option.` and then the menu |
| an empty line | `Unknown option.` and then the menu |
| `1` | the password table. Type `q` to go back. |

The screen for a wrong choice looks like this.

```
Unknown option.

== MODE SELECTION ==
  1) Retrieve password
  2) Generate password (not implemented yet)
  3) Change master key
Select:
```

### Result

The test **passes** when every row shows what the table says.

### If the result is different

* **`12` opens the password table.** The firmware compares only the first
  character. This was a known problem in Part 1 (G9). Check `HandleModeSelection`
  in `Src/app/app.c`. It must compare the whole line.

When the GENERATE_MODE branch is merged, replace the row for `2`.

## T-C4.8 Refused key changes do not change anything

### What this test checks

Only a valid new key, entered twice, may cause a write. If the user cancels, or
types an invalid key, or types two different keys, both partitions must stay
exactly as they were.

### Before you start

* You are logged in and the menu is shown. Partition B is active (version 2) with
  the full table, as at the start of T-C4.6.
* Take a fingerprint of both sectors. In gdb type these commands.

```
interrupt
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
print 'partition_store.c'::s_active.sector
continue
```

Write down the two numbers and the sector.

### Steps

**Step 1. Cancel.** On the terminal type `3` and press Enter. At the prompt for
the new key press Enter on an empty line. The screen shows `Cancelled.` and the
menu.

**Step 2. Invalid keys.** Type `3`. Then type `abcdefg` and press Enter. The
screen shows the `Invalid master key` message. Type `pässword123` and press
Enter. The same message is shown. Then press Enter on an empty line to cancel.

**Step 3. Two different entries.** Type `3`. Type `newpassword22` and press
Enter. The screen asks `Confirm new master key:`. Type `newpassword23` and press
Enter. The screen shows `The two entries do not match. Start again.` Then press
Enter on an empty line to cancel.

**Step 4. Take the fingerprints again.**

```
interrupt
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
print 'partition_store.c'::s_active.sector
continue
```

### Result

The test **passes** when the two fingerprints and the active sector are exactly
the same as before step 1.

### If the result is different

* **A fingerprint changed.** A write happened after a refused or cancelled change.
  Look at `Mode_ChangeMk_Run` in `Src/app/mode_change_mk.c`. It may call
  `Session_SetNewKey` too early.

## T-C4.9 A successful key change

### What this test checks

A key change must do these things. It writes to the other partition with the
version one higher. It leaves the old partition unchanged. It uses a new salt and
a new `auth` value. It keeps the table. It makes only the new key valid. And it
never stores the text of the new key.

### Before you start

* You are logged in and the menu is shown. Partition B is active (version 2) with
  the full table and key A (`validpassword1`).
* Record the old header. In gdb type these commands.

```
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
x/4xw 0x0800C00C
x/8xw 0x0800C01C
x/1xw 0x0800C008
continue
```

The last three lines show the salt, the `auth` value and the number of iterations
of partition B. Write everything down.

### Steps

**Step 1. Change the key on the terminal.**

Type `3`. Type `newpassword22` and press Enter. Type `newpassword22` again and
press Enter. Wait about 4 seconds. The screen shows this.

```
Re-encrypting, please wait...
Master key changed. The old key no longer works.
```

**Step 2. Look at both partitions.**

```
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
x/16xw 0x08008000
x/4xw 0x0800800C
x/8xw 0x0800801C
```

The commands show which partition is active, the fingerprints of both sectors, the
whole new header, and the new salt and `auth` value.

**Step 3. Check that both partitions are intact.**

```
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
```

For each partition the two numbers must be equal.

**Step 4. Check that the new key text is not in flash and that the session key changed.**

```
find /b 0x08008000, +0x8000, 'n','e','w','p','a','s','s','w','o','r','d'
print/x (CRC_Drv_Reset(), CRC_Drv_Feed('session.c'::s_key, 32), CRC_Drv_Result())
continue
```

The search must print `Pattern not found.`. The CRC of the session key must be
different from the CRC before the change.

**Step 5. Read a password and restart.**

On the terminal type `1` and `7`, and then `q`. The entry of id 7 must be
unchanged. Now restart. In gdb type `monitor reset` and `continue`. On the
terminal type the old key `validpassword1`. The screen must show `Wrong master
key`. Then type the new key `newpassword22`. The screen must show `Access
granted`.

### Result

The test **passes** when all of this is true.

* The active partition is now A (sector 2) with version 3.
* The fingerprint of B is the same as before. The fingerprint of A is different.
* The salt words and the `auth` words of A are different from those of B. The
  number of iterations is `0x000007d0` in both.
* Both partitions are intact.
* The new key text is not found in flash.
* Entry 7 is unchanged, the old key is refused after the restart, and the new key
  is accepted.

### If the result is different

* **B changed and A did not.** The write went to the active partition. This is a
  power-loss danger (see test T-C2.5).
* **The salt is equal to the old salt.** A new salt is not made. The same password
  would give the same `auth` value again.
* **The table is wrong after the change.** The re-encryption used a wrong copy of
  the table.
* **The old key is still accepted after the restart while both partitions are
  valid.** The login used the older partition. See also known problem G10 in
  test T-C6.5.

## T-C4.10 Repeated changes keep switching partitions

### What this test checks

Each key change must go to the other partition. After four changes the versions
and the sectors must follow a clear pattern. This test also uses the longest
allowed key, with 31 characters.

### Before you start

* You are logged in and the menu is shown. Partition B is active (version 2).

### Steps

**Step 1. Change the key four times.**

Use the menu option 3 four times. Use these new keys in this order. The first is
`newpassword22`. The second is `abcdefghijklmnopqrstuvwxyz01234` (31 characters).
The third is `validpassword1`. The fourth is `newpassword22`.

**Step 2. After each change look at the active partition.**

```
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
continue
```

The answers must be as follows.

| After change | Sector | Version |
|---|---|---|
| 1 | 2 | 3 |
| 2 | 3 | 4 |
| 3 | 2 | 5 |
| 4 | 3 | 6 |

**Step 3. Test the keys after a restart.**

After the last change, restart the board with `monitor reset` and `continue`. The
key `newpassword22` must open the device. The 31-character key must be refused.

### Result

The test **passes** when the sectors alternate, the version grows by 1 each time,
the 31-character key was accepted, and only the last key opens the device.

### If the result is different

* **The same sector appears twice in a row.** The choice of the target sector is
  wrong.
* **The 31-character key is refused at the login although the change accepted
  it.** The login buffer is one byte too small.

## T-C4.11 How much stack does the deepest calculation use?

### What this test checks

The login calls a chain of functions. The deepest part is
`Session_Authenticate`, then `Kdf_DeriveKeys`, `Kdf_Pbkdf2Sha256`,
`HmacSha256_Compute`, `SHA256_Final` and `SHA256_Transform`. Several of these use
big local variables of 200 to 300 bytes. The linker reserves `_Min_Stack_Size`,
which is `0x1000` bytes. The interrupts of the UART, the DMA and the panic button
also need stack space on top of that.

### Before you start

* The board shows the login screen `== LOCKED ==`.

### Steps

**Step 1. Set a breakpoint in the block calculation.**

```
interrupt
delete
break SHA256_Transform
continue
```

**Step 2. Log in on the terminal.**

Type `validpassword1` and press Enter. gdb stops in `SHA256_Transform`.

**Step 3. Measure the stack depth at the first stop.**

```
print $sp
print 0x20020000 - (unsigned int)$sp
bt
```

The stack starts at `0x20020000` and grows downward. The second line prints how
many bytes are in use. The `bt` command prints the chain of function calls. The
output looks like this.

```
$1 = (void *) 0x2001f8d0
$2 = 1840
#0  SHA256_Transform (...) at Src/crypto/sha256.c:40
#1  SHA256_Update (...) at Src/crypto/sha256.c:...
#2  HmacSha256_Init (...) at Src/crypto/hmac_sha256.c:...
...
```

**Step 4. Measure again near the end of the calculation.**

```
ignore 1 4005
continue
print 0x20020000 - (unsigned int)$sp
bt
delete
continue
```

The block function is called about 4010 times during a login. The `ignore 1 4005`
command skips the first 4005 stops, so the next stop is near the end. There the
one-shot `HmacSha256` calls run. If the login finishes without a stop, use a
smaller number than 4005. Use the bigger of the two depth numbers.

### Result

The test **passes** when the depth is below about 2048 bytes (my estimate for the
Debug build is about 1500 bytes, not measured).

### If the result is different

* **The depth is more than `0x800` (2048) bytes.** The space left for interrupts
  is small.
* **The depth is more than `0x1000` (4096) bytes.** The stack overflows into the
  variables. Random errors can follow. Move the large local variables
  (`hmac_sha256_ctx_t`, `partition_header_t`) to `static` storage, or make
  `_Min_Stack_Size` bigger.

---

# C5 — The panic button in every mode, and power loss

The panic button must always end at the login screen with all secrets wiped. It
must also never leave the flash in a half-finished state. This must hold even when
the button is pressed in the middle of a flash write. In this layer you press the
button in every mode and at every step of a write. You also simulate a power loss
at every step.

## Two different events

Do not mix up these two events.

* **A panic** means that the interrupt runs and then returns. The code that was
  running continues afterwards. So a flash write that was running **finishes**. But
  the session is closed.
* **A power loss** means that the processor stops at once. In gdb you simulate it
  with `monitor reset` at a breakpoint. Only data that already reached flash
  survives.

## Rules that must hold after every test

After every test in this layer, these rules must be true. Each test tells you which
commands to type to check them.

1. Only the partition that was inactive before the test may have changed.
2. If it changed, it is now the active partition. Its version is one higher than
   before. It passes the integrity check.
3. The partition that was active before the test is exactly the same as before,
   unless it is the one that changed.
4. Exactly one of the two keys (the old key and the new key) opens the device.
   The table is intact.
5. After a panic the session is closed and no secret is left in RAM.

## T-C5.1 Pressing the button from gdb is the same as pressing the real button

### What this test checks

All the panic tests use the trick from "Things to know" (note 5). You set the
"button pressed" flag from gdb. This test shows that this gives the same result as
the real button.

### Before you start

* You are logged in and the menu is shown.

### Steps

**Step 1. Press the button from gdb.**

```
interrupt
print 'session.c'::s_generation
set {unsigned int}0x40013C10 = 0x400
continue
```

The first line halts the board. The second line prints the panic counter. The
third line sets the flag of the panic button. The fourth line lets the processor
run. The terminal must show this screen.

```
== LOCKED ==
Master key:
```

**Step 2. Look at the panic counter again.**

```
interrupt
print 'session.c'::s_generation
continue
```

The counter must be one higher than in step 1. For example the first answer is
`$1 = 0` and the second answer is `$2 = 1`.

**Step 3. Do it with the real button.**

Log in again with `validpassword1`. When the menu is shown, press the physical
button on pin PA10. The terminal must show the same login screen. Then look at the
counter once more.

```
interrupt
print 'session.c'::s_generation
continue
```

The counter must be one higher again (`$3 = 2`).

### Result

The test **passes** when the gdb press and the real press both show the login
screen and both add 1 to the panic counter.

### If the result is different

* **Nothing happens after the gdb press.** Line 10 of the interrupt controller may
  be masked. Look at test L1 in Part 1. Two presses within 200 ms count as one, so
  wait a second and try again.
* **The real button does nothing but the gdb press works.** The problem is the
  wiring or the debounce. See tests T9.1 and T9.2 in Part 1.

## T-C5.2 A panic at every prompt

### What this test checks

The panic button is pressed while the board waits at each of the seven prompts. At
every prompt the state of the flash must not change. All secrets must be wiped. The
login screen (or the first setup screen on a blank device) must appear.

### Before you start

* The board runs. You can reach every situation of the table below.

### Steps

Do these steps for each row of the table.

**Step 1. Reach the situation of the row.**

| Row | Situation | How to reach it |
|---|---|---|
| 1 | The login prompt, nothing typed | Reset the board. The terminal shows `== LOCKED ==`. |
| 2 | The first setup prompt on a blank device | Erase both partitions at B0 and type `continue`. The terminal shows `== FIRST TIME SETUP ==`. |
| 3 | The first setup, after the first entry | On a blank device type `validpassword1` once. The screen shows `Confirm master key:`. |
| 4 | The menu | Log in. |
| 5 | Retrieve mode, waiting for an id | Log in. Type `1` and then `7`. The screen shows the entry and the id prompt. |
| 6 | Change key, waiting for the new key | Log in. Type `3`. The screen shows `New master key`. |
| 7 | Change key, after the first entry | Log in. Type `3` and then `newpassword22`. The screen shows `Confirm new master key:`. |

Rows 1 and 4 to 7 need a device with a partition that holds key A and the full
table. Rows 2 and 3 need a blank device.

**Step 2. Halt the board and look at the state before the panic.**

```
interrupt
print 'partition_store.c'::s_have_active
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
x/8xw 'mode_first_meet.c'::s_first
x/8xw 'mode_change_mk.c'::s_new
print/x (CRC_Drv_Reset(), CRC_Drv_Feed(&'mode_change_mk.c'::s_work, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 1024), CRC_Drv_Result())
x/16xw 'usart_drv.c'::s_rx_buf
```

The first three lines show the active partition. The next two lines give the
fingerprint of each sector. The last commands look at every place that can hold a
secret. They show these things in this order.

1. The session flag.
2. The session key.
3. The first master key of the setup.
4. The new master key of the key change.
5. The working copy of the table for the key change.
6. The decrypted table.
7. The line buffer for passwords.
8. The UART receive buffer.

Write down the results.

**Step 3. Press the panic button from gdb.**

```
set {unsigned int}0x40013C10 = 0x400
continue
```

Wait one second. Then type `interrupt` again.

**Step 4. Look at the same things again.**

Type the same commands as in step 2.

**Step 5. Let the program run and look at the terminal.**

```
continue
```

**Step 6. Type the old key.**

On the terminal type the correct master key `validpassword1`. In rows 2 and 3 the
device is blank, so the first setup starts again instead.

### Result

The test **passes** when all of this is true for every row.

* The active partition and both fingerprints are the same before and after. A
  panic never writes to flash.
* After the panic every secret place is wiped. This means that `s_authorized` is
  `0x0`, the eight-word places show only `0x00000000`, both table numbers are
  `0xbb034f8e`, the line buffer number is `0x8b0a5208`, and the receive buffer
  shows only `0x00000000`.
* The terminal shows `== LOCKED ==` (rows 1 and 4 to 7) or `== FIRST TIME SETUP ==`
  (rows 2 and 3).
* In row 3 and row 7 the first entry that you typed is gone. The setup or the
  change starts again.
* The correct old key opens the device. The new key `newpassword22` from row 7 does
  not.

### If the result is different

* **A secret place is not zero after the panic.** The panic function does not wipe
  that place. Look at `App_PanicHandler` in `Src/app/app.c`. It must call the wipe
  function of every module.
* **A fingerprint changed.** Something wrote to flash on the panic path.
* **No prompt appears after one second.** The panic left the program in a state
  that the start routine cannot leave. Type `interrupt`, `print 'app.c'::g_state`,
  and look at the value.

## T-C5.3 A panic during a key calculation

### What this test checks

A key calculation takes seconds. The panic button must not wait for it to finish.
And a correct key must not log you in after the button was pressed. The calculation
looks at a cancel function every 32 iterations, so it should stop after a very
short time. This test runs it for three cases. These are a login, a first setup
and a key change.

### Before you start

* You reached the situation of the case (see below).

### Steps

Do these steps for each of the three cases.

**Step 1. Reach the situation.**

| Case | Situation |
|---|---|
| a | The login screen. The device holds a partition with key A. |
| b | A blank device at the first setup screen. |
| c | You are logged in with key A and the menu is shown. |

**Step 2. Set a breakpoint that stops about one second into the calculation.**

```
interrupt
delete
break HmacSha256_Compute
ignore 1 500
continue
```

The calculation calls `HmacSha256_Compute` once per iteration. The `ignore 1 500`
command skips the first 500 stops. Here `1` is the number of the breakpoint. gdb
printed it when you typed `break`.

**Step 3. Start the calculation on the terminal.**

| Case | What you type |
|---|---|
| a | `validpassword1` |
| b | `validpassword1` and then `validpassword1` again to confirm |
| c | `3`, then `newpassword22`, and then `newpassword22` again |

gdb stops after about one second in the Debug build.

**Step 4. Remove the breakpoint and press the panic button.**

```
delete
set {unsigned int}0x40013C10 = 0x400
continue
```

Use a stopwatch. Measure how long it takes from `continue` until the prompt
appears on the terminal.

**Step 5. Look at the result.**

```
interrupt
print 'session.c'::s_authorized
x/8xw 'session.c'::s_key
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
continue
```

The commands show if a session is open, the session key and the fingerprint of
each sector.

**Step 6. Try the correct key again.**

For cases a and c type `validpassword1` on the terminal. For case b type the key
twice to set it. In case c also try `newpassword22`.

### Result

The test **passes** when all of this is true.

* The prompt appears within about 0.1 to 0.3 seconds after `continue`. It does not
  wait for the calculation to finish.
* Case a. The device is not logged in, although the key was correct. `s_authorized`
  is `0x0` and the key is all zeros. Typing the key again works normally.
* Case b. Both sectors are blank (`0x34132f69` twice). No partition was written.
  The first setup screen is shown again.
* Case c. Both fingerprints are the same as before. The old key still works. The
  new key does not.

### If the result is different

* **The prompt appears only after the whole calculation time.** The cancel function
  is not called. Look at `Kdf_Pbkdf2Sha256`.
* **The device is logged in (the menu is shown) after the panic.** This is a
  serious problem. The result of a calculation that was running during the panic
  was used. `OpenIfCurrent` must compare the panic counter inside the protected
  section.
* **A fingerprint changed in case b or case c.** A write happened after the
  calculation was cancelled.

## T-C5.4 A panic one step before the session opens

### What this test checks

This is the smallest gap. The key is correct and the comparison passed. The panic
button is pressed just before the result would be stored. The result must be thrown
away. In the login case the correct key must not log you in.

For a first setup and a key change, this point comes after the write to flash. So
the new data **is** written, but the session is not opened. Then the new key opens
the device at the next login.

### Before you start

* Case a is a login screen with key A. Case b is a blank device at the first setup.
  Case c is a logged in device with the menu.

### Steps

**Step 1. Set a breakpoint in the function that opens the session.**

```
interrupt
delete
break OpenIfCurrent
continue
```

**Step 2. Type the key on the terminal.**

For case a type `validpassword1`. For case b type `validpassword1` twice. For case
c type `3`, then `newpassword22` twice. Wait until gdb stops. The calculation is
finished when gdb stops here.

**Step 3. Press the panic button at this moment.**

```
delete
set {unsigned int}0x40013C10 = 0x400
continue
```

**Step 4. Look at the result.**

```
interrupt
print 'session.c'::s_authorized
x/8xw 'session.c'::s_key
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
continue
```

**Step 5. Type the keys again on the terminal.**

* Case a. Type `validpassword1`. It must be accepted.
* Case b. The login screen is shown. Type `validpassword1`. It must be accepted.
* Case c. The login screen is shown. First type the old key `validpassword1`. It must
  be refused. Then type the new key `newpassword22`. It must be accepted.

### Result

The test **passes** when all of this is true.

* Case a. The correct key did **not** log you in. `s_authorized` is `0x0` and the
  key is zeros. The login screen is shown. Typing the key again works.
* Case b. The write happened. Partition A is active with version 1. The login
  screen is shown (not the first setup). The key `validpassword1` opens the device.
* Case c. The write happened. The active partition changed and its version grew by
  1. The login screen is shown. `newpassword22` opens the device. The old key
  `validpassword1` does not.

### If the result is different

* **Case a logs you in.** This is a serious problem. The step that stores the
  session is not protected against the panic button.
* **In case b or case c the login screen shows the first setup, or a fingerprint is
  wrong.** The start routine read the flash before the write was finished.

## T-C5.5 A panic at every step of a flash write. Which partition is written?

### What this test checks

This test answers the question "when the button is pressed during a write, which
partition is being written, and is it the right one?". A key change is a write to
flash. It has five steps. The test stops at each of the five steps with a
breakpoint, and it presses the panic button there. The write must always go to the
**inactive** partition. After the panic the write finishes, and everything must be
consistent.

### Before you start

* You are logged in with key A and the menu is shown. Partition B is active (version
  2) with the full table, and partition A has version 1.
* You will change the key five times. The new keys are `changekey01`, `changekey02`,
  `changekey03`, `changekey04` and `changekey05`. After every run you log in with
  the new key. So the old key of run 2 is `changekey01`, and so on.

### Steps

**How to get the starting situation (do this again before every run).**

```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
continue
```

Type these three commands at B0. On the terminal type `validpassword1` and press
Enter, and type it again to confirm. When the menu is shown, type these commands in
gdb.

```
interrupt
restore tools/mock/table_full.bin binary $tbl
call (int)Partition_Store_Commit('session.c'::s_key, 'partition_store.c'::s_active.header.salt, 'partition_store.c'::s_active.header.kdf_iter, 'partition_store.c'::s_active.header.auth, &'mode_retrieve.c'::s_table)
continue
```

The third command must return 1. Now partition B is active (version 2) and holds the
full table with key A. Partition A has version 1.

Do these steps for each of the five runs of the table.

| Run | Step of the write | Breakpoint command | New key |
|---|---|---|---|
| 1 | Before the erase | `break Flash_Drv_EraseSector` | `changekey01` |
| 2 | The sector is erased. The header is next. | `break Flash_Drv_Write if address == $tgt` | `changekey02` |
| 3 | The header is written. The table is next. | `break Flash_Drv_Write if address == $tgt + 0x40` | `changekey03` |
| 4 | The table is written. The check is next. | `break ReadSlot` | `changekey04` |
| 5 | The write is checked and done. The session opens next. | `break OpenIfCurrent` | `changekey05` |

**Step 1. Record the state and find the target sector.**

```
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
set $tgt = ('partition_store.c'::s_active.sector == 2) ? 0x0800C000 : 0x08008000
print/x $tgt
```

The first four lines show the active partition and the fingerprints. The last two
lines calculate the start address of the target partition. It is the partition that
is **not** active. The output looks like this for the first run.

```
$1 = 3
$2 = 2
$3 = 0x20ae075f
$4 = 0x382f0110
$5 = 0x8008000
```

**Step 2. Set the breakpoint of the run.**

```
delete
```

Then type the breakpoint command of the run from the table. Then type `continue`.

**Step 3. Start the key change on the terminal.**

Type `3` and press Enter. Type the new key of the run and press Enter. Type it again
and press Enter. gdb stops at the breakpoint.

**Step 4. Look at the write before you press the button.**

For run 1 type `print sector`. For runs 2 and 3 type `print/x address`. For runs 4
and 5 there is no such value. Then type these commands for every run.

```
print 'partition_store.c'::s_active.sector
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
```

The first line shows the sector that is active now. The next lines show the
fingerprints. The write goes to the sector that is **not** active. In run 1 the
sector to be erased (`sector`) must not be the active sector. In runs 2 and 3 the
address must be inside the target partition (`$tgt`). In run 1 the target sector
still has its old fingerprint. In run 2 the target is blank (`0x34132f69`). In run 3
it has a header but no table. In runs 4 and 5 it is complete.

**Step 5. Press the panic button.**

```
delete
set {unsigned int}0x40013C10 = 0x400
continue
```

**Step 6. Wait for the login screen and look at the flash.**

```
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
x/2xw 0x08008000
x/2xw 0x0800C000
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
continue
```

The commands show the active partition, the first two words of each partition, the
fingerprints, the integrity of both partitions, and the state of the session.

**Step 7. Try the old key and then the new key on the terminal.**

The terminal shows the login screen. Type the old key. The screen must show `Wrong
master key`. Then type the new key. The screen must show `Access granted`. Type `1`
and `7` and `q` to check that entry 7 is unchanged.

### Result

The test **passes** for a run when all of this is true.

* At the breakpoint the write goes to the inactive partition and not to the active
  one.
* After the panic the login screen is shown. The active partition is the target
  partition. Its version is one higher than before.
* The fingerprint of the other partition is the same as before the run.
* Both partitions pass the integrity check.
* The new key opens the device and the old key does not. Entry 7 is unchanged.
* After the panic `s_authorized` is `0x0` and the session key is all zeros.

The panic never stops a write that is running. The interrupt returns and the write
finishes. So the new key is valid after every run.

### If the result is different

* **At the breakpoint the address or the sector belongs to the active partition.**
  This is a serious problem. The write overwrites the copy that is in use. A power
  loss at this moment would destroy all data. The choice of the target in
  `Partition_Store_Commit` is wrong, or `s_have_active` is old.
* **After the panic neither key or both keys open the device.** A half-finished
  state was used. Rule 4 is broken.
* **The active partition in RAM and the partition in flash disagree.** The firmware
  switched to the new partition without checking it.
* **The integrity numbers of the target partition are different.** The write ended
  with an error that the flash driver did not report. See known problem G8 in Part 1.

## T-C5.6 A panic at every step of the very first setup

### What this test checks

This is the same test as T-C5.5, but on a blank device. There is no active
partition, so the write must go to partition A. Partition B must stay blank at every
step.

### Before you start

* Erase both partitions at B0 and type `continue`. The terminal shows the first
  setup screen.

### Steps

Do these steps for each of the five breakpoints of T-C5.5. Use `$tgt = 0x08008000`
by typing `set $tgt = 0x08008000`.

**Step 1. Set the breakpoint.**

```
interrupt
delete
set $tgt = 0x08008000
```

Then type the breakpoint command of the run from the table in T-C5.5. Then type
`continue`.

**Step 2. Start the setup on the terminal.**

Type `validpassword1` and press Enter. Type it again and press Enter. gdb stops at
the breakpoint.

**Step 3. Look at the write and press the button.**

```
print 'partition_store.c'::s_have_active
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
delete
set {unsigned int}0x40013C10 = 0x400
continue
```

There is no active partition, so `s_have_active` is `false`. Partition B is blank
(`0x34132f69`).

**Step 4. Look at the result.**

```
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 0x4000), CRC_Drv_Result())
continue
```

Then type `validpassword1` at the login screen. Type `1`.

**Step 5. Erase both partitions again** before the next breakpoint.

```
interrupt
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
monitor reset
continue
```

### Result

The test **passes** when all of this is true at every breakpoint.

* The write goes to partition A. Partition B stays blank (`0x34132f69`).
* After the panic the screen shows `== LOCKED ==`, because the write finished and a
  partition now exists.
* The key `validpassword1` opens the device. The table is empty, and the screen
  shows `(empty)`.

### If the result is different

* **Partition B is written.** The first write picked the wrong sector.
* **The screen shows `== FIRST TIME SETUP ==` although the write finished.** The
  start routine ran before the write was finished.

## T-C5.7 Power loss at every step of a flash write

### What this test checks

A power failure stops the processor at any moment. The device must then hold either
the old key with the old data, or the new key with the new data. It must never hold
a mixture, and it must never hold nothing. In this test you stop the processor with
a reset at each step of the write.

### Before you start

* You are logged in with key A and the menu is shown. Partition B is active (version
  2) with the full table, and partition A has version 1.
* You know the five breakpoints of test T-C5.5.

### Steps

**How to get the starting situation (do this again before every run).**

```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
continue
```

Type these three commands at B0. On the terminal type `validpassword1` and press
Enter, and type it again to confirm. When the menu is shown, type these commands in
gdb.

```
interrupt
restore tools/mock/table_full.bin binary $tbl
call (int)Partition_Store_Commit('session.c'::s_key, 'partition_store.c'::s_active.header.salt, 'partition_store.c'::s_active.header.kdf_iter, 'partition_store.c'::s_active.header.auth, &'mode_retrieve.c'::s_table)
continue
```

The third command must return 1. Now partition B is active (version 2) and holds the
full table with key A. Partition A has version 1.

Do these steps for each of the five steps of the write, and for one more step in
the middle of the table. Get the starting situation again before every try.

**Step 1. Set the breakpoint.**

```
interrupt
set $tgt = ('partition_store.c'::s_active.sector == 2) ? 0x0800C000 : 0x08008000
delete
```

Then type the breakpoint command of the step. For the five steps use the commands of
the table in T-C5.5. For the extra step in the middle of the table, first type
`list Flash_Drv_Write`. Find the line with the text
`*(volatile uint32_t *)(address + i) = word;`. Note its line number `N`. Then type
this command with your number.

```
break flash_drv.c:N if address == $tgt + 0x40 && i == 800
```

This stops when about 800 bytes of the table are written. Type `continue`.

**Step 2. Start the key change on the terminal.**

Type `3`. Type `changekey01` twice. gdb stops at the breakpoint.

**Step 3. Simulate the power loss.**

```
delete
monitor reset
continue
```

The reset stops everything at once. Nothing more is written. The board restarts.

**Step 4. Try the old key and the new key.**

On the terminal type the old key `validpassword1`. Then reset the board again and
type the new key `changekey01`. Note which one opens the device.

**Step 5. Look at the flash.**

```
interrupt
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
x/2xw 0x08008000
x/2xw 0x0800C000
continue
```

### Result

The test **passes** when exactly one key opens the device and the table is intact.
The table shows which key must work.

| Power lost at | The target partition afterwards | The key that works |
|---|---|---|
| Before the erase | unchanged, still the old valid partition | the **old** key |
| After the erase | blank | the old key |
| After the header | a header only, the integrity numbers differ | the old key |
| In the middle of the table | a header and half a table, the integrity numbers differ | the old key |
| After the table, before the check | complete and valid, one version higher | the **new** key |

For the very first setup on a blank device, repeat the test with these results. For
the first four rows the first setup screen is shown again, because nothing valid was
written. For the last row the key that you set opens the device.

### If the result is different

* **Neither key works, or both work.** The header and the table do not agree. The
  CRC does not cover both.
* **The old key fails after an early power loss.** The write changed the active
  partition.
* **The table is wrong although the key is right.** The table was encrypted with
  another key than the one in the header.

## T-C5.8 The real button during a calculation and during a write

### What this test checks

The earlier tests press the button from gdb. This test uses the real button, at
moments that you cannot control exactly. It also measures how fast the board reacts.

### Before you start

* You have a stopwatch. The board runs and the terminal is open.

### Steps

**Step 1. During a login.** Log out (reset the board). Type the correct key. While
the screen shows `Checking, please wait...`, press the button. Measure the time from
the press to the login prompt.

**Step 2. During a first setup or a key change.** While the screen shows `Deriving
key, please wait...` or `Re-encrypting, please wait...`, press the button. Look at
the flash afterwards, as in T-C5.3.

**Step 3. During the flash write.** The flash write is at the end of `Re-encrypting`
and takes only a few hundred milliseconds. Press the button as soon as the
calculation seems to be over. Repeat the key change until one press lands in the
write. Afterwards check which key works.

**Step 4. During a table listing.** Log in, type `1`, and press the button while the
list is printed.

### Result

The test **passes** when all of this is true.

* Step 1. The login prompt is back within about 0.1 to 0.3 seconds.
* Step 2. Nothing was written and both fingerprints are unchanged. The old key still
  works.
* Step 3. The prompt appears only after the flash operation finishes. This can take
  up to about a second, because the processor stops while flash is erased or
  written. Afterwards the new key works.
* Step 4. The list may finish printing, because the bytes were already given to the
  DMA. Then the login screen appears. The menu never appears.

### If the result is different

* **The button does nothing until the calculation ends.** The interrupt priority is
  not the highest, or interrupts are switched off during the whole calculation.
* **The device does not work after step 3.** See test T-C5.7. Neither key must ever
  be lost.

## T-C5.9 Many panics at random moments

### What this test checks

This is a stress test. After many panics the flash must still be in a good state
and the device must still work.

### Before you start

* You are logged in with key A and the menu is shown.

### Steps

**Step 1. Do 20 rounds.** In each round, log in. Choose any mode (retrieve, change
key, or the first setup after erasing both partitions). Press the real button at a
random moment.

**Step 2. After every 5 rounds, reset the board and look at the flash.**

```
monitor reset
continue
interrupt
print 'partition_store.c'::s_active.sector
print 'partition_store.c'::s_active.header.version
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x08008000, 60), CRC_Drv_Feed((unsigned char*)0x08008040, 1488), CRC_Drv_Result())
x/1xw 0x0800803C
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
continue
```

**Step 3. Try the keys that you used.** Exactly one of them must open the device.

### Result

The test **passes** when all of this is true.

* Every partition is either intact or blank.
* The active partition has the highest valid version.
* Exactly one of your keys opens the device.
* The table is intact.
* The device never hangs.

### If the result is different

* **The active partition fails the integrity check.** An interrupted write was
  accepted as good.
* **The device hangs.** A panic during a UART transfer or a calculation left a wait
  loop that never ends. Look at `USART_Drv_WaitTxReady`.

## T-C5.10 A panic in the middle of a table listing

### What this test checks

This is a small display problem and it is **PREDICTED**. I did not run it. The
panic wipes the table before the list is printed. The program then prints an empty
list.

### Before you start

* You are logged in and the menu is shown.

### Steps

**Step 1. Stop the program before the list is printed.**

```
interrupt
delete
break Password_Table_ShowEntries
continue
```

**Step 2. Ask for the list and press the button.**

On the terminal type `1`. gdb stops. Then type these commands.

```
delete
set {unsigned int}0x40013C10 = 0x400
continue
```

### Result

I expect this output on the terminal. The wipe empties the table just before the
list is formatted. So the list is empty. After that the login screen appears.

```
-- Password table --
  (empty)

Enter id to view (0-30), or 'q' to go back:
== LOCKED ==
Master key:
```

This does no harm, because no secret is printed. But it looks confusing.

### If the result is different

* **Real entries appear after the panic.** The wipe ran after the list was
  formatted. The bytes may be sent, but the line buffer must still be all zeros
  afterwards. Run test T-C6.2.

---

# C6 — Damage and wipe

This layer answers two questions. First, after logins, failed logins and panics, is
any secret still readable in RAM? Second, when flash is damaged, does the firmware
notice, and what does it do with the data?

For the RAM searches use these byte lists. Do not search for quoted text, because
`find` would also look for the zero byte at the end of the text (see "Things to
know", note 7).

| What you look for | Byte list |
|---|---|
| The start of the master key `validpassword1` | `'v','a','l','i','d','p','a','s','s'` |
| The start of the master key `newpassword22` | `'n','e','w','p','a','s','s'` |
| The start of a password of the table (entry 7) | `'p','w','0','7','!','@','#','$'` |
| The start of a name of the table (entry 7) | `'S','0','7','a','b','c','d','e'` |

## T-C6.1 After a login, only the session key exists in RAM

### What this test checks

The master key that you type, and the values that the key calculation makes on the
way, must not stay in RAM. They can be in copies of the input line, in the UART
receive buffer, or in local variables of the calculation. Only the session key may
exist, and only once. After you leave the retrieve mode, the table and the password
line must be gone.

### Before you start

* The device holds a partition with key A (`validpassword1`) and the full table. If
  it does not, do the setup of test T-C4.6 first.
* Reset the board to B0 and clear the unused RAM. Old secrets can stay in RAM after
  a reset. In gdb type these commands.

```
tbreak App_Run
monitor reset
continue
call (void*)memset(&_ebss, 0, (unsigned int)$sp - 256 - (unsigned int)&_ebss)
```

The last line fills the unused RAM with zeros. The output looks like
`$1 = (void *) 0x200018c4`.

### Steps

**Step 1. Log in and read one entry.**

```
continue
```

On the terminal log in with `validpassword1`. Type `1`, then `7`, and then `q`. You
are back at the menu.

**Step 2. Halt the board and remember the first 8 bytes of the session key.**

```
interrupt
set $k0 = 'session.c'::s_key[0]
set $k1 = 'session.c'::s_key[1]
set $k2 = 'session.c'::s_key[2]
set $k3 = 'session.c'::s_key[3]
set $k4 = 'session.c'::s_key[4]
set $k5 = 'session.c'::s_key[5]
set $k6 = 'session.c'::s_key[6]
set $k7 = 'session.c'::s_key[7]
```

These commands store the bytes in gdb variables. Then you can search for them
after the key was wiped from its normal place.

**Step 3. Search all of RAM for the key bytes.**

```
find /b 0x20000000, +0x20000, $k0, $k1, $k2, $k3, $k4, $k5, $k6, $k7
```

The search takes a few seconds. The output looks like this.

```
0x20000a40 <s_key>
1 pattern found.
```

**Step 4. Search RAM for the master key text and for the table text.**

```
find /b 0x20000000, +0x20000, 'v','a','l','i','d','p','a','s','s'
find /b 0x20000000, +0x20000, 'p','w','0','7','!','@','#','$'
find /b 0x20000000, +0x20000, 'S','0','7','a','b','c','d','e'
```

Each command must print `Pattern not found.`.

### Result

The test **passes** when the key bytes are found exactly once (at `s_key`) and the
three searches of step 4 find nothing.

### If the result is different

* **The key bytes are found more than once.** A copy of the key exists somewhere
  else. It can be a local variable of the key calculation (`k`, `auth`, `enc`, `u` or
  `t`) that was not zeroed. Type `info symbol` and the address of each extra hit. The
  name shows where the copy is.
* **The master key text is found.** The typed line is still in RAM. The name in
  `info symbol` tells you which buffer. It can be `s_rx_buf`, `s_first` or `s_new`.
* **The table text is found.** The wipe on the way out of the retrieve mode is
  missing. Look at `Mode_Retrieve_Wipe` and `Password_Table_WipeScratch`.

Keep the gdb variables `$k0` to `$k7`. The next test uses them. Type `continue`.

## T-C6.2 After the panic button, nothing is left

### What this test checks

In Part 1, test T9.4 found the printed line `name : password` still in the line
buffer after the panic button. That was a known problem (G1). The Stage C firmware
wipes the line buffer, the session key, the receive buffer and the buffers of the
modes. This test checks it on the board.

### Before you start

* You finished T-C6.1. The variables `$k0` to `$k7` hold the session key bytes.
* You are logged in and the menu is shown. Type `1` and then `7`, so that the table
  and the password line are in RAM. Do not type `q`.

### Steps

**Step 1. Press the panic button.**

```
interrupt
set {unsigned int}0x40013C10 = 0x400
continue
```

Wait for the login screen. Then type `interrupt` again.

**Step 2. Search RAM again.**

```
find /b 0x20000000, +0x20000, $k0, $k1, $k2, $k3, $k4, $k5, $k6, $k7
find /b 0x20000000, +0x20000, 'p','w','0','7','!','@','#','$'
find /b 0x20000000, +0x20000, 'S','0','7','a','b','c','d','e'
find /b 0x20000000, +0x20000, 'v','a','l','i','d','p','a','s','s'
```

**Step 3. Look at all the secret places.**

```
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
x/8xw 'mode_first_meet.c'::s_first
x/8xw 'mode_change_mk.c'::s_new
print/x (CRC_Drv_Reset(), CRC_Drv_Feed(&'mode_change_mk.c'::s_work, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($buf, 1024), CRC_Drv_Result())
x/16xw 'usart_drv.c'::s_rx_buf
continue
```

### Result

The test **passes** when every search of step 2 prints `Pattern not found.`, and step
3 shows that everything is wiped. This means `s_authorized` is `0x0`, the places
with eight words and the receive buffer show only `0x00000000`, both table numbers
are `0xbb034f8e`, and the line buffer number is `0x8b0a5208`.

Some data can stay in the stack, below the address `0x20020000`. I predict that this
is possible on the board, because the C library function `snprintf` uses stack space
that the PC simulation did not include. If `info symbol` shows an address in the
variables area (not in the stack), that is a buffer that was missed. An address in the
stack is a leftover that you should write down.

### If the result is different

* **A hit is in `s_line_buf`, `s_table`, `s_rx_buf` or `s_key`.** The panic function
  does not call the wipe of that module. Look at `App_PanicHandler`.
* **The wipe happens before the buffer is filled again.** This can happen if the
  panic comes during the printing of a password (see test T-C5.10).

## T-C6.3 Failed logins leave no key material behind

### What this test checks

A wrong key still starts a full key calculation. The values that it makes along the
way come from the wrong guess. They must be erased even when the login fails. To
find them you first calculate what the wrong key would give. You remember the first
bytes. Then you destroy your own copy and check that the firmware left no copy.

### Before you start

* The device holds a partition with key A in partition A. If the active partition is
  B, use the address `0x0800C00C` instead of `0x0800800C` in step 1.
* Reset the board to B0 and clear the unused RAM. Then type `continue`. The
  terminal shows the login screen.

```
tbreak App_Run
monitor reset
continue
call (void*)memset(&_ebss, 0, (unsigned int)$sp - 256 - (unsigned int)&_ebss)
continue
```

### Steps

**Step 1. Calculate the derived key of the wrong guess and remember its first bytes.**

```
interrupt
restore tools/mock/mk_a.bin binary $buf
set var $buf[13] = '2'
call (int)Kdf_DeriveKeys($buf, 14, (unsigned char*)0x0800800C, 2000, 0, $buf+128, $buf+160)
set $w0 = $buf[160]
set $w1 = $buf[161]
set $w2 = $buf[162]
set $w3 = $buf[163]
set $w4 = $buf[164]
set $w5 = $buf[165]
set $w6 = $buf[166]
set $w7 = $buf[167]
call (void*)memset($buf, 0, 1024)
```

The second and third lines make the master key `validpassword2`, which is wrong.
The fourth line calculates its keys with the salt that is stored in flash. The next
eight lines remember the first bytes of the `enc` value in gdb variables. The last
line erases your own copy, so it cannot cause a false match.

**Step 2. Do a failed login on the terminal.**

```
continue
```

On the terminal type `validpassword2` and press Enter. The screen shows `Wrong master
key`.

**Step 3. Search RAM.**

```
interrupt
find /b 0x20000000, +0x20000, $w0, $w1, $w2, $w3, $w4, $w5, $w6, $w7
find /b 0x20000000, +0x20000, 'v','a','l','i','d','p','a','s','s'
print/x 'session.c'::s_authorized
x/8xw 'session.c'::s_key
continue
```

### Result

The test **passes** when both searches print `Pattern not found.`, `s_authorized` is
`0x0`, and the session key is all zeros.

### If the result is different

* **A hit in the stack.** The functions `Kdf_DeriveKeys` and `Session_Authenticate`
  clear their local variables only when the login works. They must also clear them
  when it fails.

## T-C6.4 Damage that happens while the device runs is not noticed (KNOWN PROBLEM G11)

### What this test checks

Damage can also happen while the device is on, for example from a stray write. The
check of the CRC is done only when the device starts. When you later read the table,
`Partition_Store_Load` decrypts what is in flash without checking the CRC again. This
test shows what the user then sees. It is a **KNOWN PROBLEM**.

### Before you start

* You are logged in with key A and the menu is shown. Partition B is active (version
  2) with the full table, and partition A has version 1 with an empty table.
* You typed `set {unsigned int}$z = 0`.

### Steps

**Step 1. Damage one word of the table in the active partition.**

```
interrupt
set {unsigned int}$z = 0
call (void)Flash_Drv_Write(0x0800C040 + 200, $z, 4)
```

Byte 200 of the table lies in the name of entry 4. The command writes four zero
bytes there.

**Step 2. Read the table with the session key and check it.**

```
call (int)Session_LoadTable(&'mode_retrieve.c'::s_table)
print/x (CRC_Drv_Reset(), CRC_Drv_Feed($tbl, 1488), CRC_Drv_Result())
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
continue
```

I expect this output.

```
$1 = 1
$2 = 0x3c8e1a55
$3 = 0x5ab31c0e
0x800c03c:	0xef084ab1
```

The load returns 1, so it reports success. But the table CRC is not `0x60085d29`, and
the integrity check of partition B fails (the two numbers differ). The numbers in the
example are different on your board.

**Step 3. Look at the screen.**

On the terminal type `1`. Look at the name of entry 4 in the list.

**Step 4. Restart and log in.**

In gdb type `monitor reset` and `continue`. Log in with `validpassword1`. Type `1`.

### Result

This is a **KNOWN PROBLEM**. Today the load returns 1 without an error. The name of
entry 4 is garbled on the screen. After the restart the firmware finds that partition
B is damaged and uses partition A instead. Then the table is empty, because that is
the old data. Everything that was added since is lost. This is the price of the
two-partition design, and there is no third copy.

### If the result is different

* **`Session_LoadTable` returns 0, or the screen shows a message about damage.** The
  problem G11 is fixed. Update this test so that it expects this behaviour.
* **The table CRC is `0x60085d29` after the damage.** The word that you damaged
  was already zero, or you wrote to the wrong address. Try another offset inside
  the table.

### What next

Make `Partition_Store_Load` check the CRC again (with `ValidateCrc`) and return
`false` when it does not match. Then `Mode_Retrieve_Enter` wipes the table and the
program goes to the start routine. That routine uses the other partition, as it does
at boot. The user should see a message.

## T-C6.5 After a key change, the old partition still opens with the old key (KNOWN PROBLEM G10)

### What this test checks

A user changes the master key when the old key may be known to someone else. But
after the change, the older partition still holds a valid header and the data, and it
is encrypted with the **old** key. If the new partition becomes unusable, the firmware
uses the old partition and the old key opens the data again. This test shows it.

### Before you start

* You are logged in with key A and the full table (partition B is active, version 2).
* Change the key to `newpassword22` with menu option 3. Partition A is now active with
  version 3. Partition B has version 2 and still uses the old key.
* You typed `set {unsigned int}$z = 0`.

### Steps

**Step 1. Check that the old partition is still intact.**

```
interrupt
print/x (CRC_Drv_Reset(), CRC_Drv_Feed((unsigned char*)0x0800C000, 60), CRC_Drv_Feed((unsigned char*)0x0800C040, 1488), CRC_Drv_Result())
x/1xw 0x0800C03C
```

The two numbers must be equal (`0xef084ab1` in the fixed test, another value on your
board). This means that the old partition is intact.

**Step 2. Damage the new partition and restart.**

```
call (void)Flash_Drv_Write(0x0800803C, $z, 4)
monitor reset
continue
```

The first line writes zeros over the stored CRC of partition A, which is the new
partition. The next lines restart the board.

**Step 3. Try both keys on the terminal.**

At the login screen type `newpassword22`. Then type `validpassword1`.

### Result

This is a **KNOWN PROBLEM**. Today `newpassword22` is refused, and the old key
`validpassword1` gives `Access granted` and opens the table.

### If the result is different

* **`validpassword1` is refused after step 2.** The problem G10 is fixed. Then the
  old partition no longer opens with the old key. Update this test to expect the
  first setup screen.
* **`newpassword22` is accepted after step 2.** The damage in step 2 did not reach
  the new partition. Check that partition A is the active one and repeat the
  command with the right address.

### What next

After a **verified** key change, erase the old sector. Then the device has one valid
partition until the next write, and the fallback for that one period is lost. When
this is done, this test must show that the old key never opens the data again. After
damaging partition A the device would then show the first setup screen.

## T-C6.6 What the user sees when the damage is found at start

### What this test checks

After a bad write or after bit rot, the device finds the damage when it starts. The
message must make sense, and no plaintext may appear.

### Before you start

* The device is in the situation "Key changed". Partition A is active with version 3
  and key B (`newpassword22`). Partition B has version 2 with key A
  (`validpassword1`).
* You typed `set {unsigned int}$z = 0`.

### Steps

Do these steps for each row of the table.

| Row | Damage | Command |
|---|---|---|
| a | The **inactive** partition B. The stored CRC is zeroed. | `call (void)Flash_Drv_Write(0x0800C03C, $z, 4)` |
| b | The **active** partition A. The stored CRC is zeroed. | `call (void)Flash_Drv_Write(0x0800803C, $z, 4)` |
| c | Both partitions. | the two commands of rows a and b |
| d | Both sectors are erased. | `call (void)Flash_Drv_EraseSector(2)` and `call (void)Flash_Drv_EraseSector(3)` |

**Step 1. Halt the board, do the damage of the row, and restart.**

```
interrupt
```

Type the command of the row. Then type these commands.

```
monitor reset
continue
```

**Step 2. Look at the screen and try the keys.**

Look at the first screen. Type `newpassword22`. Then type `validpassword1`.

**Step 3. Repair the situation for the next row.** Erase both sectors, set up the
situation again, or continue with the next row from the new state.

### Result

The test **passes** when each row gives the result of the table.

| Row | The first screen | The key that opens the device |
|---|---|---|
| a | `== LOCKED ==` | `newpassword22`. The table is intact. The next write repairs partition B. |
| b | `== LOCKED ==` | only the **old** key `validpassword1`, with the older data. This is the same problem as in test T-C6.5. |
| c | `== FIRST TIME SETUP ==` | none. You can choose a new key. Old encrypted data is still in flash but it cannot be read. |
| d | `== FIRST TIME SETUP ==` | none. You can choose a new key. |

After row c, set a new key and check that the write went to partition A only.
Partition B still holds the unreadable old data until the next write.

### If the result is different

* **A screen shows data that looks valid but is wrong.** A partition with a bad CRC
  was used.
* **The first setup screen appears although a valid partition exists.** The start
  routine decided from an old copy in RAM.

## T-C6.7 No plaintext and no key text in flash at the end

### What this test checks

After all these tests, flash must still not hold any secret in readable form. It must
also not hold a master key inside the program.

### Before you start

* You finished the other tests. The board is halted.

### Steps

**Step 1. Search the two partitions.**

```
interrupt
find /b 0x08008000, +0x8000, 'S','0','7','a','b','c','d','e'
find /b 0x08008000, +0x8000, 'p','w','0','7','!','@','#','$'
find /b 0x08008000, +0x8000, 'v','a','l','i','d','p','a','s','s'
find /b 0x08008000, +0x8000, 'n','e','w','p','a','s','s'
```

The commands search for the table text and for both master keys in the partitions.

**Step 2. Search the program area.**

```
find /b 0x08000000, +0x8000, 'v','a','l','i','d','p','a','s','s'
```

This searches the 32 KB where the program code is stored. It checks that no master key
is written into the program.

### Result

The test **passes** when all five searches print `Pattern not found.`

### If the result is different

* **A hit between `0x08008000` and `0x0800ffff`.** Plaintext reached the partitions.
* **A hit between `0x08000000` and `0x08007fff`.** A master key or a password is
  written in the source code of the program.

---

# C7 — Progress report, timings and known problems

## Progress checklist

Copy this table into your report. Write PASS or FAIL in the last column. For a
failure add the id of the known problem and the date.

| Tests | What they cover | Result |
|---|---|---|
| T-C1.1 to T-C1.5 | HMAC and PBKDF2 values, the two keys, the **time**, cancel, safe compare | |
| T-C2.1 to T-C2.4 | blank device, header and encrypted table, **no secret in flash**, decrypt | |
| T-C2.5 | **a write goes to the inactive partition and the active one is not touched** | |
| T-C2.6 to T-C2.9 | **integrity**, damage of every field, interrupted write, both partitions invalid | |
| T-C3.1 to T-C3.3 | key rules, login (right, wrong, invalid), no partition, stored iterations | |
| T-C4.1 to T-C4.3 | first setup, what was stored, login | |
| T-C4.4 and T-C4.5 | the login cannot be skipped, closing the session | |
| T-C4.6 and T-C4.7 | reading passwords, the menu | |
| T-C4.8 to T-C4.10 | key change (refused, successful, repeated) | |
| T-C4.11 | stack depth | |
| T-C5.1 to T-C5.4 | **panic button**. The trick from gdb, every prompt, during a calculation, one step before the session opens | |
| T-C5.5 and T-C5.6 | **panic at every step of a write. The written partition and the active partition** | |
| T-C5.7 | **power loss at every step of a write** | |
| T-C5.8 to T-C5.10 | the real button, many panics, the listing quirk | |
| T-C6.1 to T-C6.3 | **RAM search** after a login, after the panic button, after failed logins | |
| T-C6.4 to T-C6.6 | damage while running (G11), the old partition (G10), damage at start | |
| T-C6.7 | no plaintext and no key text in flash | |

## Timing table

Fill this in from tests T-C1.3 and T-C4.3.

| Build | Iterations | Time of one key calculation | Wrong guesses per hour (3600 divided by the seconds) |
|---|---|---|---|
| Debug (no optimisation) | 2000 | | |
| Release (optimised) | 2000 | | |
| The value you choose | | | |

## Known problems

These problems are new in Stage C, or they come from Part 1 and are still open.

| Id | Problem | Shown by | How to fix it |
|---|---|---|---|
| G10 | After a key change the older partition still opens with the **old** key. | T-C6.5 and T-C6.6 row b | Erase the old sector after a verified change. |
| G11 | `Partition_Store_Load` does not check the CRC again. Damage that happens after start is decrypted to garbage without an error. | T-C6.4 | Check the CRC in the load function. On a failure wipe and go to the start routine. |
| G12 | A device with no valid partition offers the first setup to whoever connects first. Old invalid data can stay in the other sector. | T-C6.6 row c | This cannot be avoided for a device that has no key. Write it in the documentation. You can also erase invalid sectors at start. |
| G13 | `Session_Save` is not in the program until the GENERATE_MODE branch uses it. The tests call `Partition_Store_Commit` directly. | Things to know, note 3 | Nothing to do. It will be available after the merge. |

These problems from Part 1 are fixed in Stage C. The tests above confirm the fixes on
the board.

| Id | Problem | Confirmed by |
|---|---|---|
| G1 | The printed password line stayed in RAM after the panic button. | T-C6.2 |
| G2 | The id check accepted `abc` or `0x5` as entry 0. | T-C4.6 |
| G4 | A write was reported as successful without a check. | T-C2.5 and T-C5.5 |
| G5 | The key was not checked before the table was decrypted. | T-C3.2 |
| G9 | The menu accepted `12` as `1`. | T-C4.7 |

These problems from Part 1 are still open.

| Id | Problem |
|---|---|
| G3 | A name without a zero byte at the end runs into the next field when it is printed. |
| G6 | The UART receive buffer holds only one line. |
| G7 | A button press within 200 ms of the start is ignored. |
| G8 | The flash driver ignores the flash status errors. |

When the GENERATE_MODE branch is merged, add tests for three things. The first is a
panic during the generation of a password. The second is the toggle of the partition
after `Session_Save`. The third is that the generated password is not left in RAM after
you leave the mode.
