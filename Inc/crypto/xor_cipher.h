#ifndef XOR_CIPHER_H
#define XOR_CIPHER_H

#include <stdint.h>
#include <stddef.h>

/**
 * Keystream (XOR) cipher: block i of the keystream is
 * SHA256(key || nonce || i), 32 bytes per block, XORed into buf. The same
 * call both encrypts and decrypts since XOR is its own inverse.
 *
 * This is a lightweight placeholder per the project's "research crypto"
 * TODO -- swap for AES later if time allows, without changing callers
 * (they only depend on this being deterministic and symmetric).
 */
void XorCipher_Apply(uint8_t *buf, size_t len,
                      const uint8_t *key, size_t key_len,
                      uint32_t nonce);

#endif /* XOR_CIPHER_H */
