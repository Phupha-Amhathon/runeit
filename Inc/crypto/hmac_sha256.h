#ifndef HMAC_SHA256_H
#define HMAC_SHA256_H

#include <stdint.h>
#include <stddef.h>
#include "sha256.h"

#define HMAC_SHA256_LEN SHA256_DIGEST_BYTES

/* Keyed context holding the pre-hashed ipad/opad blocks, so repeated HMACs
 * with one key (PBKDF2) cost 2 SHA-256 compressions instead of 4. */
typedef struct {
    sha256_ctx_t inner;
    sha256_ctx_t outer;
} hmac_sha256_ctx_t;

void HmacSha256_Init(hmac_sha256_ctx_t *ctx, const uint8_t *key, size_t key_len);

/* out may alias msg. */
void HmacSha256_Compute(const hmac_sha256_ctx_t *ctx, const uint8_t *msg, size_t msg_len,
                        uint8_t out[HMAC_SHA256_LEN]);

void HmacSha256(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t msg_len,
                uint8_t out[HMAC_SHA256_LEN]);

#endif /* HMAC_SHA256_H */
