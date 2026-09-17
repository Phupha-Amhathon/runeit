#ifndef FLASH_DRV_H
#define FLASH_DRV_H

#include <stdint.h>

/** Erases one 16 KB sector (register-level sector index, e.g. 2 or 3). */
void Flash_Drv_EraseSector(uint8_t sector);

/** Programs size bytes at address, 32-bit word at a time (0xFF-padded tail). */
void Flash_Drv_Write(uint32_t address, const uint8_t *data, uint32_t size);

/** Flash is memory-mapped, so this is a plain copy out of address space. */
void Flash_Drv_Read(uint32_t address, uint8_t *data, uint32_t size);

#endif /* FLASH_DRV_H */
