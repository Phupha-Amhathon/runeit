#include <string.h>
#include "entropy_pool.h"

#define SAMPLE_LSB      0x1U /* only the lowest ADC bit is harvested */
#define VN_PAIR_LEN     2U   /* Von Neumann consumes samples two at a time */
#define CHANNEL_TOGGLE  0x1U /* channel ^ 1 = the other of the two channels */

static uint16_t Fifo_Available(const entropy_fifo_t *fifo)
{
    return (uint16_t)(fifo->count - fifo->head);
}

/* Slides unread bits to the front so a new block can be appended. */
static void Fifo_Compact(entropy_fifo_t *fifo)
{
    if (fifo->head > 0U) {
        uint16_t remaining = Fifo_Available(fifo);
        (void)memmove(fifo->bits, &fifo->bits[fifo->head], remaining);
        fifo->head = 0U;
        fifo->count = remaining;
    } else {
        /* No action */
    }
}

void Entropy_Pool_Init(entropy_pool_t *pool)
{
    uint8_t ch;

    for (ch = 0U; ch < ENTROPY_CHANNELS; ch++) {
        pool->fifo[ch].head = 0U;
        pool->fifo[ch].count = 0U;
        RNG_Health_Init(&pool->health[ch]);
    }
}

bool Entropy_Pool_Absorb(entropy_pool_t *pool, uint8_t channel,
                          const uint16_t *samples, uint16_t count)
{
    bool ok = (channel < ENTROPY_CHANNELS);
    uint16_t i;

    if (ok) {
        for (i = 0U; i < count; i++) {
            if (RNG_Health_Check(&pool->health[channel], samples[i]) != RNG_HEALTH_OK) {
                ok = false;
            } else {
                /* No action */
            }
        }
    } else {
        /* No action */
    }

    if (ok) {
        entropy_fifo_t *fifo = &pool->fifo[channel];

        Fifo_Compact(fifo);
        for (i = 0U; (uint16_t)(i + 1U) < count; i = (uint16_t)(i + VN_PAIR_LEN)) {
            uint16_t first = samples[i] & SAMPLE_LSB;
            uint16_t second = samples[i + 1U] & SAMPLE_LSB;

            if ((first != second) && (fifo->count < ENTROPY_FIFO_CAP)) {
                fifo->bits[fifo->count] = (uint8_t)first;
                fifo->count++;
            } else {
                /* No action */
            }
        }
    } else {
        /* No action */
    }

    return ok;
}

bool Entropy_Pool_TakeByte(entropy_pool_t *pool, uint8_t *out)
{
    return Entropy_Pool_TakeBits(pool, ENTROPY_MAX_DRAW_BITS, 0U, out);
}

bool Entropy_Pool_TakeBits(entropy_pool_t *pool, uint8_t nbits, uint8_t first_channel,
                           uint8_t *out)
{
    bool ok = (nbits >= 1U) && (nbits <= ENTROPY_MAX_DRAW_BITS) &&
              (first_channel < ENTROPY_CHANNELS);

    if (ok) {
        uint8_t second_channel = (uint8_t)(first_channel ^ CHANNEL_TOGGLE);
        /* The first channel serves bits 0, 2, 4, ... so it owes the extra one. */
        uint16_t need_first = (uint16_t)((nbits + 1U) / ENTROPY_CHANNELS);
        uint16_t need_second = (uint16_t)(nbits / ENTROPY_CHANNELS);

        ok = (Fifo_Available(&pool->fifo[first_channel]) >= need_first) &&
             (Fifo_Available(&pool->fifo[second_channel]) >= need_second);
    } else {
        /* No action */
    }

    if (ok) {
        uint8_t value = 0U;
        uint8_t i;

        for (i = 0U; i < nbits; i++) {
            entropy_fifo_t *fifo = &pool->fifo[(i & CHANNEL_TOGGLE) ^ first_channel];
            value = (uint8_t)((uint8_t)(value << 1U) | fifo->bits[fifo->head]);
            fifo->head++;
        }
        *out = value;
    } else {
        /* No action */
    }

    return ok;
}

uint8_t Entropy_Pool_DrawBits(uint16_t range, uint16_t *accept_below)
{
    uint8_t best_bits = ENTROPY_MAX_DRAW_BITS;
    uint32_t best_span = 0U;
    uint32_t best_accept = 0U;
    uint8_t bits;

    if ((range >= 1U) && (range <= (1U << ENTROPY_MAX_DRAW_BITS))) {
        for (bits = 1U; bits <= ENTROPY_MAX_DRAW_BITS; bits++) {
            uint32_t span = 1UL << bits;

            if (span >= range) {
                uint32_t accept = (span / range) * range;

                /* Compares bits*span/accept between candidates without
                 * division: at most 8 * 256 * 256, well inside 32 bits. */
                if ((best_accept == 0U) ||
                    (((uint32_t)bits * span * best_accept) < ((uint32_t)best_bits * best_span * accept))) {
                    best_bits = bits;
                    best_span = span;
                    best_accept = accept;
                } else {
                    /* No action */
                }
            } else {
                /* No action */
            }
        }
    } else {
        /* No action */
    }

    *accept_below = (uint16_t)best_accept;
    return best_bits;
}
