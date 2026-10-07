# Integration test sets: login + GENERATE_MODE + partition store + retrieve

Manual tests for the merged firmware on branch `integrate-mk`: master-key
login (FIRST_MEET / MK_AUTH), change of master key, the ADC-noise password
generator, A/B flash partitions, the encrypted table, retrieve mode and the
panic button. Everything here is done from a serial terminal, with a few
optional wiring checks. No debugger is needed.

**Read this first.** This firmware uses a new flash layout (`RUN2`), so the
first boot after flashing does **not** know any old entries: it asks you to
choose a master key and starts with an empty table. Every test id below starts
unused. Pick a master key you will retype often, for example `testkey123`.

How to use: work through the sets in order, tick the box when the result
matches, write anything odd next to it. If something fails, report the **test
ID, what you typed, and what you saw**.

## 0. Setup

### 0.1 Terminal (PuTTY, Linux)
| Setting | Value |
|---|---|
| Connection type / line / speed | Serial, `/dev/ttyACM0`, `115200` |
| Serial category | 8 data bits, 1 stop bit, Parity None, Flow control None |
| Terminal > Local echo | **Force off** |
| Terminal > Local line editing | **Force off** |

Both are now **off**. The firmware ends a line at Enter, so typing one key at
a time works; and with local echo off your master key is not shown on screen
as you type it. Nothing you type is echoed back — that is expected.

Copy a password: drag over it, then Ctrl+Insert, paste elsewhere with Ctrl+V.
Paste into PuTTY: Shift+Insert or middle-click. **Send one line at a time**; a
second line sent before the first is processed is dropped (known limit).

### 0.2 Wiring
| Signal | Pin | Needed for |
|---|---|---|
| NTC thermistor divider | PA0 (A0) | entropy source |
| LDR divider | PA1 (A1) | entropy source |
| Panic button to GND | PA10 (D2) | set 9 (external button, active-low) |

### 0.3 Starting state
Press the black RESET button. On a freshly flashed board you should see:

```
== FIRST TIME SETUP ==
Choose a master key (8-31 printable characters).
Master key:
```

Type your master key, press Enter, type it again at `Confirm master key:`.
After a "Deriving key, please wait..." pause of roughly 1.5-4 s you get
`Master key set (nnnn ms).` and the menu:

```
== MODE SELECTION ==
  1) Retrieve password
  2) Generate password
  3) Change master key
Select:
```

On every later boot you get `== LOCKED ==` and a `Master key:` prompt instead.
The table starts empty, so the tests below can use any id.

### 0.4 Optional: clean board (erases the master key and saved passwords, not the firmware)
```
CLI=/opt/st/stm32cubeide_2.2.0/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.2.500.202603051304/tools/bin/STM32_Programmer_CLI
$CLI -c port=SWD mode=UR -e 2 3
```
Then press RESET. The board forgets the master key and starts again at
`== FIRST TIME SETUP ==`. If the tool rejects the syntax, skip this step and
tell me.

## Results at a glance
| Set | What it proves | Result |
|---|---|---|
| 0b | Login: first setup, wrong key, right key | [ ] |
| 1 | Boot, menu, retrieve | [ ] |
| 2 | Generate works and saves | [ ] |
| 3 | Every character class and both length limits | [ ] |
| 4 | Bad input is rejected, nothing saved | [ ] |
| 5 | Overwrite is confirmed first | [ ] |
| 6 | Cancel at every prompt saves nothing | [ ] |
| 7 | Saved data survives reset and power loss | [ ] |
| 8 | Output really looks random | [ ] |
| 9 | Panic button (needs the button) | [ ] |
| 10 | Bad or missing sensor is caught (needs jumper wire) | [ ] |
| 11 | Repeated use and limits | [ ] |
| 12 | Change of master key | [ ] |

---

## Set 0b: The login

| ID | Steps | Expected | OK |
|---|---|---|---|
| 0b.1 | On a clean board, at `Master key:` send `short` | `Invalid master key: use 8-31 printable characters.` | [ ] |
| 0b.2 | Send your key, then a **different** key at the confirm prompt | `The two entries do not match. Start again.` | [ ] |
| 0b.3 | Send your key twice | `Master key set (nnnn ms).` then the menu | [ ] |
| 0b.4 | Press RESET | `== LOCKED ==` and `Master key:` — not the menu | [ ] |
| 0b.5 | Send a wrong key | `Wrong master key (nnnn ms).` and the prompt again; the menu is never shown | [ ] |
| 0b.6 | Send the right key | `Access granted (nnnn ms).` then the menu | [ ] |

Note the times printed in 0b.3 and 0b.6; they belong in the progress report.

## Set 1: Boot, menu and retrieve

| ID | Steps | Expected | OK |
|---|---|---|---|
| 1.1 | Log in and reach the menu | Line 2 is `2) Generate password`, line 3 is `3) Change master key`, neither says "not implemented" | [ ] |
| 1.2 | Send `x` | `Unknown option.` then the menu again | [ ] |
| 1.3 | Send `12` | `Unknown option.` — the whole line must match, so this must **not** open retrieve | [ ] |
| 1.4 | Send `1` | `-- Password table --` then `  (empty)` on a fresh board, then `Enter id to view (0-30), or 'q' to go back:` | [ ] |
| 1.5 | Send `abc` | `Invalid id.` — it must **not** show entry 0 | [ ] |
| 1.6 | Send `99` | `Invalid id.`, prompt again | [ ] |
| 1.7 | Send `30` while id 30 is unused | `(no entry at that id)` | [ ] |
| 1.8 | Send `q` | Menu again | [ ] |

## Set 2: Generate, happy path

**2.1 First password**
Send: `2`, `20`, `github`, `luds`, `16` (Enter after each).

Expected after each line, in order:
```
Id to write (0-30), or 'q' to go back:
Service name (1-15 chars, empty line cancels):
Character classes: l=lower u=upper d=digit s=symbol (e.g. luds):
Password length (1-31):
```
Then after about 1-2 seconds (sampling plus the flash write):
```
Saved as id 20 (github).
Password: <16 characters>

== MODE SELECTION ==
```
- [ ] 16 characters exactly, taken from letters, digits and symbols
- [ ] the menu comes back on its own

**2.2 It is really stored**
Send `1`. The listing must contain `  20 - github`. Send `20`.
- [ ] shows `github : ` followed by **exactly the password from 2.1** (compare it
  character by character; symbols such as `\` and `` ` `` are the easy ones to
  get wrong)
- [ ] the older entries are still listed
- [ ] `q` returns to the menu

## Set 3: Character classes and limits

Use Set 2's flow (`2`, id, name, classes, length). After each save, check the
printed password against the rule.

| ID | id | name | classes | length | Password must contain | OK |
|---|---|---|---|---|---|---|
| 3.1 | 21 | `one` | `d` | `1` | exactly 1 digit (this is the case that reported ENTROPY SOURCE FAULT before the merge — see the note under Set 10) | [ ] |
| 3.2 | 22 | `max` | `luds` | `31` | exactly 31 characters (this is the maximum) | [ ] |
| 3.3 | 23 | `lower` | `l` | `20` | only `a-z` | [ ] |
| 3.4 | 24 | `upper` | `u` | `20` | only `A-Z` | [ ] |
| 3.5 | 25 | `digits` | `d` | `20` | only `0-9` | [ ] |
| 3.6 | 26 | `symbols` | `s` | `20` | only ``!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~`` | [ ] |
| 3.7 | 27 | `caps` | `LUDS` | `24` | accepted (capital letters work like lower case) | [ ] |
| 3.8 | 28 | `letdig` | `ld` | `30` | only letters and digits, no symbols | [ ] |
| 3.9 | 29 | `my bank` | `lud` | `12` | name with a space is accepted | [ ] |
| 3.10 | 30 | `abcdefghijklmno` | `luds` | `12` | 15-character name (the maximum) accepted | [ ] |

Then send `1` and confirm all of ids 20-30 are listed with the right names, and
spot-check three of them with the retrieve prompt (each must match what was
printed at generation).
- [ ] listing is complete and correct
- [ ] three spot checks match

## Set 4: Bad input is rejected and nothing is saved

Do the whole set in **one run**. Start `2`, then work down the table. At each
prompt send the bad values first (the prompt must repeat and the mode must not
exit, and the error text must match), then send the valid value to move on:
id `19`, name `t4`, classes `d`, length `5`.

| ID | Prompt | Send | Expected message | OK |
|---|---|---|---|---|
| 4.1 | id | `abc` | `Invalid id, expected 0-30.` | [ ] |
| 4.2 | id | `31` | same | [ ] |
| 4.3 | id | `-1` | same | [ ] |
| 4.4 | id | `99999` | same | [ ] |
| 4.5 | name | `abcdefghijklmnop` (16 chars) | `Name must be 1-15 printable characters.` | [ ] |
| 4.6 | classes | `xyz` | `Pick at least one of l, u, d, s.` | [ ] |
| 4.7 | classes | `123` | same | [ ] |
| 4.8 | length | `0` | `Length must be 1-31.` | [ ] |
| 4.9 | length | `32` | same | [ ] |
| 4.10 | length | `abc` | same | [ ] |

The last valid length finishes the run and saves id 19. Then check in retrieve:
- [ ] exactly one new entry, id 19 named `t4` with a 5-digit password; none of
  the rejected attempts created anything else

## Set 5: Overwrite is confirmed first

| ID | Steps | Expected | OK |
|---|---|---|---|
| 5.1 | `2`, then id `20` | `Id 20 is in use by 'github'. Overwrite? (y/n):` | [ ] |
| 5.2 | Send `n` | Back at `Id to write ...` prompt; nothing changed. Send `q`, then retrieve id 20: still the **original** password from 2.1 | [ ] |
| 5.3 | `2`, `20`, `y`, `github2`, `lud`, `10` | Saves; retrieve id 20 now shows `github2 : <new password>` and the old password is gone | [ ] |
| 5.4 | Id 20 in the listing | Shown once, as `github2` (no duplicate) | [ ] |

## Set 6: Cancel saves nothing

Note the listing first (`1`, read it, `q`). For each row: start `2`, go to the
stated prompt, then cancel. An empty line (just Enter) cancels at every prompt;
`q` also cancels at the id prompt.

| ID | Cancel at | How | Expected | OK |
|---|---|---|---|---|
| 6.1 | id prompt | Enter alone | Menu | [ ] |
| 6.2 | id prompt | `q` | Menu | [ ] |
| 6.3 | name prompt | id `18`, then Enter alone | Menu | [ ] |
| 6.4 | classes prompt | id `18`, name `x`, then Enter alone | Menu | [ ] |
| 6.5 | length prompt | id `18`, name `x`, `d`, then Enter alone | Menu | [ ] |

Known gap: once the length is accepted there is no way to cancel until the
save finishes. That is a logged open item, not a test failure.

- [ ] listing afterwards is identical to the one taken before (no id 18)

## Set 7: Persistence (the partition store)

Each save writes the *other* flash sector with a higher version, so this proves
the A/B write and read-back.

| ID | Steps | Expected | OK |
|---|---|---|---|
| 7.1 | Generate a password at id 20-30 and note it. Press **RESET**. Retrieve it. | Same password | [ ] |
| 7.2 | **Unplug the USB cable**, wait 5 s, plug in, reopen the terminal. Retrieve it. | Same password | [ ] |
| 7.3 | Do 7.2 again after two more saves (so both flash sectors have been used) | All entries still correct | [ ] |
| 7.4 | After all the above, log in and retrieve every id you saved | All of them unchanged by the later saves | [ ] |
| 7.5 | After a power cycle, at `== LOCKED ==` send a **wrong** key | Rejected; the entries are still there after the right key | [ ] |

## Set 8: Does the output look random? [ DO LATER WITH MORE TEST ]

| ID | Steps | Expected | OK |
|---|---|---|---|
| 8.1 | Generate 5 passwords with the same settings (`luds`, 31) | All 5 different | [ ] |
| 8.2 | Generate 10 passwords with classes `d`, length 31 (310 digits). Tally how often each digit 0-9 appears | Each digit about 31 times. Flag it if any digit appears fewer than 12 or more than 52 times | [ ] |
| 8.3 | Look at the 5 from 8.1 | No obvious pattern (repeated blocks, runs of one character, sorted order) | [ ] |
| 8.4 | Cover the LDR with your hand, or warm the NTC with a finger, then generate | Still normal and different each time. This must not fail, since the entropy is debiased | [ ] |

## Set 9: Panic button (PA10 to GND)

The button wipes the RAM copy of the table and forces the menu. It must never
corrupt what is saved in flash.

| ID | Steps | Expected | OK |
|---|---|---|---|
| 9.1 | `2`, id `17`, name `pan`, then **press the button** at the classes prompt | `== LOCKED ==` appears immediately — **not** the menu. The panic destroys the session, so you must log in again | [ ] |
| 9.2 | Log in, retrieve | Listing has **no** id 17, and other entries are intact | [ ] |
| 9.3 | Enter retrieve (`1`), then press the button | `== LOCKED ==` immediately | [ ] |
| 9.4 | Log in, retrieve and view an entry | Still shows the password (panic wipes RAM only, flash is untouched) | [ ] |
| 9.5 | `2`, `17`, `pan`, `d`, `8`, then press the button **as soon as you press Enter on the length** (during the 1-2 s save) | `== LOCKED ==`. The save masks interrupts, so the button can take up to ~2 s to be served — that delay is expected. No `Password:` line is printed. Either id 17 was saved or it was not, but the table must be **whole and readable**, never garbage | [ ] |
| 9.6 | Press RESET, log in, retrieve | Table is readable; id 17 is either absent or a complete entry with a valid password | [ ] |
| 9.7 | Press the button once, holding it | Only one wipe, no repeated screens (debounce) | [ ] |
| 9.8 | Press the button during the `Deriving key` / `Checking` pause of a login | Returns to `== LOCKED ==`; the wrong-or-right answer that was being computed is discarded | [ ] |

## Set 10: Bad or missing sensor (needs a jumper wire)

| ID | Steps | Expected | OK |
|---|---|---|---|
| 10.1 | Jumper **A0 (PA0) to GND**, then generate (any settings) | `ENTROPY SOURCE FAULT - nothing was saved.` then menu | [ ] |
| 10.2 | Retrieve | The attempt did not create an entry | [ ] |
| 10.3 | Remove the jumper and generate again | Works normally | [ ] |
| 10.4 | Jumper **A1 (PA1) to 3V3**, generate | Same fault message | [ ] |
| 10.5 | Remove sensors completely (pins floating), generate | Record what happens (it may work, since floating pins are noisy, or fault). Either is acceptable, note which | [ ] |

A single unexplained fault with healthy sensors happens occasionally: the
health tests have a small false-alarm rate, and the PA0 sensor was measured
running close to the limit (its most common ADC code took 40-60% of a block,
against a 64% cutoff). Repeat once before suspecting the wiring. If test 3.1
faults again, note how often — that is the open item in the error list.

## Set 11: Repeated use and limits

| ID | Steps | Expected | OK |
|---|---|---|---|
| 11.1 | Generate 10 passwords in a row without resetting | Every one saves, none hangs, menu returns each time | [ ] |
| 11.2 | Time one save (Enter on the length to `Saved as`) | Under about 3 seconds | [ ] |
| 11.3 | After the 10, retrieve each new id | Every password matches what was printed | [ ] |
| 11.4 | Optional: fill the whole table (ids 0-30 all used) and open the listing | All 31 lines appear, and any entry can be retrieved | [ ] |
| 11.5 | Send a line while a save is running (type `zzz` + Enter right after the length) | The mode finishes normally; at most that line is ignored or answered as an unknown menu option | [ ] |

## Set 12: Change of master key

Do this **last**: it replaces the key every earlier test used.

| ID | Steps | Expected | OK |
|---|---|---|---|
| 12.1 | Send `3` | `== CHANGE MASTER KEY ==` and `New master key ...` | [ ] |
| 12.2 | Send an empty line | `Cancelled.` and the menu; the old key still works | [ ] |
| 12.3 | Send `3`, then `short` | `Invalid master key: use 8-31 printable characters.` | [ ] |
| 12.4 | Send a valid new key, then a different one to confirm | `The two entries do not match. Start again.` | [ ] |
| 12.5 | Send a new key twice | `Re-encrypting, please wait...` then `Master key changed. The old key no longer works.` | [ ] |
| 12.6 | Press RESET, log in with the **old** key | `Wrong master key` | [ ] |
| 12.7 | Log in with the **new** key, then retrieve | Every entry is still readable and unchanged | [ ] |
| 12.8 | With A0 jumpered to GND (a dead entropy sensor), do a change of key | It still completes — the salt falls back to the device id instead of blocking | [ ] |

## Known limits (not failures)
- Passwords are at most **31 characters** and names at most **15**, set by the
  size of one table entry.
- Once a generate length is accepted, it cannot be cancelled until the save
  finishes (logged open item).
- Only one line can be in flight at a time; do not paste several lines together,
  and do not type during `Deriving`, `Checking`, `Re-encrypting` or a save —
  those stall the receiver and the line is lost.
- Passwords are printed in clear text on the serial link so you can read them
  once.
- Login takes seconds on purpose: the key derivation is deliberately slow.
  There is no limit on wrong guesses beyond that delay.
- Nothing here checks the encryption inside flash or the key derivation; that
  needs the debugger steps in `TESTING.md` Parts 2 and 3.

## Report back
For each failure: the test ID, exactly what you sent, and what the terminal
showed (copy and paste it). If several tests fail together, list the first one
that failed.
