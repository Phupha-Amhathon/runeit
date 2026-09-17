#ifndef CRC_DRV_H
#define CRC_DRV_H

#include <stdint.h>

void CRC_Drv_Init(void);

/** Starts a new running CRC (call once before one or more CRC_Drv_Feed() calls). */
void CRC_Drv_Reset(void);

/** Feeds len bytes into the running CRC (each call's tail is zero-padded to a word). */
void CRC_Drv_Feed(const uint8_t *data, uint32_t len);

/** Current running CRC value. */
uint32_t CRC_Drv_Result(void);

/** Convenience one-shot: CRC_Drv_Reset() + CRC_Drv_Feed() + CRC_Drv_Result(). */
uint32_t CRC_Drv_Compute(const uint8_t *data, uint32_t len);

#endif /* CRC_DRV_H */
