#ifndef ENTROPY_SOURCE_H
#define ENTROPY_SOURCE_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/**
 * Blocking wrapper around the ADC noise source for callers that need a few
 * random bytes once, such as a fresh KDF salt. GENERATE_MODE does not use it:
 * it drives the sampling from its own state machine so the main loop keeps
 * running.
 *
 * Returns false and leaves *out untouched if the source fails a health test
 * or does not deliver in time; the caller must then fall back to something
 * else rather than use a predictable value.
 */
bool Entropy_Source_GetBytes(uint8_t *out, size_t len);

#endif /* ENTROPY_SOURCE_H */
