#ifndef CRYPTO_H
#define CRYPTO_H

#include <stdint.h>

/* Turns a master-key string into one 32-bit hash, mixed with a salt
 * so the same master key produces a different hash on every device
 * (protects against a precomputed rainbow-table lookup). The same
 * salt must be reused on every future call for the same key to hash
 * the same way again (store the salt alongside the resulting hash,
 * not the master key itself). */
uint32_t Crypto_HashKey(const char *keyStr, uint32_t salt);

/* Encrypts or decrypts data in place with an XOR stream cipher (an
 * xorshift32 PRNG generates the keystream from key). One function
 * does both directions, since XOR with the same keystream undoes
 * itself: running this twice with the same key returns the original
 * data. */
void Crypto_XorProcess(uint8_t *data, uint16_t len, uint32_t key);

#endif
