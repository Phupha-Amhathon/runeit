#include "entropy_source.h"
#include "adc_drv.h"
#include "entropy_pool.h"
#include "secure_zero.h"
#include "systick_drv.h"

#define ES_BLOCK_TIMEOUT_MS  200U
#define ES_MAX_ROUNDS        40U

static entropy_pool_t s_pool;
static uint16_t s_samples[ENTROPY_BLOCK_SAMPLES];

/* Runs one channel's block to completion. Blocking on purpose: the only
 * caller is already making the user wait (key derivation). */
static bool RunBlock(adc_drv_channel_t channel, uint8_t pool_channel)
{
    uint32_t started = SysTick_Drv_Millis();

    if (!ADC_Drv_StartBlock(channel, s_samples, ENTROPY_BLOCK_SAMPLES)) {
        return false;
    }
    while (!ADC_Drv_BlockReady()) {
        if ((SysTick_Drv_Millis() - started) > ES_BLOCK_TIMEOUT_MS) {
            return false;
        }
    }
    return Entropy_Pool_Absorb(&s_pool, pool_channel, s_samples, ENTROPY_BLOCK_SAMPLES);
}

bool Entropy_Source_GetBytes(uint8_t *out, size_t len)
{
    size_t produced = 0U;
    uint32_t rounds = 0U;
    bool ok = true;

    Entropy_Pool_Init(&s_pool);

    while (ok && (produced < len)) {
        uint8_t value;

        while ((produced < len) && Entropy_Pool_TakeByte(&s_pool, &value)) {
            out[produced] = value;
            produced++;
        }
        if (produced < len) {
            if (rounds >= ES_MAX_ROUNDS) {
                ok = false;
            } else {
                rounds++;
                ok = RunBlock(ADC_DRV_CH_TEMP, 0U) && RunBlock(ADC_DRV_CH_LIGHT, 1U);
            }
        }
    }

    Secure_Zero(s_samples, sizeof(s_samples));
    Secure_Zero(&s_pool, sizeof(s_pool));
    return ok;
}
