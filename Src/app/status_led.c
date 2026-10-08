#include <stdint.h>
#include <stdbool.h>
#include "status_led.h"
#include "led_drv.h"
#include "systick_drv.h"
#include "critical_drv.h"

#define STATUS_LED_STEP_MS 150U

static const led_drv_id_t s_chase[] = { LED_DRV_BLUE, LED_DRV_RED, LED_DRV_YELLOW };
#define STATUS_LED_CHASE_LEN ((uint32_t)(sizeof(s_chase) / sizeof(s_chase[0])))

static volatile status_led_mode_t s_mode = STATUS_LED_LOCKED;
/* Touched only by Tick(), so they need no protection. */
static uint32_t s_ms = 0U;
static uint32_t s_step = 0U;
static bool s_lit = false;

/* SysTick ISR, every 1 ms. Outside BUSY it keeps the chase LED off, which also
 * undoes a write that the panic ISR preempted halfway through. */
static void Tick(void)
{
    if (s_mode != STATUS_LED_BUSY) {
        if (s_lit) {
            Led_Drv_Set(s_chase[s_step], false);
            s_lit = false;
        }
        s_ms = 0U;
    } else if (!s_lit) {
        Led_Drv_Set(s_chase[s_step], true);
        s_lit = true;
        s_ms = 0U;
    } else {
        s_ms++;
        if (s_ms >= STATUS_LED_STEP_MS) {
            s_ms = 0U;
            Led_Drv_Set(s_chase[s_step], false);
            s_step = (s_step + 1U) % STATUS_LED_CHASE_LEN;
            Led_Drv_Set(s_chase[s_step], true);
        }
    }
}

void Status_Led_Init(void)
{
    SysTick_Drv_SetTickCallback(Tick);
}

status_led_mode_t Status_Led_Show(status_led_mode_t mode)
{
    status_led_mode_t prev = s_mode;

    /* Mode first: once it is not BUSY, Tick() can no longer light the chase,
     * so the LEDs switched off below stay off. */
    s_mode = mode;
    switch (mode) {
    case STATUS_LED_IDLE:
    case STATUS_LED_BUSY:
        Led_Drv_Set(LED_DRV_GREEN, true);
        break;
    case STATUS_LED_LOCKED:
    case STATUS_LED_QUIET:
    default:
        Led_Drv_AllOff();
        break;
    }
    return prev;
}

void Status_Led_Restore(status_led_mode_t prev)
{
    uint32_t saved = Critical_Enter();

    if (s_mode == STATUS_LED_QUIET) {
        (void)Status_Led_Show(prev);
    }
    Critical_Exit(saved);
}

void Status_Led_Off(void)
{
    (void)Status_Led_Show(STATUS_LED_LOCKED);
}
