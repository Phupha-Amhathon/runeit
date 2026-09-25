#include <string.h>
#include <stdio.h>
#include "mode_first_meet.h"
#include "session.h"
#include "usart_drv.h"
#include "kdf.h"
#include "secure_zero.h"
#include "systick_drv.h"

typedef enum {
    FM_PROMPT = 0,
    FM_WAIT_FIRST,
    FM_WAIT_CONFIRM,
} fm_state_t;

/* One extra byte so a 32+ character entry is seen (and rejected) instead of
 * being silently cut to a valid-looking 31. */
#define LINE_CAP (MK_MAX_LEN + 2U)

static fm_state_t s_sub = FM_PROMPT;
static char s_first[MK_MAX_LEN + 1U];
static size_t s_first_len = 0U;
/* Must outlive the call: the UART DMA reads it after Send() returns. */
static char s_msg[48];

static void Send(const char *text)
{
    USART_Drv_WaitTxReady();
    (void)USART_Drv_SendString(text);
}

void Mode_FirstMeet_Wipe(void)
{
    Secure_Zero(s_first, sizeof(s_first));
    s_first_len = 0U;
    s_sub = FM_PROMPT;
}

void Mode_FirstMeet_Enter(void)
{
    Mode_FirstMeet_Wipe();
    USART_Drv_WipeRx(); /* drop anything typed before the prompt was shown */
}

static void OnFirstEntry(const char *line, size_t len)
{
    if (!Session_MkPolicyOk(line, len)) {
        Send("\r\nInvalid master key: use 8-31 printable characters. Try again.\r\nMaster key: ");
    } else {
        (void)memcpy(s_first, line, len);
        s_first[len] = '\0';
        s_first_len = len;
        Send("\r\nConfirm master key: ");
        s_sub = FM_WAIT_CONFIRM;
    }
}

static mode_status_t OnConfirmEntry(const char *line, size_t len)
{
    mode_status_t status = MODE_RUNNING;

    if ((len != s_first_len) || !Kdf_ConstTimeEqual((const uint8_t *)line, (const uint8_t *)s_first, len)) {
        Send("\r\nThe two entries do not match. Start again.\r\n");
        Mode_FirstMeet_Wipe();
    } else {
        uint32_t started = SysTick_Drv_Millis();

        Send("\r\nDeriving key, please wait...\r\n");
        switch (Session_SetNewKey(s_first, len, NULL)) {
        case SESSION_OK:
            (void)snprintf(s_msg, sizeof(s_msg), "Master key set (%lu ms).\r\n",
                           (unsigned long)(SysTick_Drv_Millis() - started));
            Send(s_msg);
            status = MODE_DONE;
            break;
        case SESSION_STORAGE_ERROR:
            Send("Storage error, nothing was saved. Start again.\r\n");
            break;
        default:
            break;
        }
        Mode_FirstMeet_Wipe();
    }
    return status;
}

mode_status_t Mode_FirstMeet_Run(void)
{
    char line[LINE_CAP];
    size_t len;
    mode_status_t status = MODE_RUNNING;

    switch (s_sub) {
    case FM_PROMPT:
        Send("\r\n== FIRST TIME SETUP ==\r\nChoose a master key (8-31 printable characters).\r\nMaster key: ");
        s_sub = FM_WAIT_FIRST;
        break;

    case FM_WAIT_FIRST:
        if (USART_Drv_RxComplete()) {
            len = USART_Drv_TakeLine(line, sizeof(line));
            OnFirstEntry(line, len);
            Secure_Zero(line, sizeof(line));
        }
        break;

    case FM_WAIT_CONFIRM:
        if (USART_Drv_RxComplete()) {
            len = USART_Drv_TakeLine(line, sizeof(line));
            status = OnConfirmEntry(line, len);
            Secure_Zero(line, sizeof(line));
        }
        break;

    default:
        s_sub = FM_PROMPT;
        break;
    }

    return status;
}
