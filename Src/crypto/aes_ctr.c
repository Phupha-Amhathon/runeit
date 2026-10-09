#include <string.h>
#include <stdbool.h>
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
        size_t chunk;
        size_t i;
        size_t pos = AES128_BLOCK_LEN;
        bool carry = true;

        if ((len - offset) < AES128_BLOCK_LEN) {
            chunk = len - offset;
        } else {
            chunk = AES128_BLOCK_LEN;
        }

        Aes128_EncryptBlock(key, counter, keystream);
        for (i = 0U; i < chunk; i++) {
            buf[offset + i] ^= keystream[i];
        }
        offset += chunk;

        /* Big-endian increment: carry into the next byte up only on wrap */
        while (carry && (pos > 0U)) {
            pos--;
            counter[pos]++;
            carry = (counter[pos] == 0U);
        }
    }

    Secure_Zero(keystream, sizeof(keystream));
    Secure_Zero(counter, sizeof(counter));
}
