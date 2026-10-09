#define STM32F411xE
#include "stm32f4xx.h"
#include <stddef.h>
#include "systick_drv.h"

#define HSI_CLOCK_HZ    16000000UL /* no PLL is configured */
#define SYSTICK_RATE_HZ 1000UL     /* 1 ms tick */

/*driver layer timer*/
static volatile uint32_t s_ms_ticks = 0U;
static systick_drv_tick_cb_t s_tick_cb = NULL;

/*ISR*/
void SysTick_Handler(void)
{
    s_ms_ticks++;
    if (s_tick_cb != NULL) {
        s_tick_cb();
    } else {
        /* No action */
    }
}

void SysTick_Drv_Init(void)
{
    /*Set amount of Clock cycle before Contex-m4 fire ISR, CMIS provided*/
    (void)SysTick_Config(HSI_CLOCK_HZ / SYSTICK_RATE_HZ);
}

void SysTick_Drv_SetTickCallback(systick_drv_tick_cb_t cb)
{
    s_tick_cb = cb;
}

uint32_t SysTick_Drv_Millis(void)
{
    return s_ms_ticks;
}

