/**
 * @file sha256.c
 * @brief Small, dependency-free SHA-256 (FIPS 180-4), software only.
 */
#include <string.h>
#include "sha256.h"

#define SHA256_ROUNDS        64U
#define SHA256_MSG_WORDS     16U   /* words loaded straight from one block */
#define SHA256_BLOCK_BITS    512U
#define SHA256_PAD_START     0x80U /* the single 1 bit that ends the message */
#define SHA256_LEN_HI_OFFSET 56U   /* the 64-bit message length fills bytes 56-63 */
#define SHA256_LEN_LO_OFFSET 60U
#define SHA256_WORD_BYTES    4U
#define BITS_PER_BYTE        8U
#define WORD_HALF_SHIFT      32U

/* Indices of the eight working variables a..h in the state array */
#define SHA_A 0U
#define SHA_B 1U
#define SHA_C 2U
#define SHA_D 3U
#define SHA_E 4U
#define SHA_F 5U
#define SHA_G 6U
#define SHA_H 7U

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32U - (n))))
#define CH(x, y, z)  (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define EP0(x) (ROTR(x, 2U)  ^ ROTR(x, 13U) ^ ROTR(x, 22U))
#define EP1(x) (ROTR(x, 6U)  ^ ROTR(x, 11U) ^ ROTR(x, 25U))
#define SIG0(x) (ROTR(x, 7U) ^ ROTR(x, 18U) ^ ((x) >> 3U))
#define SIG1(x) (ROTR(x, 17U) ^ ROTR(x, 19U) ^ ((x) >> 10U))
#define SCHEDULE(m, i) (SIG1((m)[(i) - 2U]) + ((m)[(i) - 7U] + (SIG0((m)[(i) - 15U]) + (m)[(i) - 16U])))

#define LOAD_BE32(p) (((uint32_t)(p)[0] << 24U) | ((uint32_t)(p)[1] << 16U) | \
                      ((uint32_t)(p)[2] << 8U) | (uint32_t)(p)[3])

static const uint32_t K[SHA256_ROUNDS] = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
    0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
    0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
    0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
    0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
    0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
    0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
    0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
    0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
    0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
    0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
    0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
    0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
    0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
    0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U
};

/* Initial hash value H(0) */
static const uint32_t s_h0[SHA256_DIGEST_WORDS] = {
    0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
    0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U
};

static void StoreBe32(uint8_t *p, uint32_t v)
{
    uint32_t k;

    for (k = 0U; k < SHA256_WORD_BYTES; k++) {
        p[k] = (uint8_t)(v >> (BITS_PER_BYTE * ((SHA256_WORD_BYTES - 1U) - k)));
    }
}

static void SHA256_Transform(sha256_ctx_t *ctx, const uint8_t data[SHA256_BLOCK_BYTES])
{
    uint32_t m[SHA256_ROUNDS];
    uint32_t i;

    for (i = 0U; i < SHA256_MSG_WORDS; i++) {
        m[i] = LOAD_BE32(&data[i * SHA256_WORD_BYTES]);
    }
    for (; i < SHA256_ROUNDS; i++) {
        m[i] = SCHEDULE(m, i);
    }

    uint32_t a = ctx->state[SHA_A];
    uint32_t b = ctx->state[SHA_B];
    uint32_t c = ctx->state[SHA_C];
    uint32_t d = ctx->state[SHA_D];
    uint32_t e = ctx->state[SHA_E];
    uint32_t f = ctx->state[SHA_F];
    uint32_t g = ctx->state[SHA_G];
    uint32_t h = ctx->state[SHA_H];

    for (i = 0U; i < SHA256_ROUNDS; i++) {
        uint32_t t1 = h + (EP1(e) + (CH(e, f, g) + (K[i] + m[i])));
        uint32_t t2 = EP0(a) + MAJ(a, b, c);
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }

    ctx->state[SHA_A] += a;
    ctx->state[SHA_B] += b;
    ctx->state[SHA_C] += c;
    ctx->state[SHA_D] += d;
    ctx->state[SHA_E] += e;
    ctx->state[SHA_F] += f;
    ctx->state[SHA_G] += g;
    ctx->state[SHA_H] += h;
}

void SHA256_Init(sha256_ctx_t *ctx)
{
    ctx->buffer_len = 0U;
    ctx->bitlen = 0U;
    (void)memcpy(ctx->state, s_h0, sizeof(ctx->state));
}

void SHA256_Update(sha256_ctx_t *ctx, const uint8_t *data, size_t len)
{
    for (size_t i = 0U; i < len; i++) {
        ctx->buffer[ctx->buffer_len] = data[i];
        ctx->buffer_len++;
        if (ctx->buffer_len == SHA256_BLOCK_BYTES) {
            SHA256_Transform(ctx, ctx->buffer);
            ctx->bitlen += SHA256_BLOCK_BITS;
            ctx->buffer_len = 0U;
        } else {
            /* No action */
        }
    }
}

void SHA256_Final(sha256_ctx_t *ctx, uint8_t digest[SHA256_DIGEST_BYTES])
{
    uint32_t i = ctx->buffer_len;

    ctx->buffer[i] = SHA256_PAD_START;
    i++;
    if (ctx->buffer_len < SHA256_LEN_HI_OFFSET) {
        while (i < SHA256_LEN_HI_OFFSET) {
            ctx->buffer[i] = 0x00U;
            i++;
        }
    } else {
        while (i < SHA256_BLOCK_BYTES) {
            ctx->buffer[i] = 0x00U;
            i++;
        }
        SHA256_Transform(ctx, ctx->buffer);
        (void)memset(ctx->buffer, 0, SHA256_LEN_HI_OFFSET);
    }

    ctx->bitlen += (uint64_t)ctx->buffer_len * BITS_PER_BYTE;
    StoreBe32(&ctx->buffer[SHA256_LEN_HI_OFFSET], (uint32_t)(ctx->bitlen >> WORD_HALF_SHIFT));
    StoreBe32(&ctx->buffer[SHA256_LEN_LO_OFFSET], (uint32_t)ctx->bitlen);
    SHA256_Transform(ctx, ctx->buffer);

    for (i = 0U; i < SHA256_DIGEST_WORDS; i++) {
        StoreBe32(&digest[i * SHA256_WORD_BYTES], ctx->state[i]);
    }
}

void SHA256_Compute(const uint8_t *data, size_t len, uint8_t digest[SHA256_DIGEST_BYTES])
{
    sha256_ctx_t ctx;
    SHA256_Init(&ctx);
    SHA256_Update(&ctx, data, len);
    SHA256_Final(&ctx, digest);
}
