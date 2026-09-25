#ifndef CRITICAL_DRV_H
#define CRITICAL_DRV_H

#include <stdint.h>

/** Disables interrupts and returns the previous state for Critical_Exit(). */
uint32_t Critical_Enter(void);

/** Restores the interrupt state saved by Critical_Enter(). */
void Critical_Exit(uint32_t saved);

#endif /* CRITICAL_DRV_H */
