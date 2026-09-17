#ifndef ADC_DRV_H
#define ADC_DRV_H

/**
 * Peripheral init only for now. Real sampling (interrupt/DMA-driven, no
 * polling) lands with GENERATE_MODE, which is out of scope for this round.
 */
void ADC_Drv_Init(void);

#endif /* ADC_DRV_H */
