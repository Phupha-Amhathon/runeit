# How to use `other_modes_trace.py`

**Status of this guide.** Connecting to the board with `tools/start_gdbserver.py`
has been confirmed working on real hardware (a NUCLEO-F411RE, ST-LINK/V2.1).
Running this file itself, its breakpoints, and the values it prints, have
not. I read the script and the code it stops inside and expect the results
below, but everything past "Before you start" is still **PREDICTED**.

This one file covers four modes. They are first-time master-key setup
(`FIRST_MEET`), changing the master key (`CHANGE_MK_MODE`), decrypting and
showing a password (`RETRIEVE_MODE`), and generating a password from sensor
noise (`GENERATE_MODE`). `tools/mk_auth_trace.py` is a separate file and
covers login (`MK_AUTH`) on its own - try that one first, since it needs
nothing but a normal boot.

This guide is only about this file. For the rest of the hardware test
suite, see [`TESTING.md`](../TESTING.md).

## Why you have to pick one mode with `--mode`

Between them, the four modes in this file use 15 gdb breakpoints. A
Cortex-M4 chip (the one on this board) can normally only hold about 6
breakpoints at once in its own hardware, since flash-resident code needs a
real hardware slot per breakpoint, there is no cheap software alternative
for code sitting in flash. Trying to arm all 15 at once would fail outright
the moment this tool tried to use them. Each mode's own set is small enough
by itself, so this tool only ever arms one mode's breakpoints per run,
chosen with `--mode`.

```
python3 tools/other_modes_trace.py --mode first_meet
python3 tools/other_modes_trace.py --mode change_mk
python3 tools/other_modes_trace.py --mode retrieve
python3 tools/other_modes_trace.py --mode generate
```

## Before you start (every mode)

* Rebuild and reflash the firmware from STM32CubeIDE, even if you have not
  changed any source file. The program file already sitting in the `Debug`
  folder can be older than the source code, and if it is, the line numbers
  this tool relies on point at the wrong place. The tool checks this
  itself on startup and refuses to run if it finds a mismatch, but
  rebuilding first means you never see that message at all.
* Close STM32CubeIDE's own debug session if one is open. Only one program
  can talk to the ST-LINK debugger at a time, and this tool needs that slot
  for itself.
* Open a new terminal window, separate from the serial terminal, and start
  the ST-LINK GDB server by itself in it.
  ```
  python3 tools/start_gdbserver.py
  ```
  This finds and runs the real `ST-LINK_gdbserver` binary with the flags it
  actually needs. Getting those flags right took real trial and error on a
  board: the plain `ST-LINK_gdbserver -p 61234 -e` command you might expect
  to work is missing two things it silently needs, `-cp <path to
  STM32CubeProgrammer>` and `-d` (SWD mode, without it the server defaults
  to JTAG and fails to identify the chip at every frequency it tries, which
  looks like a connection problem but isn't one). `tools/start_gdbserver.py`
  finds both paths itself and gets this right automatically. You should see
  it print `Waiting for debugger connection...` once it's ready. Leave this
  terminal open: `-e` keeps the server running after a tool disconnects, so
  you can run it again without restarting the server.
* If you have not used any of these tools before, install the one Python
  package they need.
  ```
  pip install pexpect
  ```
* A hit breakpoint halts the whole core, so typing on the serial terminal
  will visibly pause the instant Enter is pressed there. This is expected
  for every mode below, not a hang.

Each mode section below has its own extra precondition on top of these
shared ones.

---

## FIRST_MEET (`--mode first_meet`)

The first time RUNEIT is used on a blank device, it asks for a master key
twice and builds the very first encrypted partition from it. A visitor
watching only the serial terminal sees two prompts and a "Master key set"
message. They cannot see the two typed entries being compared, the key
being derived, or the new partition being written.

**What this shows you.** Whether your two typed entries matched, byte for
byte. What the board computed. The key is stretched into a longer secret
number (a KDF, key derivation function), then hashed twice more into two
numbers, one stored and one used to encrypt your table. What got written to
flash. Unlike a login, there is nothing to compare against here, since this
is the very first partition this device has ever had.

**Extra precondition.** This mode needs a **blank** device, the opposite of
every other mode in this file. Reset the board. The serial terminal must
show `== FIRST TIME SETUP ==`. If it shows `== LOCKED ==` instead, a master
key is already set. To erase it and start fresh, attach gdb to the board,
halt it, and run
```
call (void)Flash_Drv_EraseSector(2)
call (void)Flash_Drv_EraseSector(3)
```
then reset the board. **This destroys any existing password table.** Only
do this if you mean to.

### Steps

1. Leave the serial terminal open, at 115200 baud. It should show
   `== FIRST TIME SETUP ==`.
2. In a third terminal window, from the project folder, run
   ```
   python3 tools/other_modes_trace.py --mode first_meet
   ```
   You should see it report the gdb program it found, load the program
   file, connect, and confirm all four stopping points before printing a
   line telling you it is ready and waiting.
3. On the serial terminal, type a master key (8 to 31 printable
   characters) and press Enter.
4. Type the **same** key again to confirm, and press Enter. The instant you
   do, everything freezes. The board really is stopped, at the exact spot
   in the code that has just compared your two entries.
5. A block labelled `[1] CONFIRMED` appears. It shows both entries you
   typed and whether they matched, followed by a fresh read of the board's
   other state. "Active partition" is expected to show nothing valid yet,
   since this device has never had a key before. Press Enter to continue.
6. A block labelled `[2] COMPUTED` appears, showing the long secret number
   your key was stretched into, the two numbers made from that, and a
   freshly-generated salt (a random-ish value mixed into the key derivation
   so the same key produces different results on different devices). Press
   Enter again.
7. A block labelled `[3] COMMITTED` appears, showing the salt, the stored
   value, and the new session key, all just written to flash. There is no
   match or mismatch shown here. Press Enter again.
8. A block labelled `[4] TRANSITION` appears, showing the menu state
   changing to mode-selection. The serial terminal unfreezes and prints
   `Master key set`.
9. Press Ctrl+C in the tool's window when done. It removes its stopping
   points, disconnects, and leaves the board running normally.

### Result

Four blocks appear. You see the two typed entries and whether they
matched, the key material computed from them, the values written to the
brand-new partition, and the menu switching over. Ctrl+C always leaves the board
usable afterwards.

### If the result is different

* **The serial terminal still shows `== LOCKED ==`** → A master key is
  already set. Erase sectors 2 and 3 as above, then reset. Whether the
  board actually shows `== FIRST TIME SETUP ==` right after that erase is
  reasoned through but not board-confirmed, so treat it as expected, not
  guaranteed, the first time you try it.
* See "Problems common to every mode" at the end of this guide for
  connection and startup errors.

---

## CHANGE_MK_MODE (`--mode change_mk`)

Changing the master key means decrypting the whole password table with the
old key, deriving a new key from what you type, re-encrypting that same
table under the new key, and writing it to the other flash partition. A
visitor watching only the serial terminal sees "Re-encrypting, please
wait..." and then "Master key changed," with nothing in between.

**What this shows you.** Whether your two new-key entries matched. The
table under the old key, one entry, right before the new key takes over.
The new key material, the same shape `mk_auth_trace.py` shows for a login.
The commit, with the retired key and the new one side by side.

**Extra precondition.** Needs an **already open session**. Log in with your
current master key over the serial terminal first, then choose "Change
master key" from the menu. It cannot be demoed cold from a reset.

### Steps

1. On the serial terminal, log in, then choose "Change master key". Leave
   it sitting at the "New master key" prompt.
2. In a second terminal window, run
   ```
   python3 tools/other_modes_trace.py --mode change_mk
   ```
   You should see it confirm all five stopping points before printing a
   line telling you it is ready and waiting.
3. Type a new master key and press Enter, then type it again to confirm
   and press Enter. Everything freezes the instant you confirm.
4. A block labelled `[1] CONFIRMED` appears, showing both new-key entries
   and whether they matched. Press Enter to continue.
5. A block labelled `[2] BEFORE` appears, showing the session key about to
   be retired and one table entry decrypted under it. Press Enter again.
6. A block labelled `[3] COMPUTED` appears, showing the new key material
   being derived. Press Enter again.
7. A block labelled `[4] COMMITTED` appears, showing the old session key
   next to the new one and the values just written to the other partition.
   Press Enter again.
8. A block labelled `[5] TRANSITION` appears. This is reached whether the
   change succeeded or was cancelled, so check block `[4]` to see which
   actually happened. The serial terminal unfreezes and prints `Master key
   changed`.
9. Press Ctrl+C in the tool's window when done.

### Result

Five blocks appear. You see the two typed new-key entries and whether they
matched, the table under the old key, the new key material, the old and
new keys side by side at the commit, and the menu switching back. Ctrl+C always leaves
the board usable afterwards.

### If the result is different

* **Only block `[1]` appears, nothing after it** → There is no open
  session. Log in over the serial terminal first, then choose "Change
  master key" before running this tool.
* See "Problems common to every mode" at the end of this guide.

---

## RETRIEVE_MODE (`--mode retrieve`)

This is the direct answer to "we store a password, then retrieve it, how do
we know it's correct?" Flash always holds the encrypted password table at
rest. Retrieving an entry decrypts that table into RAM with the session key
and prints one entry over serial. A visitor watching only the serial
terminal sees a readable `service : password` line and has to take it on
trust that the flash behind it is actually encrypted.

**What this shows you.** Ciphertext at rest: one entry's worth of raw
bytes, read straight out of flash, before decryption, which should look
like random noise. The same bytes, decrypted, right after. The exact entry
you asked for, read straight out of RAM, to compare against what the
terminal just printed.

**Extra precondition.** Needs an **already open session**. Log in over the
serial terminal first, then choose "Retrieve password" from the menu.

### Steps

1. On the serial terminal, log in. Leave it at the mode-selection menu.
2. In a second terminal window, run
   ```
   python3 tools/other_modes_trace.py --mode retrieve
   ```
   You should see it confirm all three stopping points before printing a
   line telling you it is ready and waiting.
3. On the serial terminal, choose "Retrieve password". Everything freezes
   the instant you do.
4. A block labelled `[1] BEFORE` appears, showing one entry's worth of
   bytes read straight from flash, before decryption. They should look
   like noise. Press Enter to continue.
5. A block labelled `[2] AFTER` appears, showing the same byte range, now
   decrypted and readable in RAM. Compare it against block `[1]` - same
   position, completely different-looking bytes. Press Enter again. The
   serial terminal unfreezes and shows the list of service names.
6. Type an id (0 to 30) on the serial terminal and press Enter. Everything
   freezes again.
7. A block labelled `[3] SHOWN` appears, reading the exact RAM content
   behind the id you picked. It should match what the serial terminal is
   about to print. Press Enter again, and the serial terminal shows the
   `service : password` line.
8. Press Ctrl+C in the tool's window when done.

### Result

Three blocks appear. You see the raw ciphertext for one entry straight
from flash, the same bytes decrypted in RAM, and the specific entry you
picked, read straight out of RAM. The ciphertext and the decrypted bytes should look
nothing alike. The entry shown in block `[3]` should match exactly what the
serial terminal prints. Ctrl+C always leaves the board usable afterwards.

### If the result is different

* **Only block `[1]` appears, nothing after it** → There is no open
  session, so decryption failed and the mode exited immediately. Log in
  over the serial terminal first.
* **Block `[1]` and `[2]` show identical-looking bytes** → Something is
  wrong with the encryption, or the wrong offset is being read. This would
  be a real finding worth reporting, not expected behaviour.
* See "Problems common to every mode" at the end of this guide.

---

## GENERATE_MODE (`--mode generate`)

GENERATE_MODE turns sensor noise into a password. A thermistor and a light
sensor are sampled through the ADC, and the raw bits are biased (skewed as
far as 83/17 in testing, per `README.md`), so they are run through Von
Neumann debiasing before being turned into characters. A visitor watching
only the serial terminal sees the final password appear after a short
pause, with none of the noise or bias visible.

**This mode paces differently from the other three.** A 31-character
password needs roughly 9 rounds of sampling, two ADC blocks each. Pausing
on Enter at every single one would make a demo unusable. Only the **first
round** pauses with full detail. Every round after that prints one short
line and keeps going on its own, whether or not you pass `--auto`. This is
a deliberate choice, not a bug.

**What this shows you.** Raw ADC noise, once, in detail: on the first round
only, the 512 raw samples from each channel, the fraction of them with a
biased bit, and how many adjacent samples differ enough to produce a
debiased bit. A running summary for every later round. Each line shows the
bias, the yield, and how far the password is from its target length. The
save. The generated password and the id it's saved to, right before the
commit, and the same entry's encrypted bytes on flash right after.

**Extra precondition.** Needs an already open session, and you must already
be most of the way through GENERATE_MODE's prompts before starting this
tool, its breakpoints only exist once sampling begins. Log in, choose
"Generate password", and answer the id, (optional overwrite), name,
character-class and length prompts. Leave the board at the point where
sampling is about to start, or already running.

### Steps

1. On the serial terminal, log in, choose "Generate password", and answer
   the prompts. Stop right before or right after sampling starts.
2. In a second terminal window, run
   ```
   python3 tools/other_modes_trace.py --mode generate
   ```
   You should see it confirm all three stopping points before printing a
   line telling you it is ready and waiting.
3. If sampling has not started yet, let it start now (right after the
   length prompt). The instant the first ADC block is ready, everything
   freezes.
4. A block labelled `[round 1, temp] RAW` appears, showing the raw samples
   from the temperature sensor, the measured bias, and the estimated
   debiasing yield. Press Enter to continue.
5. A matching block for the light sensor appears. Press Enter again.
6. From round 2 onward, you'll see one short line per round instead of a
   full block, scrolling by on its own without waiting for Enter, until
   enough characters have been generated.
7. A block labelled `[3] SAVED` appears once the password is complete,
   showing the generated password and the id it's saved to, then the same
   entry's bytes read back from flash, now encrypted. Press Enter to let
   the board finish the commit and return to the menu.
8. Press Ctrl+C in the tool's window when done.
9. Optionally, run `other_modes_trace.py --mode retrieve` afterward and
   retrieve the same id - its decrypted bytes should match the plaintext
   block `[3]` showed here, which is the actual proof the whole round trip
   is correct.

### Result

The first sampling round produces two detailed blocks (temperature, then
light), later rounds scroll by as one-line summaries, and a final `[3]
SAVED` block shows the generated password going in as plaintext and coming
back out as ciphertext on flash. Ctrl+C always leaves the board usable
afterwards.

### If the result is different

* **`ENTROPY SOURCE FAULT` appears on the serial terminal right after you
  press Enter in the tool** → This is the predicted failure if this mode's
  breakpoints are paused at the wrong spot relative to the ADC's own
  200 ms wait timer, which keeps running even while the core is halted.
  Both breakpoints are placed after the board has already confirmed a
  block is ready, which should make this safe, but it has not been
  confirmed on real hardware yet.
* **Nothing happens for a long time, no breakpoint hit** → The board has
  not reached the sampling state yet, or is between rounds waiting for the
  next ADC block. Make sure you've actually answered the length prompt.
* See "Problems common to every mode" at the end of this guide.

---

## Problems common to every mode

* **"could not connect to the GDB server"** → The GDB server is not
  running on that port, or STM32CubeIDE's own debug session is still open
  and holding it. Close the CubeIDE session, then start `ST-LINK_gdbserver`
  again as shown in "Before you start."
* **A message saying a breakpoint resolved somewhere unexpected** → Either
  `Debug/runeit.elf` is older than the current source code, or the
  compiler organized this particular spot differently than expected.
  Rebuild and reflash from STM32CubeIDE, then run the tool again. If it
  still happens after a fresh rebuild, the tool's breakpoint table needs
  updating to match the current source, not something you can fix by
  rebuilding again.
* **Nothing happens after you run the tool, no ready message** → Check the
  tool's own terminal for an error. A missing `pexpect` or a missing
  `arm-none-eabi-gdb` both stop the tool before it connects, and both
  print a message saying what to install or which path to set.
* **"the following arguments are required: --mode"** → Every run of this
  file needs `--mode first_meet`, `--mode change_mk`, `--mode retrieve`, or
  `--mode generate` - see "Why you have to pick one mode with --mode" above.
* **The tool seems to hang after a block is printed** → It is waiting for
  you to press Enter in the tool's own window, not the serial terminal.
  This is a deliberate pause, not a freeze in the firmware.
