#ifndef LED_DRV_H
#define LED_DRV_H

#include <stdbool.h>

/** PA5 (Nucleo LD2) as a push-pull output, starting off. */
void Led_Drv_Init(void);

/** Single BSRR write, so it is safe from both the main loop and an ISR. */
void Led_Drv_Set(bool on);

#endif /* LED_DRV_H */
