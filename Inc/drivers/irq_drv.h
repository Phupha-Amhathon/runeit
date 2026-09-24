#ifndef IRQ_DRV_H
#define IRQ_DRV_H

#include <stdint.h>

/**
 * Masks all maskable interrupts and returns the previous mask state. Pass the
 * returned value to IRQ_Drv_Restore() to end the critical section. A masked
 * interrupt (such as the panic button) is not lost: it stays pending and runs
 * as soon as the mask is restored.
 */
uint32_t IRQ_Drv_Disable(void);

void IRQ_Drv_Restore(uint32_t previous_mask);

#endif /* IRQ_DRV_H */
