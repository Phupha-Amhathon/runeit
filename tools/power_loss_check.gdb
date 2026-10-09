# Power-loss test, step 2 of 2: after the board has rebooted from the power
# cut, show what is in each partition and which one the board chose.
#
#   $GDB -q Debug/runeit.elf
#   (gdb) source tools/power_loss_check.gdb
#
# ValidateCrc() is the firmware's own boot check (magic is tested separately
# in ReadSlot). Calling it uses the CRC unit, which the halted firmware is not
# using at the LOCKED screen.

target remote localhost:61234

set $a = (const partition_header_t *)0x08008000
set $b = (const partition_header_t *)0x0800C000
set $crc_a = (int)'partition_store.c'::ValidateCrc(0x08008000, $a)
set $crc_b = (int)'partition_store.c'::ValidateCrc(0x0800C000, $b)

printf "\n            magic       version  CRC\n"
printf "sector 2 A  0x%08x  %7u  ", $a->magic, $a->version
if $crc_a
  echo ok\n
else
  echo BAD\n
end
printf "sector 3 B  0x%08x  %7u  ", $b->magic, $b->version
if $crc_b
  echo ok\n
else
  echo BAD\n
end

if 'partition_store.c'::s_have_active
  printf "\nboard chose: sector %d, version %u\n", 'partition_store.c'::s_active.sector, 'partition_store.c'::s_active.header.version
else
  echo \nboard chose: nothing (no valid partition, FIRST_MEET)\n
end
echo PASS = the half-written sector shows CRC BAD and the board chose the other one.\n
echo Then log in on serial: the old entries are there, the interrupted one is not.\n

detach
