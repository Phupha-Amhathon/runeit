#include "line_buf.h"

#define ASCII_BS  0x08U
#define ASCII_DEL 0x7FU

size_t LineBuf_Start(const uint8_t *buf, size_t len, bool swallow_lf)
{
    size_t start = 0U;

    if (swallow_lf && (len > 0U) && (buf[0] == (uint8_t)'\n')) {
        start = 1U;
    } else {
        /* No action */
    }
    return start;
}

size_t LineBuf_FindEnd(const uint8_t *buf, size_t start, size_t len)
{
    size_t i = start;

    while ((i < len) && (buf[i] != (uint8_t)'\r') && (buf[i] != (uint8_t)'\n')) {
        i++;
    }
    return i;
}

size_t LineBuf_Extract(const uint8_t *buf, size_t start, size_t end, char *dst, size_t cap)
{
    size_t total = 0U;
    size_t i;
    size_t produced;

    if (cap == 0U) {
        return 0U;
    } else {
        /* No action */
    }

    for (i = start; i < end; i++) {
        if ((buf[i] == ASCII_BS) || (buf[i] == ASCII_DEL)) {
            if (total > 0U) {
                total--;
            } else {
                /* No action */
            }
        } else {
            if (total < (cap - 1U)) {
                dst[total] = (char)buf[i];
            } else {
                /* No action */
            }
            total++;
        }
    }

    if (total < (cap - 1U)) {
        produced = total;
    } else {
        produced = cap - 1U;
    }
    dst[produced] = '\0';
    return produced;
}
