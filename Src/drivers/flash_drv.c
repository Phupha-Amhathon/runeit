#define STM32F411xE
#include "stm32f4xx.h"
#include <string.h>
#include "flash_drv.h"

/* Unlock sequence from the reference manual; the device header has no names for it */
#define FLASH_UNLOCK_KEY1   0x45670123U
#define FLASH_UNLOCK_KEY2   0xCDEF89ABU
#define FLASH_PSIZE_X32     FLASH_CR_PSIZE_1 /* program and erase 32 bits at a time */
#define FLASH_WORD_BYTES    4U
#define FLASH_ERASED_WORD   0xFFFFFFFFU

static void Flash_Unlock(void)
{
    if ((FLASH->CR & FLASH_CR_LOCK) != 0U) {
        FLASH->KEYR = FLASH_UNLOCK_KEY1;
        FLASH->KEYR = FLASH_UNLOCK_KEY2;
    } else {
        /* No action */
    }
}

static void Flash_Lock(void)
{
    FLASH->CR |= FLASH_CR_LOCK;
}

void Flash_Drv_EraseSector(uint8_t sector)
{
    Flash_Unlock();
    while ((FLASH->SR & FLASH_SR_BSY) != 0U) {
        /* wait for the flash controller to go idle */
    }

    FLASH->CR &= ~(FLASH_CR_SNB | FLASH_CR_PSIZE);
    FLASH->CR |= FLASH_PSIZE_X32 | ((uint32_t)sector << FLASH_CR_SNB_Pos) | FLASH_CR_SER;
    FLASH->CR |= FLASH_CR_STRT;
    while ((FLASH->SR & FLASH_SR_BSY) != 0U) {
        /* wait for the sector erase to finish */
    }
    FLASH->CR &= ~FLASH_CR_SER;

    Flash_Lock();
}

void Flash_Drv_Write(uint32_t address, const uint8_t *data, uint32_t size)
{
    Flash_Unlock();
    while ((FLASH->SR & FLASH_SR_BSY) != 0U) {
        /* wait for the flash controller to go idle */
    }

    for (uint32_t i = 0U; i < size; i += FLASH_WORD_BYTES) {
        uint32_t word = FLASH_ERASED_WORD;
        uint32_t chunk;

        if ((size - i) >= FLASH_WORD_BYTES) {
            chunk = FLASH_WORD_BYTES;
        } else {
            chunk = size - i;
        }
        (void)memcpy(&word, data + i, chunk);

        FLASH->CR &= ~FLASH_CR_PSIZE;
        FLASH->CR |= FLASH_PSIZE_X32 | FLASH_CR_PG;

        *(volatile uint32_t *)(address + i) = word;
        while ((FLASH->SR & FLASH_SR_BSY) != 0U) {
            /* wait for this word to be programmed */
        }

        FLASH->CR &= ~FLASH_CR_PG;
    }

    Flash_Lock();
}

void Flash_Drv_Read(uint32_t address, uint8_t *data, uint32_t size)
{
    (void)memcpy(data, (const void *)address, size);
}
