/**
 ******************************************************************************
 * @file           : rng_health.h
 * @brief          : Entropy-source health test for the ADC noise generator
 ******************************************************************************
 *
 * Implements the two continuous health tests required by NIST SP 800-90B
 * section 4.4: the Repetition Count Test (catches a stuck/failed source,
 * e.g. a disconnected sensor line) and the Adaptive Proportion Test
 * (catches a source that has degraded to a strong bias within a window).
 *
 * Not wired into main.c yet - main.c is left unmodified until a board is
 * available to test against. Call rht_init() once per channel, then
 * rht_check() after every adc_read() on that channel; a non-OK return
 * means the raw sample must not be used as entropy.
 *
 ******************************************************************************
 */

#ifndef RNG_HEALTH_H
#define RNG_HEALTH_H

#include <stdint.h>

/* Assumed worst-case min-entropy per raw ADC sample, in bits. Measured
 * on real hardware (adc_samples.csv, 10000 samples) as 0.99 bit; 0.90 is
 * used here to keep margin against a slightly worse batch/board. */
#define RHT_ASSUMED_MIN_ENTROPY_BITS   (0.90)

/* Repetition Count Test: fails if the same raw value repeats this many
 * times in a row. C = 1 + ceil(20 / H), per SP 800-90B 4.4.1, false
 * alarm rate 2^-20. */
#define RHT_RCT_CUTOFF                 (24U)

/* Adaptive Proportion Test: fails if the value seen first in a window
 * of RHT_APT_WINDOW samples reappears at least RHT_APT_CUTOFF times in
 * that window. Cutoff for H=0.90 bit, window 512, false alarm 2^-20. */
#define RHT_APT_WINDOW                 (512U)
#define RHT_APT_CUTOFF                 (329U)

typedef enum
{
    RHT_OK = 0,
    RHT_FAIL_REPETITION,
    RHT_FAIL_ADAPTIVE
} rht_result_t;

typedef struct
{
    uint16_t last_value;
    uint16_t repeat_count;
    uint16_t window_ref_value;
    uint16_t window_match_count;
    uint16_t window_pos;
    uint8_t  started;
} rht_state_t;

/* Resets one health-test instance. Call once per entropy channel
 * (e.g. one instance for PA0/temp, one for PA1/light) before use. */
void rht_init(rht_state_t *state);

/* Feeds one raw ADC sample into both tests. Returns RHT_OK if the
 * sample is safe to use as entropy input, otherwise the test that
 * failed. On failure the caller must discard the sample and should
 * stop producing output until health checks pass again. */
rht_result_t rht_check(rht_state_t *state, uint16_t sample);

#endif /* RNG_HEALTH_H */
