#define STM32F411xE
#include "stm32f4xx.h"
#include "led_drv.h"

void Led_Drv_Init(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN;

    GPIOA->BSRR = GPIO_BSRR_BR5; /* off before the pin starts driving */
    GPIOA->OTYPER &= ~GPIO_OTYPER_OT5;
    GPIOA->MODER &= ~GPIO_MODER_MODER5;
    GPIOA->MODER |= GPIO_MODER_MODER5_0;
}

/* BSRR, not ODR: a read-modify-write of ODR interrupted by the panic ISR
 * could undo the ISR's write. */
void Led_Drv_Set(bool on)
{
    GPIOA->BSRR = on ? GPIO_BSRR_BS5 : GPIO_BSRR_BR5;
}
