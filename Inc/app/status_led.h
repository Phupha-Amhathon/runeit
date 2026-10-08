#ifndef STATUS_LED_H
#define STATUS_LED_H

/**
 * The shield LEDs as a status display. The chase is stepped from the SysTick
 * interrupt, so it keeps moving through a key derivation that blocks the main
 * loop for seconds.
 */
typedef enum {
    STATUS_LED_LOCKED = 0,  /* all off: no session                              */
    STATUS_LED_IDLE,        /* green: session open                              */
    STATUS_LED_BUSY,        /* green + blue/red/yellow chase: a mode that writes */
    STATUS_LED_QUIET,       /* all off: the ADC is sampling noise, LED switching
                               would leak a regular pattern into it            */
} status_led_mode_t;

void Status_Led_Init(void);

/** Switches the display and returns the mode it replaced. QUIET and LOCKED
 *  turn every LED off before returning. */
status_led_mode_t Status_Led_Show(status_led_mode_t mode);

/** Ends a QUIET period by going back to prev, unless something else (the
 *  panic button) changed the display in the meantime. */
void Status_Led_Restore(status_led_mode_t prev);

/** Panic path, interrupt-safe: everything off, display LOCKED. */
void Status_Led_Off(void);

#endif /* STATUS_LED_H */
