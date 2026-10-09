#ifndef UID_DRV_H
#define UID_DRV_H

#include <stdint.h>

#define UID_DRV_WORDS 3U /* 96 bits */

/** Reads the factory-programmed 96-bit unique device ID as three words. */
void Uid_Drv_Read(uint32_t out[UID_DRV_WORDS]);

#endif /* UID_DRV_H */
