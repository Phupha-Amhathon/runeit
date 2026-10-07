#ifndef AES_CTR_H
#define AES_CTR_H

#include <stdint.h>
#include <stddef.h>
#include "aes128.h"

/**
 * AES-128 in CTR mode. The 16-byte counter block starts as iv and the whole
 * block is incremented as a big-endian number after every 16 bytes. The same
 * call encrypts and decrypts. It gives no integrity, so always pair it with a
 * MAC (see partition_store.c). Never reuse an iv with the same key.
 */
void AesCtr_Apply(uint8_t *buf, size_t len,
                  const uint8_t key[AES128_KEY_LEN],
                  const uint8_t iv[AES128_BLOCK_LEN]);

#endif /* AES_CTR_H */
