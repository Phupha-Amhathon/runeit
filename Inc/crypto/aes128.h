#ifndef AES128_H
#define AES128_H

#include <stdint.h>

#define AES128_KEY_LEN    16U
#define AES128_BLOCK_LEN  16U

/** Encrypts one 16-byte block (FIPS-197). Only the encrypt direction exists
 * because CTR mode never needs the inverse cipher. */
void Aes128_EncryptBlock(const uint8_t key[AES128_KEY_LEN],
                         const uint8_t in[AES128_BLOCK_LEN],
                         uint8_t out[AES128_BLOCK_LEN]);

#endif /* AES128_H */
