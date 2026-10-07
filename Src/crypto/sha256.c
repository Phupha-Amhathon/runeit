/**
 * @file sha256.c
 * @brief Small, dependency-free SHA-256 (FIPS 180-4), software only.
 */
#include <string.h>
#include "sha256.h"

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32U - (n))))
#define CH(x, y, z)  (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define EP0(x) (ROTR(x, 2)  ^ ROTR(x, 13) ^ ROTR(x, 22))
#define EP1(x) (ROTR(x, 6)  ^ ROTR(x, 11) ^ ROTR(x, 25))
#define SIG0(x) (ROTR(x, 7) ^ ROTR(x, 18) ^ ((x) >> 3))
#define SIG1(x) (ROTR(x, 17) ^ ROTR(x, 19) ^ ((x) >> 10))

static const uint32_t K[64] = {
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

static void SHA256_Transform(sha256_ctx_t *ctx, const uint8_t data[64])
{
    uint32_t m[64];
    uint32_t i, j;

    for (i = 0U, j = 0U; i < 16U; i++, j += 4U) {
        m[i] = ((uint32_t)data[j] << 24) | ((uint32_t)data[j + 1U] << 16) |
               ((uint32_t)data[j + 2U] << 8) | (uint32_t)data[j + 3U];
    }
    for (; i < 64U; i++) {
        m[i] = SIG1(m[i - 2U]) + m[i - 7U] + SIG0(m[i - 15U]) + m[i - 16U];
    }

    uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
    uint32_t e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];

    for (i = 0U; i < 64U; i++) {
        uint32_t t1 = h + EP1(e) + CH(e, f, g) + K[i] + m[i];
        uint32_t t2 = EP0(a) + MAJ(a, b, c);
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

void SHA256_Init(sha256_ctx_t *ctx)
{
    ctx->buffer_len = 0U;
    ctx->bitlen = 0U;
    ctx->state[0] = 0x6a09e667U; ctx->state[1] = 0xbb67ae85U;
    ctx->state[2] = 0x3c6ef372U; ctx->state[3] = 0xa54ff53aU;
    ctx->state[4] = 0x510e527fU; ctx->state[5] = 0x9b05688cU;
    ctx->state[6] = 0x1f83d9abU; ctx->state[7] = 0x5be0cd19U;
}

void SHA256_Update(sha256_ctx_t *ctx, const uint8_t *data, size_t len)
{
    for (size_t i = 0U; i < len; i++) {
        ctx->buffer[ctx->buffer_len] = data[i];
        ctx->buffer_len++;
        if (ctx->buffer_len == 64U) {
            SHA256_Transform(ctx, ctx->buffer);
            ctx->bitlen += 512U;
            ctx->buffer_len = 0U;
        }
    }
}

void SHA256_Final(sha256_ctx_t *ctx, uint8_t digest[SHA256_DIGEST_BYTES])
{
    uint32_t i = ctx->buffer_len;

    if (ctx->buffer_len < 56U) {
        ctx->buffer[i++] = 0x80U;
        while (i < 56U) {
            ctx->buffer[i++] = 0x00U;
        }
    } else {
        ctx->buffer[i++] = 0x80U;
        while (i < 64U) {
            ctx->buffer[i++] = 0x00U;
        }
        SHA256_Transform(ctx, ctx->buffer);
        memset(ctx->buffer, 0, 56U);
    }

    ctx->bitlen += (uint64_t)ctx->buffer_len * 8U;
    ctx->buffer[63] = (uint8_t)(ctx->bitlen);
    ctx->buffer[62] = (uint8_t)(ctx->bitlen >> 8);
    ctx->buffer[61] = (uint8_t)(ctx->bitlen >> 16);
    ctx->buffer[60] = (uint8_t)(ctx->bitlen >> 24);
    ctx->buffer[59] = (uint8_t)(ctx->bitlen >> 32);
    ctx->buffer[58] = (uint8_t)(ctx->bitlen >> 40);
    ctx->buffer[57] = (uint8_t)(ctx->bitlen >> 48);
    ctx->buffer[56] = (uint8_t)(ctx->bitlen >> 56);
    SHA256_Transform(ctx, ctx->buffer);

    for (i = 0U; i < 4U; i++) {
        digest[i]        = (uint8_t)(ctx->state[0] >> (24U - (i * 8U)));
        digest[i + 4U]   = (uint8_t)(ctx->state[1] >> (24U - (i * 8U)));
        digest[i + 8U]   = (uint8_t)(ctx->state[2] >> (24U - (i * 8U)));
        digest[i + 12U]  = (uint8_t)(ctx->state[3] >> (24U - (i * 8U)));
        digest[i + 16U]  = (uint8_t)(ctx->state[4] >> (24U - (i * 8U)));
        digest[i + 20U]  = (uint8_t)(ctx->state[5] >> (24U - (i * 8U)));
        digest[i + 24U]  = (uint8_t)(ctx->state[6] >> (24U - (i * 8U)));
        digest[i + 28U]  = (uint8_t)(ctx->state[7] >> (24U - (i * 8U)));
    }
}

void SHA256_Compute(const uint8_t *data, size_t len, uint8_t digest[SHA256_DIGEST_BYTES])
{
    sha256_ctx_t ctx;
    SHA256_Init(&ctx);
    SHA256_Update(&ctx, data, len);
    SHA256_Final(&ctx, digest);
}
