#include <string.h>
#include <stdio.h>
#include "password_table.h"
#include "usart_drv.h"

/* Built up with snprintf (pure formatting, no I/O) and sent as one DMA
 * transfer so a multi-line listing is a single atomic USART_Drv_Send(). */
#define LINE_BUF_LEN 1024U
static char s_line_buf[LINE_BUF_LEN];

void Password_Table_InitEmpty(pwd_table_t *table)
{
    memset(table, 0, sizeof(*table));
}

bool Password_Table_EntryIsUsed(const pwd_entry_t *entry)
{
    return entry->name[0] != '\0';
}

void Password_Table_ShowEntries(const pwd_table_t *table)
{
    int written = snprintf(s_line_buf, LINE_BUF_LEN, "\r\n-- Password table --\r\n");
    size_t pos = (written > 0) ? (size_t)written : 0U;
    bool any = false;

    for (uint32_t id = 0U; id < PWD_TABLE_MAX_ENTRIES; id++) {
        const pwd_entry_t *entry = &table->entries[id];
        if (Password_Table_EntryIsUsed(entry) && (pos < LINE_BUF_LEN)) {
            written = snprintf(s_line_buf + pos, LINE_BUF_LEN - pos,
                                "  %2lu - %s\r\n", (unsigned long)id, entry->name);
            if (written > 0) {
                pos += (size_t)written;
            }
            any = true;
        }
    }
    if (!any && (pos < LINE_BUF_LEN)) {
        written = snprintf(s_line_buf + pos, LINE_BUF_LEN - pos, "  (empty)\r\n");
        if (written > 0) {
            pos += (size_t)written;
        }
    }

    USART_Drv_WaitTxReady();
    (void)USART_Drv_Send((const uint8_t *)s_line_buf, (uint16_t)pos);
}

void Password_Table_ShowEntry(const pwd_table_t *table, uint32_t id)
{
    int written;

    if (id >= PWD_TABLE_MAX_ENTRIES) {
        written = snprintf(s_line_buf, LINE_BUF_LEN, "\r\nInvalid id.\r\n");
    } else if (!Password_Table_EntryIsUsed(&table->entries[id])) {
        written = snprintf(s_line_buf, LINE_BUF_LEN, "\r\n(no entry at that id)\r\n");
    } else {
        const pwd_entry_t *entry = &table->entries[id];
        written = snprintf(s_line_buf, LINE_BUF_LEN, "\r\n%s : %s\r\n", entry->name, entry->password);
    }

    USART_Drv_WaitTxReady();
    (void)USART_Drv_Send((const uint8_t *)s_line_buf, (uint16_t)((written > 0) ? written : 0));
}
