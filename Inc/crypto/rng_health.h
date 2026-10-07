#ifndef RNG_HEALTH_H
#define RNG_HEALTH_H

#include <stdint.h>

/**
 * Continuous health tests for a raw entropy source, per NIST SP 800-90B
 * section 4.4: the Repetition Count Test catches a stuck or disconnected
 * source, and the Adaptive Proportion Test catches one that has degraded to
 * a strong bias within a window. Feed every raw sample through
 * RNG_Health_Check() before it is used; a failing sample must be discarded.
 *
 * Cutoffs assume a worst-case min-entropy of 0.90 bit per raw ADC sample
 * (measured 0.99 on real hardware) and a 2^-20 false-alarm rate.
 */

/* C = 1 + ceil(20 / H), SP 800-90B 4.4.1 */
#define RNG_HEALTH_RCT_CUTOFF   24U

/* Cutoff for a 512-sample window at H = 0.90 bit */
#define RNG_HEALTH_APT_WINDOW   512U
#define RNG_HEALTH_APT_CUTOFF   329U

typedef enum {
    RNG_HEALTH_OK = 0,
    RNG_HEALTH_FAIL_REPETITION,
    RNG_HEALTH_FAIL_ADAPTIVE,
} rng_health_result_t;

typedef struct {
    uint16_t last_value;
    uint16_t repeat_count;
    uint16_t window_ref_value;
    uint16_t window_match_count;
    uint16_t window_pos;
    uint8_t  started;
} rng_health_state_t;

/** Resets one test instance; use one instance per physical entropy channel. */
void RNG_Health_Init(rng_health_state_t *state);

/** Feeds one raw sample into both tests. RNG_HEALTH_OK means it is usable. */
rng_health_result_t RNG_Health_Check(rng_health_state_t *state, uint16_t sample);

#endif /* RNG_HEALTH_H */
