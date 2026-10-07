#include <string.h>
#include "aes_ctr.h"
#include "secure_zero.h"

void AesCtr_Apply(uint8_t *buf, size_t len,
                  const uint8_t key[AES128_KEY_LEN],
                  const uint8_t iv[AES128_BLOCK_LEN])
{
    uint8_t counter[AES128_BLOCK_LEN];
    uint8_t keystream[AES128_BLOCK_LEN];
    size_t offset = 0U;

    (void)memcpy(counter, iv, sizeof(counter));

    while (offset < len) {
        size_t chunk = ((len - offset) < AES128_BLOCK_LEN) ? (len - offset) : AES128_BLOCK_LEN;
        size_t i;
        int32_t pos;

        Aes128_EncryptBlock(key, counter, keystream);
        for (i = 0U; i < chunk; i++) {
            buf[offset + i] ^= keystream[i];
        }
        offset += chunk;

        for (pos = (int32_t)AES128_BLOCK_LEN - 1; pos >= 0; pos--) {
            counter[pos]++;
            if (counter[pos] != 0U) {
                break;
            }
        }
    }

    Secure_Zero(keystream, sizeof(keystream));
    Secure_Zero(counter, sizeof(counter));
}
