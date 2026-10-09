#define STM32F411xE
#include <stdbool.h>
#include <stddef.h>
#include "stm32f4xx.h"
#include "exti_drv.h"
#include "systick_drv.h"

#define BUTTON_GUARD_MS 200U
#define TILT_GUARD_MS   500U

/* SYSCFG->EXTICR[2] is EXTICR3, which selects the port for EXTI lines 8-11 */
#define EXTICR_LINES_8_11  2U

#define EXTI_PRIO_BUTTON   0U
#define EXTI_PRIO_TILT     1U

static exti_drv_panic_cb_t s_panic_cb = NULL;
static volatile uint32_t s_last_trigger_ms = 0U;
static volatile uint32_t s_last_tilt_ms = 0U;
static volatile bool s_tilt_seen = false;

void EXTI_Drv_ButtonInit(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN;
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;

    /* PA10 = input, internal pull-up (button to GND, active-low) */
    GPIOA->MODER &= ~GPIO_MODER_MODER10;
    GPIOA->PUPDR &= ~GPIO_PUPDR_PUPD10;
    GPIOA->PUPDR |=  GPIO_PUPDR_PUPD10_0;

    SYSCFG->EXTICR[EXTICR_LINES_8_11] &= ~SYSCFG_EXTICR3_EXTI10;
    SYSCFG->EXTICR[EXTICR_LINES_8_11] |=  SYSCFG_EXTICR3_EXTI10_PA;

    EXTI->FTSR |= EXTI_FTSR_TR10;
    EXTI->RTSR &= ~EXTI_RTSR_TR10;
    EXTI->IMR  |= EXTI_IMR_MR10;

    /* Highest priority (0) in this project: the panic wipe must be able to
     * preempt USART/DMA activity, never wait behind it. */
    NVIC_SetPriority(EXTI15_10_IRQn, EXTI_PRIO_BUTTON);
    NVIC_EnableIRQ(EXTI15_10_IRQn);
}

void EXTI_Drv_TiltInit(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOBEN;
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;

    /* PB9 = input with pull-up (tilt switch on the shield's AHT10 SDA pin).
     * It must stay an input because the shield's I2C bus shares this pin. */
    GPIOB->MODER &= ~GPIO_MODER_MODER9;
    GPIOB->PUPDR &= ~GPIO_PUPDR_PUPD9;
    GPIOB->PUPDR |=  GPIO_PUPDR_PUPD9_0;

    /* EXTICR[2] also holds the button's EXTI10 field, so only touch EXTI9 */
    SYSCFG->EXTICR[EXTICR_LINES_8_11] &= ~SYSCFG_EXTICR3_EXTI9;
    SYSCFG->EXTICR[EXTICR_LINES_8_11] |=  SYSCFG_EXTICR3_EXTI9_PB;

    /* Both edges: the upright level is not known, any change is movement. */
    EXTI->FTSR |= EXTI_FTSR_TR9;
    EXTI->RTSR |= EXTI_RTSR_TR9;
    EXTI->IMR  |= EXTI_IMR_MR9;

    /* Priority 1: below the button (0), equal to USART2 IDLE. */
    NVIC_SetPriority(EXTI9_5_IRQn, EXTI_PRIO_TILT);
    NVIC_EnableIRQ(EXTI9_5_IRQn);
}

void EXTI_Drv_SetPanicCallback(exti_drv_panic_cb_t cb)
{
    s_panic_cb = cb;
}

void EXTI15_10_IRQHandler(void)
{
    if ((EXTI->PR & EXTI_PR_PR10) != 0U) {
        EXTI->PR = EXTI_PR_PR10; /* write 1 to clear */

        uint32_t now = SysTick_Drv_Millis();
        if ((now - s_last_trigger_ms) >= BUTTON_GUARD_MS) {
            s_last_trigger_ms = now;
            if (s_panic_cb != NULL) {
                s_panic_cb();
            } else {
                /* No action */
            }
        } else {
            /* No action */
        }
    } else {
        /* No action */
    }
}

void EXTI9_5_IRQHandler(void)
{
    if ((EXTI->PR & EXTI_PR_PR9) != 0U) {
        EXTI->PR = EXTI_PR_PR9; /* write 1 to clear */

        /* The ball chatters, so one tilt gives a burst of edges. Guard it. */
        uint32_t now = SysTick_Drv_Millis();
        if ((!s_tilt_seen) || ((now - s_last_tilt_ms) >= TILT_GUARD_MS)) {
            s_tilt_seen = true;
            s_last_tilt_ms = now;
            if (s_panic_cb != NULL) {
                s_panic_cb();
            } else {
                /* No action */
            }
        } else {
            /* No action */
        }
    } else {
        /* No action */
    }
}
