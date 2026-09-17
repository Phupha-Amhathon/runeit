#ifndef SHA256_H
#define SHA256_H

#include <stdint.h>
#include <stddef.h>

#define SHA256_DIGEST_BYTES 32U
#define SHA256_DIGEST_WORDS  8U

typedef struct {
    uint32_t state[8];
    uint64_t bitlen;
    uint8_t  buffer[64];
    uint32_t buffer_len;
} sha256_ctx_t;

void SHA256_Init(sha256_ctx_t *ctx);
void SHA256_Update(sha256_ctx_t *ctx, const uint8_t *data, size_t len);
void SHA256_Final(sha256_ctx_t *ctx, uint8_t digest[SHA256_DIGEST_BYTES]);

/** One-shot helper: SHA256_Init + Update + Final. */
void SHA256_Compute(const uint8_t *data, size_t len, uint8_t digest[SHA256_DIGEST_BYTES]);

#endif /* SHA256_H */
