#define STM32F411xE
#include "stm32f4xx.h"
#include <string.h>
#include "flash_drv.h"

static void Flash_Unlock(void)
{
    if ((FLASH->CR & FLASH_CR_LOCK) != 0U) {
        FLASH->KEYR = 0x45670123U;
        FLASH->KEYR = 0xCDEF89ABU;
    }
}

static void Flash_Lock(void)
{
    FLASH->CR |= FLASH_CR_LOCK;
}

void Flash_Drv_EraseSector(uint8_t sector)
{
    Flash_Unlock();
    while ((FLASH->SR & FLASH_SR_BSY) != 0U) { }

    FLASH->CR &= ~(FLASH_CR_SNB | FLASH_CR_PSIZE);
    FLASH->CR |= (0x02U << FLASH_CR_PSIZE_Pos) | ((uint32_t)sector << FLASH_CR_SNB_Pos) | FLASH_CR_SER;
    FLASH->CR |= FLASH_CR_STRT;
    while ((FLASH->SR & FLASH_SR_BSY) != 0U) { }
    FLASH->CR &= ~FLASH_CR_SER;

    Flash_Lock();
}

void Flash_Drv_Write(uint32_t address, const uint8_t *data, uint32_t size)
{
    Flash_Unlock();
    while ((FLASH->SR & FLASH_SR_BSY) != 0U) { }

    for (uint32_t i = 0U; i < size; i += 4U) {
        uint32_t word = 0xFFFFFFFFU;
        uint32_t chunk = ((size - i) >= 4U) ? 4U : (size - i);
        memcpy(&word, data + i, chunk);

        FLASH->CR &= ~FLASH_CR_PSIZE;
        FLASH->CR |= (0x02U << FLASH_CR_PSIZE_Pos) | FLASH_CR_PG;

        *(volatile uint32_t *)(address + i) = word;
        while ((FLASH->SR & FLASH_SR_BSY) != 0U) { }

        FLASH->CR &= ~FLASH_CR_PG;
    }

    Flash_Lock();
}

void Flash_Drv_Read(uint32_t address, uint8_t *data, uint32_t size)
{
    memcpy(data, (const void *)address, size);
}
