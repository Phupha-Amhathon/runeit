#define STM32F411xE
#include "stm32f4xx.h"
#include "uid_drv.h"

void Uid_Drv_Read(uint32_t out[3])
{
    const volatile uint32_t *uid = (const volatile uint32_t *)UID_BASE;

    out[0] = uid[0];
    out[1] = uid[1];
    out[2] = uid[2];
}
