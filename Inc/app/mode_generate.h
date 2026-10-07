#ifndef MODE_GENERATE_H
#define MODE_GENERATE_H

#include <stdint.h>
#include <stdbool.h>
#include "app_types.h"

/**
 * GENERATE_MODE: the user picks a table id and service name, a character set
 * and a length; the password is built from debiased ADC noise (see
 * entropy_pool.h) and stored with Session_Save(), which encrypts the table
 * with the session key and commits it to the inactive flash partition.
 *
 *   PROMPT_ID -> [OVERWRITE?] -> NAME -> CLASSES -> LENGTH
 *   -> SAMPLING (DMA blocks, non-blocking) -> SAVE
 *
 * An empty line cancels at any prompt. Nothing is written if there is no open
 * session, the entropy source fails a health test, or the panic button fires
 * before the save starts.
 */

/** Decrypts the active table into RAM with the session key and resets the
 *  sub-state machine. Needs an open session. */
void Mode_Generate_Enter(void);

/** Call once per main-loop iteration while in GENERATE_MODE. */
mode_status_t Mode_Generate_Run(void);

/** Wipes the table, the generated password and the entropy pool, and aborts a
 *  save that has not started yet (used by the panic-button handler). */
void Mode_Generate_Wipe(void);

#endif /* MODE_GENERATE_H */
