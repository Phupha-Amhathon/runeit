#ifndef MODE_GENERATE_H
#define MODE_GENERATE_H

#include <stdint.h>
#include <stdbool.h>

/**
 * GENERATE_MODE: the user picks a table id and service name, a character set
 * and a length; the password is built from debiased ADC noise (see
 * entropy_pool.h). The mode then hands over to TOGGLE_PARTITION, which
 * encrypts the table with input_mk and commits it to the inactive flash
 * partition via Partition_Store_Commit().
 *
 *   PROMPT_ID -> [OVERWRITE?] -> NAME -> CLASSES -> LENGTH
 *   -> SAMPLING (DMA blocks, non-blocking) -> READY -> (TOGGLE_PARTITION)
 *
 * An empty line cancels at any prompt. Nothing is written if the entropy
 * source fails a health test or the panic button fires mid-way.
 */

typedef enum {
    MODE_GENERATE_RUNNING = 0,
    MODE_GENERATE_FINISHED,       /* cancelled or failed: nothing to write, secrets wiped */
    MODE_GENERATE_READY_TO_SAVE,  /* password built: go to TOGGLE_PARTITION */
} mode_generate_status_t;

/** Decrypts the active table into RAM and resets the sub-state machine. */
void Mode_Generate_Enter(const uint8_t *input_mk, uint32_t mk_len);

/** Call once per main-loop iteration while in GENERATE_MODE. On READY_TO_SAVE
 * the table and password are still in RAM, waiting for Mode_Generate_Commit(). */
mode_generate_status_t Mode_Generate_Run(void);

/**
 * TOGGLE_PARTITION work for a generated password: writes the entry into the
 * table and commits it with interrupts masked, checks by re-reading flash that
 * the newer partition really is the active one, reports the result, then wipes
 * every secret. Does nothing but wipe if the panic button fired first.
 */
void Mode_Generate_Commit(void);

/** Wipes the table, the generated password and the entropy pool, and
 * aborts any commit still to come (used by the panic-button handler). */
void Mode_Generate_Wipe(void);

#endif /* MODE_GENERATE_H */
