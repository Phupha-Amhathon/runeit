#define STM32F411xE
#include "stm32f4xx.h"
#include "systick_drv.h"

/*driver layer timer*/
static volatile uint32_t s_ms_ticks = 0U;

/*ISR*/
void SysTick_Handler(void)
{
    s_ms_ticks++;
}

void SysTick_Drv_Init(void)
{
    /*Set amount of Clock cycle before Contex-m4 fire ISR, CMIS provided*/
    (void)SysTick_Config(16000000UL / 1000UL); /* HSI 16 MHz, 1 ms tick */
}

uint32_t SysTick_Drv_Millis(void)
{
    return s_ms_ticks;
}

void SysTick_Drv_DelayMs(uint32_t ms)
{
    uint32_t start = s_ms_ticks;
    while ((s_ms_ticks - start) < ms) {
        __NOP();
    }
}
