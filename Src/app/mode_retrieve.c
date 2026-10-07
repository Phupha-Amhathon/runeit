#include "mode_retrieve.h"
#include "session.h"
#include "password_table.h"
#include "usart_drv.h"
#include "secure_zero.h"

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

bool Mode_Retrieve_Enter(void)
{
    s_sub = RETRIEVE_SUB_SHOW_TABLE;
    if (!Session_LoadTable(&s_table)) {
        Mode_Retrieve_Wipe();
        return false;
    }
    return true;
}

void Mode_Retrieve_Wipe(void)
{
    Secure_Zero(&s_table, sizeof(s_table));
}

/* Optional leading spaces, then 1-2 decimal digits and nothing else. Anything
 * else (letters, "0x5", "-1", a number too long to be an id) is rejected as
 * PWD_TABLE_MAX_ENTRIES, which the Show function reports as an invalid id. */
static uint32_t ParseId(const char *line)
{
    uint32_t value = 0U;
    uint32_t digits = 0U;

    while (*line == ' ') {
        line++;
    }
    while ((*line >= '0') && (*line <= '9')) {
        value = (value * 10U) + (uint32_t)(*line - '0');
        digits++;
        line++;
    }
    if ((digits == 0U) || (digits > 2U) || (*line != '\0')) {
        value = PWD_TABLE_MAX_ENTRIES;
    }
    return value;
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
                Password_Table_WipeScratch();
                return true; /* mode_op_done -> back to MODE_SELECTION */
            }
            Password_Table_ShowEntry(&s_table, ParseId(line));
            SendIdPrompt();
        }
        break;

    default:
        s_sub = RETRIEVE_SUB_SHOW_TABLE;
        break;
    }

    return false;
}
