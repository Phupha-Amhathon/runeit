#include <string.h>
#include "entropy_pool.h"

#define BITS_PER_CHANNEL_PER_BYTE 4U

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
            }
        }
    }

    if (ok) {
        entropy_fifo_t *fifo = &pool->fifo[channel];

        Fifo_Compact(fifo);
        for (i = 0U; (uint16_t)(i + 1U) < count; i = (uint16_t)(i + 2U)) {
            uint16_t first = samples[i] & 0x1U;
            uint16_t second = samples[i + 1U] & 0x1U;

            if ((first != second) && (fifo->count < ENTROPY_FIFO_CAP)) {
                fifo->bits[fifo->count] = (uint8_t)first;
                fifo->count++;
            }
        }
    }

    return ok;
}

bool Entropy_Pool_TakeByte(entropy_pool_t *pool, uint8_t *out)
{
    bool ok = (Fifo_Available(&pool->fifo[0]) >= BITS_PER_CHANNEL_PER_BYTE) &&
              (Fifo_Available(&pool->fifo[1]) >= BITS_PER_CHANNEL_PER_BYTE);

    if (ok) {
        uint8_t value = 0U;
        uint8_t i;

        for (i = 0U; i < 8U; i++) {
            entropy_fifo_t *fifo = &pool->fifo[i & 0x1U];
            value = (uint8_t)((uint8_t)(value << 1) | fifo->bits[fifo->head]);
            fifo->head++;
        }
        *out = value;
    }

    return ok;
}
