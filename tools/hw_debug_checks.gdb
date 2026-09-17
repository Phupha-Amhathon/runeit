# RUNEIT hardware debug checks (arm-none-eabi-gdb command file)
#
# Verifies three things the serial protocol alone can't prove:
#   SECTION 1  - the password table is actually encrypted at rest in flash
#   SECTION 2  - the A/B partition commit/reload mechanism works, exercised
#                for real by calling Partition_Store_Commit() on the halted
#                target (no TOGGLE_PARTITION UI exists yet -- this doesn't
#                need one)
#   SECTION 2b - a corrupted CRC is detected and the firmware falls back to
#                the other, still-valid partition instead of trusting it
#   SECTION 3  - the panic button actually zeroes the decrypted table in RAM
#
# HOW TO RUN: see TESTING.md for how to get a gdb prompt attached to the
# board. This file is NOT meant to be sourced start-to-finish blind --
# Sections 1's positive control and the post-reset check in Section 2
# depend on something you do *on the serial terminal*, which gdb can't see
# or wait for. Read the echoed instructions as you go; where a section
# says "do this on the serial terminal first", pause and do that before
# letting/re-running the rest of that section.
#
# Static (file-scope) C symbols below are qualified as 'file.c'::name
# because GDB cannot resolve internal-linkage globals by bare name outside
# their own compilation unit.

echo \n============================================================\n
echo SECTION 1a: flash-at-rest encryption -- negative control\n
echo ============================================================\n
echo Searching the partition flash region (0x08008000-0x08010000) for the\n
echo known seed plaintext. Every 'find' below should print "Pattern not found.".\n
echo If any of them DO find a match, the table is being written to flash\n
echo un-encrypted -- look at partition_store.c's Commit()/seed path.\n\n

find 0x08008000, 0x08010000, "example.com"
find 0x08008000, 0x08010000, "hunter2"
find 0x08008000, 0x08010000, "email"
find 0x08008000, 0x08010000, "correct-horse-battery"

echo \n============================================================\n
echo SECTION 1b: flash-at-rest encryption -- positive control\n
echo ============================================================\n
echo This proves the search above is meaningful (i.e. it isn't just\n
echo failing to find anything anywhere). On the SERIAL terminal, send "1"\n
echo at the menu to enter RETRIEVE_MODE, THEN run this one line again:\n
echo \n    find &'mode_retrieve.c'::s_table, +sizeof('mode_retrieve.c'::s_table), "example.com"\n\n
echo It SHOULD find a match this time -- that's the decrypted RAM copy.\n

find &'mode_retrieve.c'::s_table, +sizeof('mode_retrieve.c'::s_table), "example.com"

echo \n============================================================\n
echo SECTION 2: partition A/B commit + reload after a real reset\n
echo ============================================================\n
echo Requires the board to already be in RETRIEVE_MODE (Section 1b, above)\n
echo so 'mode_retrieve.c'::s_table is populated with something to commit.\n\n

print 'partition_store.c'::s_active
echo ^ note the current .sector and .header.version -- compare after the commit below\n\n

set variable 'mode_retrieve.c'::s_table.entries[0].name = "TOGGLED-A"
call SHA256_Compute('app.c'::s_hardcoded_mk, sizeof('app.c'::s_hardcoded_mk)-1, (unsigned char*)&'partition_store.c'::s_active.header.hash_mk)
call (int)Partition_Store_Commit('app.c'::s_hardcoded_mk, sizeof('app.c'::s_hardcoded_mk)-1, 'partition_store.c'::s_active.header.hash_mk, &'mode_retrieve.c'::s_table)

print 'partition_store.c'::s_active
echo ^ .sector should have flipped (2 <-> 3) and .header.version should be +1 from before\n\n

echo Resetting so Partition_Store_Init() re-selects from flash on a real\n
echo boot, not just from the live call above.\n
monitor reset
continue

echo \nOnce the board has rebooted, on the SERIAL terminal send "1" then "0"\n
echo at the menu/id prompts. id 0's name should now read "TOGGLED-A" -- that\n
echo is this section's pass condition (proves both the write-to-the-other-\n
echo sector logic AND that Partition_Store_Init() re-selects it after reset).\n

echo \n============================================================\n
echo SECTION 2b: CRC fault injection (run Section 2 first)\n
echo ============================================================\n
echo Corrupts the CURRENTLY ACTIVE partition's stored CRC (writing all-zero\n
echo bits only, which is always legal on NOR flash without an erase cycle)\n
echo and resets, expecting the firmware to notice the mismatch and fall\n
echo back to the OTHER partition (still valid, one version older) rather\n
echo than trust corrupted data or crash.\n\n
echo This is the one check in this file that depends on your specific\n
echo probe/GDB-server passing plain memory writes through to flash. Check\n
echo with the "print/x" before/after below -- if the value doesn't actually\n
echo change, your setup doesn't support this and this section is optional\n
echo to skip (Sections 1-3 don't depend on it).\n\n

print/x 'partition_store.c'::s_active.header.crc32
set variable *(unsigned int*)('partition_store.c'::s_active.addr + 40) = 0
print/x 'partition_store.c'::s_active.header.crc32

monitor reset
continue

echo \nOn the SERIAL terminal, confirm the board still boots straight to the\n
echo menu (not a crash/hang) and that "1" now shows the entry from BEFORE\n
echo Section 2's commit (i.e. it fell back to the older-but-valid partition,\n
echo not the one we just corrupted).\n

echo \n============================================================\n
echo SECTION 3: panic-button RAM wipe\n
echo ============================================================\n
echo Requires the board to be in RETRIEVE_MODE on the SERIAL terminal\n
echo (send "1" at the menu) so 'mode_retrieve.c'::s_table is populated.\n\n

print 'mode_retrieve.c'::s_table.entries[0]
echo ^ should show a plaintext entry right now\n\n

break Mode_Retrieve_Wipe
echo \n>>> Press the PA10 button on the board now. This will block until you do. <<<\n
continue
finish

print 'mode_retrieve.c'::s_table
echo ^ every field should now read as zero\n\n
find &'mode_retrieve.c'::s_table, +sizeof('mode_retrieve.c'::s_table), "example.com"
echo ^ should now say "Pattern not found." -- compare to Section 1b, which found it before the wipe\n
