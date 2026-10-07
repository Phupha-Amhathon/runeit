/**
 * @file    session.h
 * @brief   The authorized session: the derived cipher key in RAM, and every
 *          operation that creates, uses or destroys it.
 *
 * This is the interface other modes (e.g. GENERATE_MODE) use:
 *   Session_IsAuthorized(), Session_LoadTable(), Session_Save().
 */
#ifndef SESSION_H
#define SESSION_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "partition_store.h"
#include "password_table.h"

#define MK_MIN_LEN 8U
#define MK_MAX_LEN 31U

typedef enum {
    SESSION_OK = 0,
    SESSION_WRONG_KEY,       /* master key did not match the stored auth value */
    SESSION_INVALID_KEY,     /* master key breaks the length/character rules   */
    SESSION_CANCELLED,       /* panic button fired during the operation        */
    SESSION_STORAGE_ERROR,   /* flash write did not verify; old data untouched */
    SESSION_NO_PARTITION,    /* nothing stored yet -> FIRST_MEET               */
} session_result_t;

/** 8-31 characters, every one printable ASCII (0x20-0x7E). */
bool Session_MkPolicyOk(const char *mk, size_t len);

bool Session_IsAuthorized(void);

/** Panic path. Interrupt-safe: zeroes the key, drops authorization, and makes
 *  any derivation that is still running discard its result. */
void Session_Wipe(void);

/** Derives keys from mk with the stored salt/iterations (slow: seconds) and
 *  opens the session if the stored auth value matches. */
session_result_t Session_Authenticate(const char *mk, size_t len);

/** Chooses a new master key: fresh salt, derives keys (slow), writes header+table
 *  to the inactive partition (table == NULL -> empty table), and opens the
 *  session with the new key. On any failure the previous partition and session
 *  are left as they were. */
session_result_t Session_SetNewKey(const char *mk, size_t len, const pwd_table_t *table);

/** Decrypts the active partition into *out with the session key. */
bool Session_LoadTable(pwd_table_t *out);

/** Persists *table with the current key (same salt/auth) to the inactive partition. */
bool Session_Save(const pwd_table_t *table);

#endif /* SESSION_H */
