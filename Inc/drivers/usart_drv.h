#ifndef USART_DRV_H
#define USART_DRV_H

#include <stdint.h>
#include <stdbool.h>

/** Max bytes of one received line, including the NUL terminator. */
#define USART_DRV_RX_LINE_MAX 64U

/**
 * USART2 (PA2=TX, PA3=RX), 115200 8N1, entirely interrupt/DMA-driven:
 * - TX: DMA1 Stream6 (mem -> USART2->DR), completion flagged by the
 *   stream's transfer-complete interrupt (no TXE polling).
 * - RX: DMA1 Stream5 (USART2->DR -> mem). Bytes are collected by DMA; the
 *   USART IDLE-line interrupt (no RXNE polling, no software timeout timer)
 *   checks after each pause whether an Enter (CR or LF) has arrived, and only
 *   then is the line complete. The terminal must therefore send CR, LF or CR+LF
 *   at the end of each line; typing one key at a time works, and backspace
 *   edits the line.
 */
void USART_Drv_Init(void);

/** Queues a DMA transfer of len bytes. Returns false if TX is still busy. */
bool USART_Drv_Send(const uint8_t *buf, uint16_t len);

/** Convenience wrapper around USART_Drv_Send() for a NUL-terminated string. */
bool USART_Drv_SendString(const char *str);

/** True once the most recently queued TX has completed. */
bool USART_Drv_TxReady(void);

/** Blocks (spins on the software tx_ready flag, not a UART register) until
 * any in-flight TX finishes. Safe to call before USART_Drv_Send(). */
void USART_Drv_WaitTxReady(void);

/** True once a full line has been received and is waiting to be read. */
bool USART_Drv_RxComplete(void);

/**
 * Copies the completed line (CR/LF stripped, NUL-terminated) into dst
 * (capacity dst_cap bytes), zeroes the driver's own copy (it may have been a
 * master key) and re-arms RX for the next line. Returns the
 * copied length (excluding the NUL). Must only be called after
 * USART_Drv_RxComplete() returns true.
 */
uint16_t USART_Drv_TakeLine(char *dst, uint16_t dst_cap);

/** Zeroes the receive buffer and re-arms RX, dropping any partial or pending
 *  line. Interrupt-safe (used by the panic button). */
void USART_Drv_WipeRx(void);

#endif /* USART_DRV_H */
