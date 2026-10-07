# How to use `mk_auth_trace.py`

**Status of this guide.** Connecting to the board with `tools/start_gdbserver.py`
has been confirmed working on real hardware (a NUCLEO-F411RE, ST-LINK/V2.1).
Running `mk_auth_trace.py` itself, its breakpoints, and the values it
prints, have not. I read the script and the code it stops inside and expect
the results below, but everything from "Steps" onward is still
**PREDICTED**.

A visitor who only watches the serial terminal sees two possible messages
when they log in, `Access granted` or `Wrong master key`. They cannot see
what the board actually does with the key they typed. This tool makes that
visible. It is a script that drives gdb for you. You do not type any gdb
commands yourself. The script stops the board four times during one login
attempt, and each time it stops, it prints what the board is holding in RAM
and in flash at that exact moment.

This guide is only about this one tool. For the rest of the hardware test
suite, see [`TESTING.md`](../TESTING.md).

## What this tool shows you

Logging in has three moments worth seeing, and this tool shows all three.

* **What you typed.** The exact bytes you sent from the serial terminal,
  before the board does anything with them.
* **What the board computed.** Your typed key is stretched into a longer
  secret number through many rounds of hashing (this is called a KDF, short
  for key derivation function). That longer number is then hashed two more
  times into two different numbers. One of those two numbers is compared
  against what is already saved in flash. The other becomes the key used to
  decrypt your password table, and is called the session key.
* **Whether it matched.** The tool reads the number it just computed and the
  number already stored in flash side by side, and prints whether they are
  the same.

## Before you start

* The board must already have a saved master key. Reset it. The terminal
  must show `== LOCKED ==`. If it shows `== FIRST TIME SETUP ==` instead,
  set a master key first through the normal menu, then come back to this
  guide.
* Rebuild and reflash the firmware from STM32CubeIDE, even if you have not
  changed any source file. The program file already sitting in the `Debug`
  folder can be older than the source code, and if it is, the line numbers
  this tool relies on point at the wrong function. The tool checks this
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
  you can run `mk_auth_trace.py` again without restarting the server.
* If you have not used this tool before, install the one Python package it
  needs.
  ```
  pip install pexpect
  ```

## Steps

1. Leave the serial terminal open, at 115200 baud, the same way you always
   test the board.
2. In a third terminal window, from the project folder, run the tool with no
   arguments.
   ```
   python3 tools/mk_auth_trace.py
   ```
   With no arguments it looks for the program file at `Debug/runeit.elf` and
   connects to the GDB server at `localhost:61234`, which match the defaults
   used above. You should see it report the gdb program it found, load the
   program file, connect, and confirm all four stopping points before
   printing a line telling you it is ready and waiting.
3. On the serial terminal, type a master key you know is **wrong**, then
   press Enter.
4. The instant you press Enter, everything freezes, the serial terminal
   included. This is expected, not a hang. The board really is stopped, at
   the exact spot in the code that has just received your key.
5. In the tool's window, a block labelled `[1] TYPED` appears. It shows the
   key you typed and how many bytes it was, followed by a fresh read of
   several other things worth watching. It shows which state the board's
   menu is in, which flash partition is active, the raw bytes of both flash
   partitions, and whether a session is currently open. Press Enter in the
   tool's window to let the board continue.
6. A block labelled `[2] COMPUTED` appears. It shows the long secret number
   the board stretched your key into, and the two numbers made from that.
   Press Enter again.
7. A block labelled `[3] COMPARED` appears. Because the key was wrong, it
   should say `MISMATCH`, and list `SESSION_WRONG_KEY` as the result. Press
   Enter again.
8. No fourth block appears, because the login was rejected. The serial
   terminal unfreezes and prints `Wrong master key`, followed by a time in
   milliseconds. That time includes however long you spent reading the
   tool's output between steps, so a large number here does not mean
   anything is slow.
9. Type your **correct** master key at the serial terminal and press Enter.
   Blocks `[1]` through `[3]` appear again the same way, except block `[3]`
   should now say `MATCH` and list `SESSION_OK`. A fourth block, `[4]
   TRANSITION`, appears after it, showing the board's menu state changing
   from the login screen to the mode-selection screen. The serial terminal
   unfreezes, prints `Access granted`, and shows the normal menu.
10. When you are done, press Ctrl+C in the tool's window. It removes its
    stopping points, disconnects, and leaves the board running normally,
    ready for a normal demo or for another run of this tool.

## Result

Two login attempts, one wrong and one correct, each produce a small,
readable story in the tool's window. You see the key you typed, the numbers
the board computed from it, the number already stored in flash, and whether
they agreed. The wrong attempt stops after three blocks and reports
`MISMATCH`. The correct attempt goes through all four blocks and reports
`MATCH`. Ctrl+C always leaves the board usable afterwards.

## If the result is different

* **"could not connect to the GDB server"** → The GDB server is not running
  on that port, or STM32CubeIDE's own debug session is still open and
  holding it. Close the CubeIDE session, then start `ST-LINK_gdbserver`
  again as shown above.
* **"Debug/runeit.elf is stale relative to the current source"** → Rebuild
  and reflash from STM32CubeIDE, then run the tool again. This message means
  exactly what it says. The program file on disk is older than the source
  code, so its addresses point at the wrong place.
* **Nothing happens after you run the tool, no ready message** → Check the
  tool's own terminal for an error. A missing `pexpect` or a missing
  `arm-none-eabi-gdb` both stop the tool before it connects, and both print
  a message saying what to install or which path to set.
* **The tool says it is ready, but nothing prints when you type on the
  serial terminal** → The tool is waiting for the very first stopping point,
  which only fires once you press Enter after typing a key on the serial
  terminal. Typing without pressing Enter does not trigger it, the same as
  the normal login screen.
* **The tool seems to hang after a block is printed** → It is waiting for
  you to press Enter in the tool's own window, not the serial terminal. This
  is the same pause described in "Before you start", deliberate and not a
  freeze in the firmware.
