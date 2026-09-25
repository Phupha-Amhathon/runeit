#include <string.h>
#include "session.h"
#include "kdf.h"
#include "secure_zero.h"
#include "critical_drv.h"
#include "uid_drv.h"
#include "systick_drv.h"

static uint8_t s_key[PARTITION_KEY_LEN];
static volatile bool s_authorized = false;
/* Bumped by every panic. An operation remembers the value it started with
 * and only publishes its result if it is unchanged. */
static volatile uint32_t s_generation = 0U;
static uint32_t s_op_generation = 0U;

static const pwd_table_t s_empty_table;

static bool KdfCancelled(void)
{
    return s_generation != s_op_generation;
}

bool Session_MkPolicyOk(const char *mk, size_t len)
{
    size_t i;

    if ((len < MK_MIN_LEN) || (len > MK_MAX_LEN)) {
        return false;
    }
    for (i = 0U; i < len; i++) {
        if (((uint8_t)mk[i] < 0x20U) || ((uint8_t)mk[i] > 0x7EU)) {
            return false;
        }
    }
    return true;
}

bool Session_IsAuthorized(void)
{
    return s_authorized;
}

void Session_Wipe(void)
{
    s_generation++;
    s_authorized = false;
    Secure_Zero(s_key, sizeof(s_key));
}

static bool OpenIfCurrent(uint32_t generation, const uint8_t enc[PARTITION_KEY_LEN])
{
    bool ok = false;
    uint32_t saved = Critical_Enter();

    if (generation == s_generation) {
        (void)memcpy(s_key, enc, sizeof(s_key));
        s_authorized = true;
        ok = true;
    }
    Critical_Exit(saved);
    return ok;
}

static bool CopyKey(uint8_t out[PARTITION_KEY_LEN])
{
    bool ok = false;
    uint32_t saved = Critical_Enter();

    if (s_authorized) {
        (void)memcpy(out, s_key, PARTITION_KEY_LEN);
        ok = true;
    }
    Critical_Exit(saved);
    return ok;
}

/* Unique per device and per commit. Not secret (it is stored in the header);
 * it only has to differ between devices/keys. Replace with the RNG once the
 * generator branch provides one. */
static void MakeSalt(uint8_t salt[PARTITION_SALT_LEN])
{
    const partition_info_t *active = Partition_Store_Active();
    uint32_t uid[3];
    uint32_t mix = ((active != NULL) ? active->header.version : 0U) ^ SysTick_Drv_Millis();

    Uid_Drv_Read(uid);
    (void)memcpy(salt, uid, sizeof(uid));
    (void)memcpy(&salt[sizeof(uid)], &mix, sizeof(mix));
}

session_result_t Session_Authenticate(const char *mk, size_t len)
{
    const partition_info_t *active = Partition_Store_Active();
    partition_header_t header;
    uint8_t auth[PARTITION_KEY_LEN];
    uint8_t enc[PARTITION_KEY_LEN];
    uint32_t generation;
    session_result_t result;

    if (active == NULL) {
        return SESSION_NO_PARTITION;
    }
    if (!Session_MkPolicyOk(mk, len)) {
        return SESSION_WRONG_KEY;
    }

    header = active->header;
    generation = s_generation;
    s_op_generation = generation;

    if (!Kdf_DeriveKeys((const uint8_t *)mk, len, header.salt, header.kdf_iter,
                        KdfCancelled, auth, enc)) {
        result = SESSION_CANCELLED;
    } else if (!Kdf_ConstTimeEqual(auth, header.auth, sizeof(auth))) {
        result = SESSION_WRONG_KEY;
    } else if (!OpenIfCurrent(generation, enc)) {
        result = SESSION_CANCELLED;
    } else {
        result = SESSION_OK;
    }

    Secure_Zero(auth, sizeof(auth));
    Secure_Zero(enc, sizeof(enc));
    return result;
}

session_result_t Session_SetNewKey(const char *mk, size_t len, const pwd_table_t *table)
{
    uint8_t salt[PARTITION_SALT_LEN];
    uint8_t auth[PARTITION_KEY_LEN];
    uint8_t enc[PARTITION_KEY_LEN];
    uint32_t generation;
    session_result_t result;

    if (!Session_MkPolicyOk(mk, len)) {
        return SESSION_INVALID_KEY;
    }

    MakeSalt(salt);
    generation = s_generation;
    s_op_generation = generation;

    if (!Kdf_DeriveKeys((const uint8_t *)mk, len, salt, KDF_ITERATIONS, KdfCancelled, auth, enc)) {
        result = SESSION_CANCELLED;
    } else if (generation != s_generation) {
        result = SESSION_CANCELLED;
    } else if (!Partition_Store_Commit(enc, salt, KDF_ITERATIONS, auth,
                                       (table != NULL) ? table : &s_empty_table)) {
        result = SESSION_STORAGE_ERROR;
    } else if (!OpenIfCurrent(generation, enc)) {
        result = SESSION_CANCELLED;
    } else {
        result = SESSION_OK;
    }

    Secure_Zero(auth, sizeof(auth));
    Secure_Zero(enc, sizeof(enc));
    return result;
}

bool Session_LoadTable(pwd_table_t *out)
{
    uint8_t key[PARTITION_KEY_LEN];
    bool ok = CopyKey(key) && Partition_Store_Load(key, out);

    Secure_Zero(key, sizeof(key));
    return ok;
}

bool Session_Save(const pwd_table_t *table)
{
    const partition_info_t *active = Partition_Store_Active();
    partition_header_t header;
    uint8_t key[PARTITION_KEY_LEN];
    bool ok = false;

    if (active == NULL) {
        return false;
    }
    header = active->header;

    if (CopyKey(key)) {
        ok = Partition_Store_Commit(key, header.salt, header.kdf_iter, header.auth, table);
    }
    Secure_Zero(key, sizeof(key));
    return ok;
}
