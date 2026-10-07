#ifndef MODE_FIRST_MEET_H
#define MODE_FIRST_MEET_H

#include "app_types.h"

/**
 * FIRST_MEET_PROMPT -> FIRST_MEET_CHECK -> (confirm) -> FIRST_MEET_HASH of
 * RUNEIT_V1, for a device with no valid partition: the user chooses the
 * master key (asked twice, so a typo cannot lock them out), the key is
 * stretched and the first partition is written. On success the session is
 * already open, so the machine continues straight to MODE_SELECTION.
 */
void Mode_FirstMeet_Enter(void);
mode_status_t Mode_FirstMeet_Run(void);

/** Zeroes the master key being typed (panic button). */
void Mode_FirstMeet_Wipe(void);

#endif /* MODE_FIRST_MEET_H */
