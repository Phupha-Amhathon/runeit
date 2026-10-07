#define STM32F411xE
#include "stm32f4xx.h"
#include "critical_drv.h"

uint32_t Critical_Enter(void)
{
    uint32_t primask = __get_PRIMASK();

    __disable_irq();
    return primask;
}

void Critical_Exit(uint32_t saved)
{
    __set_PRIMASK(saved);
}
