#include <stdio.h>
#include "mode_mk_auth.h"
#include "session.h"
#include "usart_drv.h"
#include "secure_zero.h"
#include "systick_drv.h"

typedef enum {
    MA_PROMPT = 0,
    MA_WAIT,
} ma_state_t;

#define LINE_CAP (MK_MAX_LEN + 2U)

static ma_state_t s_sub = MA_PROMPT;
/* Must outlive the call: the UART DMA reads it after Send() returns. */
static char s_msg[64];

static void Send(const char *text)
{
    USART_Drv_WaitTxReady();
    (void)USART_Drv_SendString(text);
}

void Mode_MkAuth_Wipe(void)
{
    s_sub = MA_PROMPT;
}

void Mode_MkAuth_Enter(void)
{
    s_sub = MA_PROMPT;
    USART_Drv_WipeRx(); /* drop anything typed before the prompt was shown */
}

static mode_status_t Check(const char *line, size_t len)
{
    mode_status_t status = MODE_RUNNING;

    if (len == 0U) {
        Send("\r\nMaster key: ");
        return status;
    }

    uint32_t started = SysTick_Drv_Millis();
    session_result_t result;

    Send("\r\nChecking, please wait...\r\n");
    result = Session_Authenticate(line, len);
    (void)snprintf(s_msg, sizeof(s_msg), "%s (%lu ms).\r\n",
                   (result == SESSION_OK) ? "Access granted" : "Wrong master key",
                   (unsigned long)(SysTick_Drv_Millis() - started));
    switch (result) {
    case SESSION_OK:
        Send(s_msg);
        status = MODE_DONE;
        break;
    case SESSION_NO_PARTITION:
        status = MODE_CANCELLED;
        break;
    case SESSION_CANCELLED:
        s_sub = MA_PROMPT;
        break;
    default:
        Send(s_msg);
        Send("Master key: ");
        break;
    }
    return status;
}

mode_status_t Mode_MkAuth_Run(void)
{
    char line[LINE_CAP];
    mode_status_t status = MODE_RUNNING;

    switch (s_sub) {
    case MA_PROMPT:
        Send("\r\n== LOCKED ==\r\nMaster key: ");
        s_sub = MA_WAIT;
        break;

    case MA_WAIT:
        if (USART_Drv_RxComplete()) {
            size_t len = USART_Drv_TakeLine(line, sizeof(line));
            status = Check(line, len);
            Secure_Zero(line, sizeof(line));
        }
        break;

    default:
        s_sub = MA_PROMPT;
        break;
    }

    return status;
}
