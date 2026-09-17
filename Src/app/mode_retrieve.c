#include <stdlib.h>
#include <string.h>
#include "mode_retrieve.h"
#include "partition_store.h"
#include "password_table.h"
#include "usart_drv.h"

typedef enum {
    RETRIEVE_SUB_SHOW_TABLE = 0,
    RETRIEVE_SUB_ID_WAIT,
} retrieve_sub_state_t;

static pwd_table_t s_table;
static retrieve_sub_state_t s_sub = RETRIEVE_SUB_SHOW_TABLE;

static void SendIdPrompt(void)
{
    USART_Drv_WaitTxReady();
    (void)USART_Drv_SendString("\r\nEnter id to view (0-30), or 'q' to go back: ");
}

void Mode_Retrieve_Enter(const uint8_t *input_mk, uint32_t mk_len)
{
    (void)Partition_Store_Load(input_mk, mk_len, &s_table);
    s_sub = RETRIEVE_SUB_SHOW_TABLE;
}

void Mode_Retrieve_Wipe(void)
{
    memset(&s_table, 0, sizeof(s_table));
}

bool Mode_Retrieve_Run(void)
{
    char line[32];

    switch (s_sub) {
    case RETRIEVE_SUB_SHOW_TABLE:
        Password_Table_ShowEntries(&s_table);
        SendIdPrompt();
        s_sub = RETRIEVE_SUB_ID_WAIT;
        break;

    case RETRIEVE_SUB_ID_WAIT:
        if (USART_Drv_RxComplete()) {
            (void)USART_Drv_TakeLine(line, sizeof(line));
            if ((line[0] == 'q') || (line[0] == 'Q') || (line[0] == '\0')) {
                Mode_Retrieve_Wipe();
                return true; /* mode_op_done -> back to MODE_SELECTION */
            }
            uint32_t id = (uint32_t)strtoul(line, NULL, 10);
            Password_Table_ShowEntry(&s_table, id);
            SendIdPrompt();
        }
        break;

    default:
        s_sub = RETRIEVE_SUB_SHOW_TABLE;
        break;
    }

    return false;
}
