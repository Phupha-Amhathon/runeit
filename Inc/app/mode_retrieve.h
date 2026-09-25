#ifndef MODE_RETRIEVE_H
#define MODE_RETRIEVE_H

#include <stdint.h>
#include <stdbool.h>

/**
 * RETRIEVE_MODE per RUNEIT_V1's diagram:
 *   RETRIEVE_PWD_MODE (decrypt) -> SHOW_TABLE_ENTRIES -> PWD_ID_SELECTION
 *   -> SHOW_PWD_ENTRIES -> back to PWD_ID_SELECTION (or 'q' -> mode_op_done)
 *
 * Decryption happens once, synchronously, in Mode_Retrieve_Enter(), with the
 * key of the open session; the decrypted table is kept in RAM for repeated
 * lookups until the user exits.
 */

/** Returns false (nothing decrypted) if there is no open session. */
bool Mode_Retrieve_Enter(void);

/** Call once per main-loop iteration while in RETRIEVE_MODE.
 * Returns true once the user has exited back to MODE_SELECTION. */
bool Mode_Retrieve_Run(void);

/** Wipes the decrypted table from RAM (used on exit and by the panic button). */
void Mode_Retrieve_Wipe(void);

#endif /* MODE_RETRIEVE_H */
