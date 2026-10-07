#include <string.h>
#include "hmac_sha256.h"
#include "secure_zero.h"

#define HMAC_BLOCK_LEN 64U

void HmacSha256_Init(hmac_sha256_ctx_t *ctx, const uint8_t *key, size_t key_len)
{
    uint8_t k[HMAC_BLOCK_LEN] = {0};
    uint8_t pad[HMAC_BLOCK_LEN];
    size_t i;

    if (key_len > HMAC_BLOCK_LEN) {
        SHA256_Compute(key, key_len, k);
    } else {
        (void)memcpy(k, key, key_len);
    }

    for (i = 0U; i < HMAC_BLOCK_LEN; i++) {
        pad[i] = k[i] ^ 0x36U;
    }
    SHA256_Init(&ctx->inner);
    SHA256_Update(&ctx->inner, pad, HMAC_BLOCK_LEN);

    for (i = 0U; i < HMAC_BLOCK_LEN; i++) {
        pad[i] = k[i] ^ 0x5CU;
    }
    SHA256_Init(&ctx->outer);
    SHA256_Update(&ctx->outer, pad, HMAC_BLOCK_LEN);

    Secure_Zero(k, sizeof(k));
    Secure_Zero(pad, sizeof(pad));
}

void HmacSha256_Compute(const hmac_sha256_ctx_t *ctx, const uint8_t *msg, size_t msg_len,
                        uint8_t out[HMAC_SHA256_LEN])
{
    sha256_ctx_t inner = ctx->inner;
    sha256_ctx_t outer = ctx->outer;
    uint8_t digest[HMAC_SHA256_LEN];

    SHA256_Update(&inner, msg, msg_len);
    SHA256_Final(&inner, digest);
    SHA256_Update(&outer, digest, sizeof(digest));
    SHA256_Final(&outer, out);

    Secure_Zero(digest, sizeof(digest));
    Secure_Zero(&inner, sizeof(inner));
    Secure_Zero(&outer, sizeof(outer));
}

void HmacSha256(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t msg_len,
                uint8_t out[HMAC_SHA256_LEN])
{
    hmac_sha256_ctx_t ctx;

    HmacSha256_Init(&ctx, key, key_len);
    HmacSha256_Compute(&ctx, msg, msg_len, out);
    Secure_Zero(&ctx, sizeof(ctx));
}
