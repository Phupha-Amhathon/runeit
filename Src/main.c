/**
 * @file    main.c
 * @brief   RUNEIT (Pocket Password) entry point.
 */
#define STM32F411xE
#include "stm32f4xx.h"

#include "systick_drv.h"
#include "exti_drv.h"
#include "usart_drv.h"
#include "crc_drv.h"
#include "adc_drv.h"
#include "app.h"

#if !defined(__SOFT_FP__) && defined(__ARM_FP)
  #warning "FPU is not initialized, but the project is compiling for an FPU. Please initialize the FPU before use."
#endif

int main(void)
{
    SysTick_Drv_Init();
    EXTI_Drv_ButtonInit();
    USART_Drv_Init();
    CRC_Drv_Init();
    ADC_Drv_Init();

    App_Init();
    App_Run(); /* never returns */

    for (;;) {
    }
}
