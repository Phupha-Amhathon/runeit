#define STM32F411xE
#include "stm32f4xx.h"
#include "adc_drv.h"

/* ADC1 <-> DMA2 request mapping on STM32F411: Stream0 / Channel 0 */
#define ADC_DMA_STREAM   DMA2_Stream0

/* Short on purpose: a long settle time suppresses the very noise being
 * harvested, but too short leaves back-to-back conversions correlated.
 * 0b010 = 28 cycles. */
#define ADC_SAMPLE_TIME_28  2U

static volatile bool s_block_ready = false;
static volatile bool s_block_busy = false;

void ADC_Drv_Init(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_DMA2EN;
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;

    /* PA0 and PA1 = analog mode */
    GPIOA->MODER |= (3U << (0U * 2U)) | (3U << (1U * 2U));

    ADC1->SMPR2 &= ~(ADC_SMPR2_SMP0 | ADC_SMPR2_SMP1);
    ADC1->SMPR2 |= (ADC_SAMPLE_TIME_28 << ADC_SMPR2_SMP0_Pos) |
                   (ADC_SAMPLE_TIME_28 << ADC_SMPR2_SMP1_Pos);
    ADC1->SQR1 &= ~ADC_SQR1_L; /* one conversion per sequence */
    ADC1->CR2 = ADC_CR2_ADON;

    /* Peripheral -> memory, 16-bit both sides, Channel 0 (CHSEL bits stay 0) */
    ADC_DMA_STREAM->CR = DMA_SxCR_MINC | DMA_SxCR_PSIZE_0 | DMA_SxCR_MSIZE_0 | DMA_SxCR_TCIE;
    ADC_DMA_STREAM->PAR = (uint32_t)&ADC1->DR;

    /* Below the panic button (0) and the UART path (1 and 2, see usart_drv.c). */
    NVIC_SetPriority(DMA2_Stream0_IRQn, 3);
    NVIC_EnableIRQ(DMA2_Stream0_IRQn);
}

bool ADC_Drv_StartBlock(adc_drv_channel_t channel, uint16_t *buf, uint16_t count)
{
    if (s_block_busy || (count == 0U) ||
        ((channel != ADC_DRV_CH_TEMP) && (channel != ADC_DRV_CH_LIGHT))) {
        return false;
    }

    s_block_busy = true;
    s_block_ready = false;

    ADC_DMA_STREAM->CR &= ~DMA_SxCR_EN;
    while ((ADC_DMA_STREAM->CR & DMA_SxCR_EN) != 0U) {
        /* wait for hardware to actually disable the stream */
    }
    DMA2->LIFCR = DMA_LIFCR_CTCIF0 | DMA_LIFCR_CHTIF0 | DMA_LIFCR_CTEIF0 |
                  DMA_LIFCR_CDMEIF0 | DMA_LIFCR_CFEIF0;

    ADC_DMA_STREAM->M0AR = (uint32_t)buf;
    ADC_DMA_STREAM->NDTR = count;
    ADC_DMA_STREAM->CR  |= DMA_SxCR_EN;

    ADC1->SQR3 = (ADC1->SQR3 & ~ADC_SQR3_SQ1) | ((uint32_t)channel << ADC_SQR3_SQ1_Pos);
    ADC1->SR = 0U;
    /* DDS keeps issuing DMA requests after every conversion, CONT keeps
     * converting until the transfer-complete ISR clears it. */
    ADC1->CR2 |= ADC_CR2_DMA | ADC_CR2_DDS | ADC_CR2_CONT;
    ADC1->CR2 |= ADC_CR2_SWSTART;
    return true;
}

bool ADC_Drv_BlockReady(void)
{
    return s_block_ready;
}

void DMA2_Stream0_IRQHandler(void)
{
    if ((DMA2->LISR & DMA_LISR_TCIF0) != 0U) {
        DMA2->LIFCR = DMA_LIFCR_CTCIF0;
        ADC1->CR2 &= ~(ADC_CR2_CONT | ADC_CR2_DMA | ADC_CR2_DDS);
        s_block_busy = false;
        s_block_ready = true;
    }
}
