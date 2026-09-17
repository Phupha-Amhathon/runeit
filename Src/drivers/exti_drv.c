#define STM32F411xE
#include "stm32f4xx.h"
#include "exti_drv.h"
#include "systick_drv.h"

#define BUTTON_GUARD_MS 200U

static exti_drv_panic_cb_t s_panic_cb = 0;
static volatile uint32_t s_last_trigger_ms = 0U;

void EXTI_Drv_ButtonInit(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN;
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;

    /* PA10 = input, internal pUull-up (button to GND, active-low) */
    GPIOA->MODER &= ~(3U << (10 * 2U));
    GPIOA->PUPDR &= ~(3U << (10U * 2U));
    GPIOA->PUPDR |=  (1U << (10U * 2U));

    SYSCFG->EXTICR[2] &= ~SYSCFG_EXTICR3_EXTI10;
    SYSCFG->EXTICR[2] |=  SYSCFG_EXTICR3_EXTI10_PA;

    EXTI->FTSR |= EXTI_FTSR_TR10;
    EXTI->RTSR &= ~EXTI_RTSR_TR10;
    EXTI->IMR  |= EXTI_IMR_MR10;

    /* Highest priority (0) in this project: the panic wipe must be able to
     * preempt USART/DMA activity, never wait behind it. */
    NVIC_SetPriority(EXTI15_10_IRQn, 0);
    NVIC_EnableIRQ(EXTI15_10_IRQn);
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
            if (s_panic_cb != 0) {
                s_panic_cb();
            }
        }
    }
}
