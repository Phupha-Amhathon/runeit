#include "crypto.h"

/* FNV-1a hash, salted: the salt is folded in as the starting state
 * instead of the fixed FNV offset basis, so two devices (or two
 * resets that pick a new random salt) hash the same master key
 * string to two different values. */
uint32_t Crypto_HashKey(const char *keyStr, uint32_t salt) {
    uint32_t hash = 2166136261U ^ salt;
    uint16_t i = 0;

    while (keyStr[i] != '\0') {
        hash ^= (uint8_t)keyStr[i];
        hash *= 16777619U;
        i++;
    }
    return hash;
}

/* xorshift32: a small PRNG used to turn one 32-bit key into a long
 * keystream. Deterministic - the same seed always reproduces the
 * same keystream, which is what makes encrypt/decrypt symmetric. */
static uint32_t Xorshift32_Next(uint32_t *state) {
    uint32_t x = *state;

    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

void Crypto_XorProcess(uint8_t *data, uint16_t len, uint32_t key) {
    uint32_t state = (key != 0U) ? key : 0xA5A5A5A5U;
    uint16_t i = 0;

    while (i < len) {
        uint32_t rnd = Xorshift32_Next(&state);
        uint8_t keystreamByte = (uint8_t)(rnd & 0xFFU);

        data[i] = data[i] ^ keystreamByte;
        i++;
    }
}
