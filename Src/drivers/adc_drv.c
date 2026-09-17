#define STM32F411xE
#include "stm32f4xx.h"
#include "adc_drv.h"

void ADC_Drv_Init(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN;
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;

    /* PA0 = ADC1_IN0, analog mode */
    GPIOA->MODER |= (3U << (0U * 2U));

    ADC1->CR2 = 0U; /* not sampling yet -- see GENERATE_MODE follow-up */
}
