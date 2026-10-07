#ifndef KDF_H
#define KDF_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#define KDF_SALT_LEN        16U
#define KDF_KEY_LEN         32U
/* One PBKDF2 iteration costs about 24.7k instructions at -O0 (the CubeIDE
 * Debug default) and 8.6k at -O1 (measured on a Cortex-M4 model), i.e. roughly
 * 2000 iterations = 4 s at -O0 or 1.4 s at -O1 on the 16 MHz HSI clock (CPI
 * ~1.3). The screens print the real elapsed time so this can be tuned on the
 * board. The value used for a partition is stored in its header, so changing
 * this later does not lock out existing data. */
#define KDF_ITERATIONS      2000U

/* Polled every few iterations; return true to abort the derivation. */
typedef bool (*kdf_cancel_fn)(void);

/**
 * PBKDF2-HMAC-SHA256 producing one 32-byte block. Returns false (and writes
 * nothing to out) if cancelled. salt_len must be <= KDF_SALT_LEN.
 */
bool Kdf_Pbkdf2Sha256(const uint8_t *password, size_t password_len,
                      const uint8_t *salt, size_t salt_len,
                      uint32_t iterations, kdf_cancel_fn cancel,
                      uint8_t out[KDF_KEY_LEN]);

/**
 * K = PBKDF2(mk, salt, iterations); auth = HMAC(K,"RUNEIT-auth-v1") is what
 * gets stored, enc = HMAC(K,"RUNEIT-enc-v1") is the cipher key and is never
 * stored. Guessing through the stored hash or through the ciphertext therefore
 * costs an attacker the same full derivation. Returns false if cancelled.
 */
bool Kdf_DeriveKeys(const uint8_t *mk, size_t mk_len,
                    const uint8_t salt[KDF_SALT_LEN], uint32_t iterations,
                    kdf_cancel_fn cancel,
                    uint8_t auth[KDF_KEY_LEN], uint8_t enc[KDF_KEY_LEN]);

/** Compares n bytes without an early exit. */
bool Kdf_ConstTimeEqual(const uint8_t *a, const uint8_t *b, size_t n);

#endif /* KDF_H */
