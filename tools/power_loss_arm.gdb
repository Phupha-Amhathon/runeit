# Power-loss test, step 1 of 2: stop a real save right after word $word of
# the encrypted table has been written to the new partition, then you pull
# the USB cable while the CPU is halted. Step 2 is power_loss_check.gdb.
#
# Needs: the board logged in, at least one entry already saved, and the GDB
# server running (python3 tools/start_gdbserver.py). From the repo root:
#
#   $GDB -q Debug/runeit.elf
#   (gdb) set $word = 200                  <- optional, 0-371, default 200
#   (gdb) source tools/power_loss_arm.gdb
#
# then on the serial terminal do a Generate to a NEW id (e.g. 1, "bank").

target remote localhost:61234

if $_isvoid($word)
  set $word = 200
end
set $hdr_len   = sizeof(partition_header_t)
set $tbl_words = sizeof(pwd_table_t) / 4
if $word >= $tbl_words
  printf "error: $word must be 0-%d\n", $tbl_words - 1
  detach
  quit
end

# Partition_Store_Commit() always writes the sector that is NOT active.
if 'partition_store.c'::s_have_active && 'partition_store.c'::s_active.sector == 2
  set $target_sector = 3
  set $target_base   = 0x0800C000
else
  set $target_sector = 2
  set $target_base   = 0x08008000
end
set $tbl = $target_base + $hdr_len

printf "\nactive partition : sector %d, version %d\n", 'partition_store.c'::s_active.sector, 'partition_store.c'::s_active.header.version
printf "next save goes to: sector %d, table at 0x%08x (%d words)\n", $target_sector, $tbl, $tbl_words
printf "will stop after  : table word %d is written (byte offset %d)\n\n", $word, $word * 4

# Line 49 runs after the BSY wait, so word i is already in flash. The address
# check skips the header write, which is the first Flash_Drv_Write call.
break flash_drv.c:72 if address == $tbl && i == $word * 4

echo >>> Now do a Generate to a NEW id on the serial terminal. Waiting...\n
continue

printf "\nstopped: table words 0-%d are in flash, %d-%d are not\n", $word, $word + 1, $tbl_words - 1
printf "header  @0x%08x : ", $target_base
x/2xw $target_base
printf "word %3d @0x%08x : ", $word, $tbl + $word * 4
x/1xw $tbl + $word * 4
if $word + 1 < $tbl_words
  printf "word %3d @0x%08x : ", $word + 1, $tbl + ($word + 1) * 4
  x/1xw $tbl + ($word + 1) * 4
end
echo \n>>> UNPLUG THE USB CABLE NOW, wait 2 s, plug it back in.\n
echo >>> Then: quit this gdb (y), restart start_gdbserver.py, run power_loss_check.gdb.\n
