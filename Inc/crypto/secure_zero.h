#ifndef SECURE_ZERO_H
#define SECURE_ZERO_H

#include <stddef.h>
#include <stdint.h>

/* Volatile writes cannot be optimised away, unlike memset() on a buffer
 * that is never read again (exactly the situation for a dying key). */
static inline void Secure_Zero(void *ptr, size_t len)
{
    volatile uint8_t *p = (volatile uint8_t *)ptr;
    while (len > 0U) {
        *p = 0U;
        p++;
        len--;
    }
}

#endif /* SECURE_ZERO_H */
