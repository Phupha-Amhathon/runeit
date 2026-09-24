#define STM32F411xE
#include "stm32f4xx.h"
#include "irq_drv.h"

uint32_t IRQ_Drv_Disable(void)
{
    uint32_t previous_mask = __get_PRIMASK();
    __disable_irq();
    return previous_mask;
}

void IRQ_Drv_Restore(uint32_t previous_mask)
{
    __set_PRIMASK(previous_mask);
}
