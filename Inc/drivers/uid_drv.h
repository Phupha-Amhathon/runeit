#ifndef UID_DRV_H
#define UID_DRV_H

#include <stdint.h>

/** Reads the factory-programmed 96-bit unique device ID as three words. */
void Uid_Drv_Read(uint32_t out[3]);

#endif /* UID_DRV_H */
