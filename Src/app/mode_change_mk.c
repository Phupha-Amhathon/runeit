#include <string.h>
#include "mode_change_mk.h"
#include "session.h"
#include "usart_drv.h"
#include "kdf.h"
#include "password_table.h"
#include "secure_zero.h"

typedef enum {
    CM_PROMPT = 0,
    CM_WAIT_NEW,
    CM_WAIT_CONFIRM,
} cm_state_t;

#define LINE_CAP (MK_MAX_LEN + 2U)

static cm_state_t s_sub = CM_PROMPT;
static char s_new[MK_MAX_LEN + 1U];
static size_t s_new_len = 0U;
static pwd_table_t s_work;

static void Send(const char *text)
{
    USART_Drv_WaitTxReady();
    (void)USART_Drv_SendString(text);
}

void Mode_ChangeMk_Wipe(void)
{
    Secure_Zero(s_new, sizeof(s_new));
    Secure_Zero(&s_work, sizeof(s_work));
    s_new_len = 0U;
    s_sub = CM_PROMPT;
}

void Mode_ChangeMk_Enter(void)
{
    Mode_ChangeMk_Wipe();
}

static mode_status_t OnNewEntry(const char *line, size_t len)
{
    mode_status_t status = MODE_RUNNING;

    if (len == 0U) {
        Send("\r\nCancelled.\r\n");
        status = MODE_CANCELLED;
    } else if (!Session_MkPolicyOk(line, len)) {
        Send("\r\nInvalid master key: use 8-31 printable characters. Try again.\r\nNew master key: ");
    } else {
        (void)memcpy(s_new, line, len);
        s_new[len] = '\0';
        s_new_len = len;
        Send("\r\nConfirm new master key: ");
        s_sub = CM_WAIT_CONFIRM;
    }
    return status;
}

static mode_status_t Apply(void)
{
    mode_status_t status = MODE_CANCELLED;

    Send("\r\nRe-encrypting, please wait...\r\n");
    if (Session_LoadTable(&s_work)) {
        switch (Session_SetNewKey(s_new, s_new_len, &s_work)) {
        case SESSION_OK:
            Send("Master key changed. The old key no longer works.\r\n");
            status = MODE_DONE;
            break;
        case SESSION_STORAGE_ERROR:
            Send("Storage error: nothing was changed, the old master key is still valid.\r\n");
            break;
        default:
            break;
        }
    }
    return status;
}

static mode_status_t OnConfirmEntry(const char *line, size_t len)
{
    mode_status_t status = MODE_RUNNING;

    if ((len != s_new_len) || !Kdf_ConstTimeEqual((const uint8_t *)line, (const uint8_t *)s_new, len)) {
        Send("\r\nThe two entries do not match. Start again.\r\n");
        Mode_ChangeMk_Wipe();
    } else {
        status = Apply();
        Mode_ChangeMk_Wipe();
    }
    return status;
}

mode_status_t Mode_ChangeMk_Run(void)
{
    char line[LINE_CAP];
    size_t len;
    mode_status_t status = MODE_RUNNING;

    switch (s_sub) {
    case CM_PROMPT:
        Send("\r\n== CHANGE MASTER KEY ==\r\nNew master key (8-31 printable characters, empty line cancels): ");
        s_sub = CM_WAIT_NEW;
        break;

    case CM_WAIT_NEW:
        if (USART_Drv_RxComplete()) {
            len = USART_Drv_TakeLine(line, sizeof(line));
            status = OnNewEntry(line, len);
            Secure_Zero(line, sizeof(line));
        }
        break;

    case CM_WAIT_CONFIRM:
        if (USART_Drv_RxComplete()) {
            len = USART_Drv_TakeLine(line, sizeof(line));
            status = OnConfirmEntry(line, len);
            Secure_Zero(line, sizeof(line));
        }
        break;

    default:
        s_sub = CM_PROMPT;
        break;
    }

    return status;
}
