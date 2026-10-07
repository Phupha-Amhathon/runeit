#ifndef LINE_BUF_H
#define LINE_BUF_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/**
 * Pure helpers (no registers) that turn the raw bytes the UART DMA collected
 * into one edited line. Kept apart from usart_drv.c so they can be tested on a PC.
 *
 * A line ends at the first CR or LF, so a terminal that sends one keystroke at
 * a time, or sends the text and its Enter as two writes, still produces one line.
 */

/** 1 if a lone LF (the second half of a CR LF pair that arrived separately)
 *  leads the buffer and swallow_lf is set, else 0. */
size_t LineBuf_Start(const uint8_t *buf, size_t len, bool swallow_lf);

/** Index of the first CR or LF in buf[start..len), or len if there is none. */
size_t LineBuf_FindEnd(const uint8_t *buf, size_t start, size_t len);

/** Copies buf[start..end) into dst, applying backspace (0x08) and DEL (0x7F)
 *  editing; NUL-terminates and keeps at most cap-1 characters. Returns the length. */
size_t LineBuf_Extract(const uint8_t *buf, size_t start, size_t end, char *dst, size_t cap);

#endif /* LINE_BUF_H */
