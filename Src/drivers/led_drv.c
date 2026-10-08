#define STM32F411xE
#include "stm32f4xx.h"
#include "led_drv.h"

typedef struct {
    GPIO_TypeDef *port;
    uint32_t      pin;
} led_pin_t;

static const led_pin_t s_leds[LED_DRV_COUNT] = {
    { GPIOA, 5U },
    { GPIOA, 6U },
    { GPIOA, 7U },
    { GPIOB, 6U },
};

void Led_Drv_Init(void)
{
    uint32_t i;

    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN;

    for (i = 0U; i < (uint32_t)LED_DRV_COUNT; i++) {
        GPIO_TypeDef *port = s_leds[i].port;
        uint32_t pin = s_leds[i].pin;

        port->BSRR = 1UL << (pin + GPIO_BSRR_BR0_Pos); /* off before the pin starts driving */
        port->OTYPER &= ~(GPIO_OTYPER_OT0 << pin);
        port->MODER &= ~(GPIO_MODER_MODER0 << (pin * 2U));
        port->MODER |= GPIO_MODER_MODER0_0 << (pin * 2U);
    }
}

/* BSRR, not ODR: a read-modify-write of ODR interrupted by an ISR that also
 * drives an LED could undo that ISR's write. */
void Led_Drv_Set(led_drv_id_t led, bool on)
{
    uint32_t i = (uint32_t)led;

    if (i < (uint32_t)LED_DRV_COUNT) {
        uint32_t pin = s_leds[i].pin;
        s_leds[i].port->BSRR = on ? (1UL << pin) : (1UL << (pin + GPIO_BSRR_BR0_Pos));
    }
}

void Led_Drv_AllOff(void)
{
    uint32_t i;

    for (i = 0U; i < (uint32_t)LED_DRV_COUNT; i++) {
        Led_Drv_Set((led_drv_id_t)i, false);
    }
}
