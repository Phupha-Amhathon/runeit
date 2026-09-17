#include "xor_cipher.h"
#include "sha256.h"

void XorCipher_Apply(uint8_t *buf, size_t len,
                      const uint8_t *key, size_t key_len,
                      uint32_t nonce)
{
    uint32_t block_index = 0U;
    size_t offset = 0U;
    uint8_t nonce_be[4] = {
        (uint8_t)(nonce >> 24), (uint8_t)(nonce >> 16),
        (uint8_t)(nonce >> 8),  (uint8_t)(nonce)
    };

    while (offset < len) {
        uint8_t counter_be[4] = {
            (uint8_t)(block_index >> 24), (uint8_t)(block_index >> 16),
            (uint8_t)(block_index >> 8),  (uint8_t)(block_index)
        };
        uint8_t keystream[SHA256_DIGEST_BYTES];
        sha256_ctx_t ctx;

        SHA256_Init(&ctx);
        SHA256_Update(&ctx, key, key_len);
        SHA256_Update(&ctx, nonce_be, sizeof(nonce_be));
        SHA256_Update(&ctx, counter_be, sizeof(counter_be));
        SHA256_Final(&ctx, keystream);

        size_t chunk = (len - offset) < SHA256_DIGEST_BYTES ? (len - offset) : SHA256_DIGEST_BYTES;
        for (size_t i = 0U; i < chunk; i++) {
            buf[offset + i] ^= keystream[i];
        }

        offset += chunk;
        block_index++;
    }
}
