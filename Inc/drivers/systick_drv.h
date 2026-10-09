#ifndef SYSTICK_DRV_H
#define SYSTICK_DRV_H

#include <stdint.h>

/** Called from the 1 ms SysTick interrupt; keep it short. */
typedef void (*systick_drv_tick_cb_t)(void);

/** Starts a 1 ms SysTick interrupt (HSI 16 MHz assumed, no PLL configured). */
void SysTick_Drv_Init(void);

/** Milliseconds since SysTick_Drv_Init(), wraps every ~49.7 days. */
uint32_t SysTick_Drv_Millis(void);

void SysTick_Drv_SetTickCallback(systick_drv_tick_cb_t cb);

#endif /* SYSTICK_DRV_H */
