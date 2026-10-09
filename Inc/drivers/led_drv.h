#ifndef LED_DRV_H
#define LED_DRV_H

#include <stdbool.h>

/** The four LEDs on the NEXTY training shield. */
typedef enum {
    LED_DRV_BLUE = 0,   /* D13, PA5 */
    LED_DRV_RED,        /* D12, PA6 */
    LED_DRV_YELLOW,     /* D11, PA7 */
    LED_DRV_GREEN,      /* D10, PB6 */
    LED_DRV_COUNT
} led_drv_id_t;

/** All four as push-pull outputs, starting off. */
void Led_Drv_Init(void);

/** Single BSRR write, so it is safe from both the main loop and an ISR. */
void Led_Drv_Set(led_drv_id_t led, bool on);

void Led_Drv_AllOff(void);

#endif /* LED_DRV_H */
