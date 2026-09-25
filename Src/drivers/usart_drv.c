#define STM32F411xE
#include "stm32f4xx.h"
#include <string.h>
#include "usart_drv.h"
#include "secure_zero.h"
#include "line_buf.h"
#include "critical_drv.h"

/* USART2 <-> DMA1 request mapping on STM32F411: TX = Stream6/Ch4, RX = Stream5/Ch4 */
#define USART_DMA_TX_STREAM   DMA1_Stream6
#define USART_DMA_RX_STREAM   DMA1_Stream5
#define USART_DMA_CHANNEL4    (4UL << DMA_SxCR_CHSEL_Pos)

static volatile bool s_tx_ready = true;
static volatile bool s_rx_complete = false;

static uint8_t s_rx_buf[USART_DRV_RX_LINE_MAX];
static volatile uint16_t s_rx_len = 0U;
/* The previous line ended with a CR that was the last byte received, so an LF
 * arriving on its own next belongs to that line and must not start a new one. */
static volatile bool s_swallow_lf = false;

static void RxStream_Arm(void)
{
    USART_DMA_RX_STREAM->CR &= ~DMA_SxCR_EN;
    while ((USART_DMA_RX_STREAM->CR & DMA_SxCR_EN) != 0U) {
        /* wait for hardware to actually disable the stream */
    }
    DMA1->HIFCR = DMA_HIFCR_CTCIF5 | DMA_HIFCR_CHTIF5 | DMA_HIFCR_CTEIF5 |
                  DMA_HIFCR_CDMEIF5 | DMA_HIFCR_CFEIF5;

    USART_DMA_RX_STREAM->M0AR = (uint32_t)s_rx_buf;
    USART_DMA_RX_STREAM->NDTR = USART_DRV_RX_LINE_MAX;
    USART_DMA_RX_STREAM->CR  |= DMA_SxCR_EN;
}

void USART_Drv_Init(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_DMA1EN;
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;

    /* PA2 = TX, PA3 = RX, AF7 (USART2) */
    GPIOA->MODER &= ~((3U << (2U * 2U)) | (3U << (3U * 2U)));
    GPIOA->MODER |=  ((2U << (2U * 2U)) | (2U << (3U * 2U)));
    GPIOA->AFR[0] &= ~((0xFU << (2U * 4U)) | (0xFU << (3U * 4U)));
    GPIOA->AFR[0] |=  ((7U << (2U * 4U)) | (7U << (3U * 4U)));

    USART2->BRR = 0x008BU; /* 115200 @ 16 MHz HSI */
    USART2->CR3 = USART_CR3_DMAT | USART_CR3_DMAR;
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE | USART_CR1_IDLEIE;

    /* TX stream: memory -> USART2->DR */
    USART_DMA_TX_STREAM->CR = USART_DMA_CHANNEL4 | DMA_SxCR_DIR_0 |
                               DMA_SxCR_MINC | DMA_SxCR_TCIE;
    USART_DMA_TX_STREAM->PAR = (uint32_t)&USART2->DR;

    /* RX stream: USART2->DR -> memory (DIR = 00, no DIR bits set) */
    USART_DMA_RX_STREAM->CR = USART_DMA_CHANNEL4 | DMA_SxCR_MINC | DMA_SxCR_TCIE;
    USART_DMA_RX_STREAM->PAR = (uint32_t)&USART2->DR;
    RxStream_Arm();

    /* Below the panic button's priority (0, see exti_drv.c) so a button
     * press always preempts in-flight UART activity. */
    NVIC_SetPriority(DMA1_Stream6_IRQn, 2);
    NVIC_EnableIRQ(DMA1_Stream6_IRQn);
    NVIC_SetPriority(DMA1_Stream5_IRQn, 2);
    NVIC_EnableIRQ(DMA1_Stream5_IRQn);
    NVIC_SetPriority(USART2_IRQn, 1);
    NVIC_EnableIRQ(USART2_IRQn);
}

bool USART_Drv_Send(const uint8_t *buf, uint16_t len)
{
    if (!s_tx_ready || (len == 0U)) {
        return false;
    }

    s_tx_ready = false;

    USART_DMA_TX_STREAM->CR &= ~DMA_SxCR_EN;
    while ((USART_DMA_TX_STREAM->CR & DMA_SxCR_EN) != 0U) {
        /* wait for hardware to actually disable the stream */
    }
    DMA1->HIFCR = DMA_HIFCR_CTCIF6 | DMA_HIFCR_CHTIF6 | DMA_HIFCR_CTEIF6 |
                  DMA_HIFCR_CDMEIF6 | DMA_HIFCR_CFEIF6;

    USART_DMA_TX_STREAM->M0AR = (uint32_t)buf;
    USART_DMA_TX_STREAM->NDTR = len;
    USART_DMA_TX_STREAM->CR  |= DMA_SxCR_EN;
    return true;
}

bool USART_Drv_SendString(const char *str)
{
    return USART_Drv_Send((const uint8_t *)str, (uint16_t)strlen(str));
}

bool USART_Drv_TxReady(void)
{
    return s_tx_ready;
}

void USART_Drv_WaitTxReady(void)
{
    while (!s_tx_ready) {
        /* spin on the software flag set by the DMA TC interrupt */
    }
}

bool USART_Drv_RxComplete(void)
{
    return s_rx_complete;
}

uint16_t USART_Drv_TakeLine(char *dst, uint16_t dst_cap)
{
    size_t received = s_rx_len;
    size_t start = LineBuf_Start(s_rx_buf, received, s_swallow_lf);
    size_t end = LineBuf_FindEnd(s_rx_buf, start, received);
    uint16_t len = (uint16_t)LineBuf_Extract(s_rx_buf, start, end, dst, dst_cap);
    uint32_t saved;

    s_swallow_lf = (end < received) && (s_rx_buf[end] == (uint8_t)'\r') && ((end + 1U) == received);

    /* Re-arm and clear the flag as one step, so an interrupt cannot slip in
     * between and hand out the old line a second time. */
    saved = Critical_Enter();
    Secure_Zero(s_rx_buf, sizeof(s_rx_buf));
    s_rx_len = 0U;
    s_rx_complete = false;
    RxStream_Arm();
    Critical_Exit(saved);
    return len;
}

void USART_Drv_WipeRx(void)
{
    uint32_t saved = Critical_Enter();

    Secure_Zero(s_rx_buf, sizeof(s_rx_buf));
    s_rx_len = 0U;
    s_rx_complete = false;
    s_swallow_lf = false;
    RxStream_Arm();
    Critical_Exit(saved);
}

void DMA1_Stream6_IRQHandler(void)
{
    if ((DMA1->HISR & DMA_HISR_TCIF6) != 0U) {
        DMA1->HIFCR = DMA_HIFCR_CTCIF6;
        s_tx_ready = true;
    }
}

void DMA1_Stream5_IRQHandler(void)
{
    /* RX buffer filled completely without an IDLE gap (line too long):
     * treat whatever is buffered as the line, same as an IDLE completion. */
    if ((DMA1->HISR & DMA_HISR_TCIF5) != 0U) {
        DMA1->HIFCR = DMA_HIFCR_CTCIF5;
        s_rx_len = USART_DRV_RX_LINE_MAX;
        s_rx_complete = true;
    }
}

void USART2_IRQHandler(void)
{
    if ((USART2->SR & USART_SR_IDLE) != 0U) {
        size_t received;
        size_t start;

        (void)USART2->SR;
        (void)USART2->DR; /* required sequence to clear the IDLE flag */

        /* The line is only finished by Enter (CR/LF). A pause without one
         * (typing one key at a time, or the text and its Enter sent as two
         * writes) leaves the DMA running and keeps collecting. */
        received = (size_t)USART_DRV_RX_LINE_MAX - (size_t)USART_DMA_RX_STREAM->NDTR;
        start = LineBuf_Start(s_rx_buf, received, s_swallow_lf);
        if (LineBuf_FindEnd(s_rx_buf, start, received) < received) {
            USART_DMA_RX_STREAM->CR &= ~DMA_SxCR_EN;
            while ((USART_DMA_RX_STREAM->CR & DMA_SxCR_EN) != 0U) {
                /* wait for hardware to actually disable the stream */
            }
            s_rx_len = (uint16_t)(USART_DRV_RX_LINE_MAX - (uint16_t)USART_DMA_RX_STREAM->NDTR);
            s_rx_complete = true;
        }
    }
}
