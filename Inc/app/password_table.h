/**
 * @file    password_table.h
 * @brief   In-RAM password table layout and USART presentation helpers.
 *
 * This struct is the plaintext form of the data that lives, encrypted,
 * inside a flash partition (see partition_store.h). It must stay a plain
 * fixed-size POD layout since it is encrypted/decrypted byte-for-byte.
 */
#ifndef PASSWORD_TABLE_H
#define PASSWORD_TABLE_H

#include <stdint.h>
#include <stdbool.h>

#define PWD_TABLE_MAX_ENTRIES   31U   /* ids 0..30, per RUNEIT_V1 diagram */
#define PWD_NAME_LEN             16U  /* includes NUL terminator          */
#define PWD_SECRET_LEN           32U  /* includes NUL terminator          */

typedef struct {
    char name[PWD_NAME_LEN];
    char password[PWD_SECRET_LEN];
} pwd_entry_t;

typedef struct {
    pwd_entry_t entries[PWD_TABLE_MAX_ENTRIES];
} pwd_table_t;

/** Clears the table (all entries considered unused). */
void Password_Table_InitEmpty(pwd_table_t *table);

/** An entry is "used" when it has a non-empty name. */
bool Password_Table_EntryIsUsed(const pwd_entry_t *entry);

/** Sends "id - name" for every used entry over USART2 (SHOW_TABLE_ENTRIES). */
void Password_Table_ShowEntries(const pwd_table_t *table);

/** Sends "name : password" for one entry id over USART2 (SHOW_PWD_ENTRIES). */
void Password_Table_ShowEntry(const pwd_table_t *table, uint32_t id);

/** Zeroes the buffer the two Show functions format plaintext into. */
void Password_Table_WipeScratch(void);

#endif /* PASSWORD_TABLE_H */
