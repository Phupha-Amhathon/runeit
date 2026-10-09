#define STM32F411xE
#include "stm32f4xx.h"
#include "uid_drv.h"

void Uid_Drv_Read(uint32_t out[UID_DRV_WORDS])
{
    const volatile uint32_t *uid = (const volatile uint32_t *)UID_BASE;
    uint32_t i;

    for (i = 0U; i < UID_DRV_WORDS; i++) {
        out[i] = uid[i];
    }
}
