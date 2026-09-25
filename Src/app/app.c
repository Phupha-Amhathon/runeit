#include <string.h>
#include <stdbool.h>
#include "app.h"
#include "app_types.h"
#include "usart_drv.h"
#include "exti_drv.h"
#include "partition_store.h"
#include "password_table.h"
#include "mode_retrieve.h"
#include "mode_generate.h"
#include "sha256.h"

/* Stage-B placeholder: FIRST_MEET_xxx and MK_AUTH_xxx aren't implemented yet, so
 * INIT uses this fixed key instead of a user-supplied, hashed-and-verified
 * input_mk. Stage C replaces every use of this with the real auth flow. */
static const uint8_t s_hardcoded_mk[] = "TempMasterKey1";
#define HARDCODED_MK_LEN ((uint32_t)(sizeof(s_hardcoded_mk) - 1U))

static volatile app_state_t g_state = APP_STATE_INIT;

static const char s_menu_text[] =
    "\r\n== MODE SELECTION ==\r\n"
    "  1) Retrieve password\r\n"
    "  2) Generate password\r\n"
    "  3) Change master key (not implemented yet)\r\n"
    "Select: ";

static void App_PanicHandler(void)
{
    /* Stage-B interim behaviour: there is no MK_AUTH_PROMPT to return to
     * yet, so the closest available "locked" state is MODE_SELECTION.
     * Stage C will point this at MK_AUTH_PROMPT instead. */
    Mode_Retrieve_Wipe();
    Mode_Generate_Wipe();
    g_state = APP_STATE_MODE_SELECTION;
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

    if (Partition_Store_Active() == NULL) {
        /* First boot, no FIRST_MEET yet: auto-provision a sample table
         * under the hardcoded MK so RETRIEVE_MODE has something to
         * decrypt and show while this stage is being tested. */
        pwd_table_t seed;
        uint32_t hash_mk[SHA256_DIGEST_WORDS];

        Password_Table_InitEmpty(&seed);
        (void)strncpy(seed.entries[0].name, "example.com", PWD_NAME_LEN - 1U);
        (void)strncpy(seed.entries[0].password, "hunter2", PWD_SECRET_LEN - 1U);
        (void)strncpy(seed.entries[1].name, "email", PWD_NAME_LEN - 1U);
        (void)strncpy(seed.entries[1].password, "correct-horse-battery", PWD_SECRET_LEN - 1U);

        SHA256_Compute(s_hardcoded_mk, HARDCODED_MK_LEN, (uint8_t *)hash_mk);
        (void)Partition_Store_Commit(s_hardcoded_mk, HARDCODED_MK_LEN, hash_mk, &seed);
        Partition_Store_Init(); /* re-read so Active() reflects the fresh commit */
    }

    g_state = APP_STATE_MODE_SELECTION;
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
    switch (line[0]) {
    case '1':
        g_state = APP_STATE_RETRIEVE_MODE;
        break;
    case '2':
        g_state = APP_STATE_GENERATE_MODE;
        break;
    case '3':
        g_state = APP_STATE_CHANGE_MK_MODE;
        break;
    default:
        USART_Drv_WaitTxReady();
        (void)USART_Drv_SendString("\r\nUnknown option.\r\n");
        SendMenu();
        break;
    }
}

static void HandleNotImplemented(bool entered)
{
    if (entered) {
        USART_Drv_WaitTxReady();
        (void)USART_Drv_SendString("\r\nNot implemented yet.\r\n");
        g_state = APP_STATE_MODE_SELECTION;
    }
}

void App_Run(void)
{
    app_state_t prev_state = APP_STATE_INIT;

    for (;;) {
        /* One read only: the panic ISR can change g_state between reads, which
         * would swallow the "entered" edge and skip the menu. */
        app_state_t state = g_state;
        bool entered = (state != prev_state);
        prev_state = state;

        switch (state) {
        case APP_STATE_INIT:
            HandleInit();
            break;

        case APP_STATE_MODE_SELECTION:
            HandleModeSelection(entered);
            break;

        case APP_STATE_RETRIEVE_MODE:
            if (entered) {
                Mode_Retrieve_Enter(s_hardcoded_mk, HARDCODED_MK_LEN);
            }
            if (Mode_Retrieve_Run()) {
                g_state = APP_STATE_MODE_SELECTION;
            }
            break;

        case APP_STATE_GENERATE_MODE:
            if (entered) {
                Mode_Generate_Enter(s_hardcoded_mk, HARDCODED_MK_LEN);
            }
            switch (Mode_Generate_Run()) {
            case MODE_GENERATE_READY_TO_SAVE:
                g_state = APP_STATE_TOGGLE_PARTITION;
                break;
            case MODE_GENERATE_FINISHED:
                g_state = APP_STATE_MODE_SELECTION;
                break;
            default:
                break;
            }
            break;

        case APP_STATE_TOGGLE_PARTITION:
            /* Back through INIT afterwards so the partitions are re-read from
             * flash, which is what makes the fresh commit the active one. */
            Mode_Generate_Commit();
            g_state = APP_STATE_INIT;
            break;

        case APP_STATE_CHANGE_MK_MODE:
            HandleNotImplemented(entered);
            break;

        default:
            g_state = APP_STATE_MODE_SELECTION;
            break;
        }
    }
}
