#include <string.h>
#include <stdbool.h>
#include "app.h"
#include "app_types.h"
#include "usart_drv.h"
#include "exti_drv.h"
#include "partition_store.h"
#include "password_table.h"
#include "session.h"
#include "mode_first_meet.h"
#include "mode_mk_auth.h"
#include "mode_retrieve.h"
#include "mode_change_mk.h"
#include "mode_generate.h"

static volatile app_state_t g_state = APP_STATE_INIT;

static const char s_menu_text[] =
    "\r\n== MODE SELECTION ==\r\n"
    "  1) Retrieve password\r\n"
    "  2) Generate password\r\n"
    "  3) Change master key\r\n"
    "Select: ";

/* Panic button, called from the EXTI interrupt: destroy every copy of a
 * secret we know about, then drop back to INIT, which decides between
 * FIRST_MEET and MK_AUTH again. */
static void App_PanicHandler(void)
{
    Session_Wipe();
    Mode_Retrieve_Wipe();
    Mode_FirstMeet_Wipe();
    Mode_MkAuth_Wipe();
    Mode_ChangeMk_Wipe();
    Mode_Generate_Wipe();
    Password_Table_WipeScratch();
    USART_Drv_WipeRx();
    g_state = APP_STATE_INIT;
}

void App_Init(void)
{
    EXTI_Drv_SetPanicCallback(App_PanicHandler);
}

static void SendMenu(void)
{
    USART_Drv_WaitTxReady();
    (void)USART_Drv_SendString(s_menu_text);
}

static void HandleInit(void)
{
    Partition_Store_Init();
    g_state = (Partition_Store_Active() == NULL) ? APP_STATE_FIRST_MEET : APP_STATE_MK_AUTH;
}

static void HandleModeSelection(bool entered)
{
    char line[8];

    if (entered) {
        SendMenu();
    }
    if (!USART_Drv_RxComplete()) {
        return;
    }

    (void)USART_Drv_TakeLine(line, sizeof(line));
    if (strcmp(line, "1") == 0) {
        g_state = APP_STATE_RETRIEVE_MODE;
    } else if (strcmp(line, "2") == 0) {
        g_state = APP_STATE_GENERATE_MODE;
    } else if (strcmp(line, "3") == 0) {
        g_state = APP_STATE_CHANGE_MK_MODE;
    } else {
        USART_Drv_WaitTxReady();
        (void)USART_Drv_SendString("\r\nUnknown option.\r\n");
        SendMenu();
    }
}

static bool StateNeedsSession(app_state_t state)
{
    return (state != APP_STATE_INIT) && (state != APP_STATE_FIRST_MEET) && (state != APP_STATE_MK_AUTH);
}

void App_Run(void)
{
    app_state_t prev_state = APP_STATE_INIT;

    for (;;) {
        /* Read g_state once: the panic ISR can change it between reads, which
         * would swallow the "entered" edge and skip a screen. */
        app_state_t state = g_state;
        bool entered = (state != prev_state);

        /* Defence in depth: whatever a handler or the panic button did to
         * g_state, nothing past the login screens runs without a session. */
        if (StateNeedsSession(state) && !Session_IsAuthorized()) {
            g_state = APP_STATE_INIT;
            state = APP_STATE_INIT;
        }
        prev_state = state;


        switch (state) {
        case APP_STATE_INIT:
            HandleInit();
            break;

        case APP_STATE_FIRST_MEET:
            if (entered) {
                Mode_FirstMeet_Enter();
            }
            if (Mode_FirstMeet_Run() == MODE_DONE) {
                g_state = APP_STATE_MODE_SELECTION;
            }
            break;

        case APP_STATE_MK_AUTH:
            if (entered) {
                Mode_MkAuth_Enter();
            }
            switch (Mode_MkAuth_Run()) {
            case MODE_DONE:
                g_state = APP_STATE_MODE_SELECTION;
                break;
            case MODE_CANCELLED:
                g_state = APP_STATE_INIT;
                break;
            default:
                break;
            }
            break;

        case APP_STATE_MODE_SELECTION:
            HandleModeSelection(entered);
            break;

        case APP_STATE_RETRIEVE_MODE:
            if (entered && !Mode_Retrieve_Enter()) {
                g_state = APP_STATE_INIT;
            } else if (Mode_Retrieve_Run()) {
                g_state = APP_STATE_MODE_SELECTION;
            } else {
                /* still running */
            }
            break;

        case APP_STATE_GENERATE_MODE:
            if (entered) {
                Mode_Generate_Enter();
            }
            if (Mode_Generate_Run() != MODE_RUNNING) {
                g_state = APP_STATE_MODE_SELECTION;
            }
            break;

        case APP_STATE_CHANGE_MK_MODE:
            if (entered) {
                Mode_ChangeMk_Enter();
            }
            if (Mode_ChangeMk_Run() != MODE_RUNNING) {
                g_state = APP_STATE_MODE_SELECTION;
            }
            break;

        default:
            g_state = APP_STATE_INIT;
            break;
        }
    }
}
