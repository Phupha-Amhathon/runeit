#ifndef ENTROPY_POOL_H
#define ENTROPY_POOL_H

#include <stdint.h>
#include <stdbool.h>
#include "rng_health.h"

/**
 * Turns raw ADC samples from two independent noise channels into unbiased
 * entropy bytes. The raw ADC LSB is badly skewed on real hardware (measured
 * as far as 83/17 rather than 50/50), so each channel is Von Neumann
 * debiased: two consecutive samples from the same channel are compared by
 * their LSB, 01 yields 0 and 10 yields 1, and 00 or 11 yields nothing.
 * P(01) equals P(10) for any source bias, so the output bit is unbiased as
 * long as consecutive samples are roughly independent.
 *
 * No hardware dependency: callers fill sample buffers (see adc_drv) and
 * pass them in.
 */

/** Samples per channel the ADC driver is asked for in one block. */
#define ENTROPY_BLOCK_SAMPLES   512U

#define ENTROPY_CHANNELS        2U

/* One debiased bit per element. Sized for a fully unbiased block (half of
 * ENTROPY_BLOCK_SAMPLES pairs, one bit per pair) plus leftover bits carried
 * over from earlier blocks. */
#define ENTROPY_FIFO_CAP        512U

typedef struct {
    uint8_t  bits[ENTROPY_FIFO_CAP];
    uint16_t head;
    uint16_t count;
} entropy_fifo_t;

typedef struct {
    entropy_fifo_t     fifo[ENTROPY_CHANNELS];
    rng_health_state_t health[ENTROPY_CHANNELS];
} entropy_pool_t;

/** Empties the pool and resets both channels' health tests. */
void Entropy_Pool_Init(entropy_pool_t *pool);

/**
 * Health-checks then debiases one block of raw samples from a channel
 * (0 or 1). Returns false if the channel failed a health test or the
 * channel index is invalid; in that case nothing from the block is kept.
 */
bool Entropy_Pool_Absorb(entropy_pool_t *pool, uint8_t channel,
                          const uint16_t *samples, uint16_t count);

/**
 * Packs 8 debiased bits into *out, alternating channel 0 and channel 1 per
 * bit. Returns false without consuming anything if either channel has fewer
 * than 4 bits available.
 */
bool Entropy_Pool_TakeByte(entropy_pool_t *pool, uint8_t *out);

#endif /* ENTROPY_POOL_H */
