#include <string.h>
#include "kdf.h"
#include "hmac_sha256.h"
#include "secure_zero.h"

#define KDF_CANCEL_POLL_MASK 31U

static const uint8_t s_label_auth[] = "RUNEIT-auth-v1";
static const uint8_t s_label_enc[]  = "RUNEIT-enc-v1";

bool Kdf_Pbkdf2Sha256(const uint8_t *password, size_t password_len,
                      const uint8_t *salt, size_t salt_len,
                      uint32_t iterations, kdf_cancel_fn cancel,
                      uint8_t out[KDF_KEY_LEN])
{
    hmac_sha256_ctx_t hmac;
    uint8_t u[KDF_KEY_LEN];
    uint8_t t[KDF_KEY_LEN];
    uint8_t first[KDF_SALT_LEN + 4U];
    bool ok = true;
    uint32_t i;
    uint32_t j;

    if (salt_len > KDF_SALT_LEN) {
        return false;
    }

    HmacSha256_Init(&hmac, password, password_len);

    (void)memcpy(first, salt, salt_len);
    first[salt_len]      = 0U;
    first[salt_len + 1U] = 0U;
    first[salt_len + 2U] = 0U;
    first[salt_len + 3U] = 1U; /* block index 1, big endian */
    HmacSha256_Compute(&hmac, first, salt_len + 4U, u);
    (void)memcpy(t, u, sizeof(t));

    for (i = 1U; i < iterations; i++) {
        if ((cancel != NULL) && ((i & KDF_CANCEL_POLL_MASK) == 0U) && cancel()) {
            ok = false;
            break;
        }
        HmacSha256_Compute(&hmac, u, sizeof(u), u);
        for (j = 0U; j < KDF_KEY_LEN; j++) {
            t[j] ^= u[j];
        }
    }

    if (ok) {
        (void)memcpy(out, t, KDF_KEY_LEN);
    }

    Secure_Zero(&hmac, sizeof(hmac));
    Secure_Zero(u, sizeof(u));
    Secure_Zero(t, sizeof(t));
    return ok;
}

bool Kdf_DeriveKeys(const uint8_t *mk, size_t mk_len,
                    const uint8_t salt[KDF_SALT_LEN], uint32_t iterations,
                    kdf_cancel_fn cancel,
                    uint8_t auth[KDF_KEY_LEN], uint8_t enc[KDF_KEY_LEN])
{
    uint8_t k[KDF_KEY_LEN];

    if (!Kdf_Pbkdf2Sha256(mk, mk_len, salt, KDF_SALT_LEN, iterations, cancel, k)) {
        return false;
    }
    HmacSha256(k, sizeof(k), s_label_auth, sizeof(s_label_auth) - 1U, auth);
    HmacSha256(k, sizeof(k), s_label_enc, sizeof(s_label_enc) - 1U, enc);
    Secure_Zero(k, sizeof(k));
    return true;
}

bool Kdf_ConstTimeEqual(const uint8_t *a, const uint8_t *b, size_t n)
{
    uint8_t diff = 0U;
    size_t i;

    for (i = 0U; i < n; i++) {
        diff |= (uint8_t)(a[i] ^ b[i]);
    }
    return diff == 0U;
}
