#ifndef EXTI_DRV_H
#define EXTI_DRV_H

/**
 * PA10 "panic" button. On the falling edge, the ISR calls the registered
 * callback immediately (before any debounce delay) so a wipe-sensitive-RAM
 * action never waits on software timing -- debouncing only stops the same
 * physical press from re-triggering the callback multiple times.
 */
typedef void (*exti_drv_panic_cb_t)(void);

void EXTI_Drv_ButtonInit(void);
void EXTI_Drv_SetPanicCallback(exti_drv_panic_cb_t cb);

#endif /* EXTI_DRV_H */
