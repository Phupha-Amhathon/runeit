#define STM32F411xE
#include "stm32f4xx.h"
#include <string.h>
#include "crc_drv.h"

#define CRC_WORD_BYTES 4U

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
    while ((i + CRC_WORD_BYTES) <= len) {
        uint32_t word;
        (void)memcpy(&word, data + i, CRC_WORD_BYTES);
        CRC->DR = word;
        i += CRC_WORD_BYTES;
    }
    if (i < len) {
        uint32_t word = 0U; /* zero-padded tail */
        (void)memcpy(&word, data + i, len - i);
        CRC->DR = word;
    } else {
        /* No action */
    }
}

uint32_t CRC_Drv_Result(void)
{
    return CRC->DR;
}

