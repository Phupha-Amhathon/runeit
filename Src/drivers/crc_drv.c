#define STM32F411xE
#include "stm32f4xx.h"
#include <string.h>
#include "crc_drv.h"

void CRC_Drv_Init(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_CRCEN;
}

void CRC_Drv_Reset(void)
{
    CRC->CR = CRC_CR_RESET;
}

void CRC_Drv_Feed(const uint8_t *data, uint32_t len)
{
    uint32_t i = 0U;
    while ((i + 4U) <= len) {
        uint32_t word;
        memcpy(&word, data + i, 4U);
        CRC->DR = word;
        i += 4U;
    }
    if (i < len) {
        uint32_t word = 0U; /* zero-padded tail */
        memcpy(&word, data + i, len - i);
        CRC->DR = word;
    }
}

uint32_t CRC_Drv_Result(void)
{
    return CRC->DR;
}

uint32_t CRC_Drv_Compute(const uint8_t *data, uint32_t len)
{
    CRC_Drv_Reset();
    CRC_Drv_Feed(data, len);
    return CRC_Drv_Result();
}
