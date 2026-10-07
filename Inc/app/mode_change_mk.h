#ifndef MODE_CHANGE_MK_H
#define MODE_CHANGE_MK_H

#include "app_types.h"

/**
 * CHANGE_MK_MODE of RUNEIT_V1 (CHANGE_MK_CHECK, RE_EN_PWD_TABLE, RE_HASH_MK,
 * TOGGLE_PARTITION). Unlike the flow drawn in the PDF, the re-encrypted
 * table and the new key material are written by ONE Partition_Store_Commit()
 * to the inactive sector with a single CRC, so a power loss can never leave a
 * table encrypted with one key next to a hash of another; the partition
 * toggle is simply the effect of that commit.
 *
 * Needs an open session (the table is decrypted with the current key).
 * An empty line at the prompt cancels.
 */
void Mode_ChangeMk_Enter(void);
mode_status_t Mode_ChangeMk_Run(void);

/** Zeroes the new master key and the working copy of the table (panic button). */
void Mode_ChangeMk_Wipe(void);

#endif /* MODE_CHANGE_MK_H */
