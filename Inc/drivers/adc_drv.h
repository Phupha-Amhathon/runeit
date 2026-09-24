#ifndef ADC_DRV_H
#define ADC_DRV_H

#include <stdint.h>
#include <stdbool.h>

typedef enum {
    ADC_DRV_CH_TEMP = 0,   /* PA0, ADC1_IN0, NTC thermistor divider */
    ADC_DRV_CH_LIGHT = 1,  /* PA1, ADC1_IN1, LDR divider */
} adc_drv_channel_t;

/**
 * ADC1 on PA0 and PA1, sampled in blocks by DMA2 Stream0 / Channel 0 with a
 * transfer-complete interrupt (no EOC polling). One block is single-channel,
 * back-to-back conversions, which keeps the sample timing the entropy source
 * was characterised with (28-cycle sample time).
 */
void ADC_Drv_Init(void);

/**
 * Starts converting count samples of channel into buf. buf must stay valid
 * until ADC_Drv_BlockReady() returns true. Returns false if a block is still
 * in flight, count is 0, or the channel is invalid.
 */
bool ADC_Drv_StartBlock(adc_drv_channel_t channel, uint16_t *buf, uint16_t count);

/** True once the most recently started block has been fully written to buf. */
bool ADC_Drv_BlockReady(void);

#endif /* ADC_DRV_H */
