#include <stdio.h>
#include <string.h>
#include "mode_generate.h"
#include "partition_store.h"
#include "password_table.h"
#include "usart_drv.h"
#include "adc_drv.h"
#include "entropy_pool.h"
#include "session.h"
#include "secure_zero.h"
#include "critical_drv.h"
#include "systick_drv.h"

#define GEN_NAME_MAX_LEN      (PWD_NAME_LEN - 1U)
#define GEN_PWD_MAX_LEN       (PWD_SECRET_LEN - 1U)
/* Von Neumann keeps only pairs whose two bits differ, so the yield per block
 * is 2*p*(1-p) and falls off sharply as the source gets more skewed. Measured
 * in simulation, a 31-character password needs about 3 rounds at 83% ones,
 * 9 at the ~96% the PA0 sensor shows on the board, and about 16 at 98%. The
 * old cap of 16 was sized for 83% and left no headroom; 40 is that headroom,
 * not a fix for an observed failure. */
#define GEN_MAX_ROUNDS        40U
#define GEN_BLOCK_TIMEOUT_MS  200U /* a 512-sample block takes about 3 ms */
#define GEN_LINE_LEN          32U
#define GEN_MSG_LEN           128U
#define GEN_CHARSET_MAX_LEN   94U /* lower 26 + upper 26 + digit 10 + symbol 32 */

static const char s_chars_lower[]  = "abcdefghijklmnopqrstuvwxyz";
static const char s_chars_upper[]  = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
static const char s_chars_digit[]  = "0123456789";
static const char s_chars_symbol[] = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";

typedef enum {
    GEN_SUB_PROMPT_ID = 0,
    GEN_SUB_WAIT_ID,
    GEN_SUB_WAIT_OVERWRITE,
    GEN_SUB_PROMPT_NAME,
    GEN_SUB_WAIT_NAME,
    GEN_SUB_PROMPT_CLASSES,
    GEN_SUB_WAIT_CLASSES,
    GEN_SUB_PROMPT_LENGTH,
    GEN_SUB_WAIT_LENGTH,
    GEN_SUB_START_ROUND,
    GEN_SUB_WAIT_TEMP,
    GEN_SUB_WAIT_LIGHT,
    GEN_SUB_SAVE,
} generate_sub_state_t;

static generate_sub_state_t s_sub = GEN_SUB_PROMPT_ID;

static pwd_table_t s_table;
static bool s_loaded = false;
static volatile bool s_wiped = false;

static uint32_t s_id = 0U;
static char s_name[PWD_NAME_LEN];
static char s_charset[GEN_CHARSET_MAX_LEN + 1U];
static uint16_t s_charset_len = 0U;
static uint16_t s_reject_threshold = 0U;
static uint32_t s_length = 0U;

static char s_pwd[PWD_SECRET_LEN];
static uint32_t s_chars_done = 0U;
static uint32_t s_rounds = 0U;
static uint32_t s_wait_start_ms = 0U;
static entropy_pool_t s_pool;
static uint16_t s_samples[ENTROPY_BLOCK_SAMPLES];

/* Formatted prompts and the final "saved" message are built here, so it is
 * wiped with the other secrets. */
static char s_msg[GEN_MSG_LEN];

static void ClearSecrets(void)
{
    Secure_Zero(&s_table, sizeof(s_table));
    Secure_Zero(s_pwd, sizeof(s_pwd));
    Secure_Zero(s_msg, sizeof(s_msg));
    Secure_Zero(&s_pool, sizeof(s_pool));
    Secure_Zero(s_samples, sizeof(s_samples));
}

static void SendText(const char *text)
{
    USART_Drv_WaitTxReady();
    (void)USART_Drv_SendString(text);
}

/* Callers must USART_Drv_WaitTxReady() before rewriting s_msg, because a
 * DMA transfer from an earlier message may still be reading it. */
static void SendMsg(void)
{
    (void)USART_Drv_SendString(s_msg);
}

static bool ParseUnsigned(const char *text, uint32_t *value_out)
{
    uint32_t value = 0U;
    size_t i = 0U;
    bool ok = (text[0] != '\0');

    while (ok && (text[i] != '\0')) {
        if ((text[i] < '0') || (text[i] > '9') || (i >= 4U)) {
            ok = false;
        } else {
            value = (value * 10U) + (uint32_t)(text[i] - '0');
        }
        i++;
    }
    if (ok) {
        *value_out = value;
    }
    return ok;
}

static bool NameIsValid(const char *text)
{
    size_t len = strlen(text);
    size_t i;
    bool ok = (len >= 1U) && (len <= GEN_NAME_MAX_LEN);

    for (i = 0U; ok && (i < len); i++) {
        if ((text[i] < ' ') || (text[i] > '~')) {
            ok = false;
        }
    }
    return ok;
}

static void AppendCharClass(const char *class_chars)
{
    size_t i = 0U;

    while (class_chars[i] != '\0') {
        s_charset[s_charset_len] = class_chars[i];
        s_charset_len++;
        i++;
    }
}

/* Builds s_charset from a string of class letters (l, u, d, s in any case).
 * Returns false if none of them was given. */
static bool BuildCharset(const char *letters)
{
    bool want_lower = false;
    bool want_upper = false;
    bool want_digit = false;
    bool want_symbol = false;
    size_t i;

    for (i = 0U; letters[i] != '\0'; i++) {
        switch (letters[i]) {
        case 'l': case 'L': want_lower = true;  break;
        case 'u': case 'U': want_upper = true;  break;
        case 'd': case 'D': want_digit = true;  break;
        case 's': case 'S': want_symbol = true; break;
        default: break;
        }
    }

    s_charset_len = 0U;
    if (want_lower) {
        AppendCharClass(s_chars_lower);
    }
    if (want_upper) {
        AppendCharClass(s_chars_upper);
    }
    if (want_digit) {
        AppendCharClass(s_chars_digit);
    }
    if (want_symbol) {
        AppendCharClass(s_chars_symbol);
    }

    if (s_charset_len > 0U) {
        /* Largest multiple of the charset size that fits in one byte; bytes
         * at or above it are discarded so every character is equally likely. */
        s_reject_threshold = (uint16_t)((256U / s_charset_len) * s_charset_len);
    }
    return s_charset_len > 0U;
}

static void ProduceChars(void)
{
    uint8_t value;

    while ((s_chars_done < s_length) && Entropy_Pool_TakeByte(&s_pool, &value)) {
        if ((uint16_t)value < s_reject_threshold) {
            s_pwd[s_chars_done] = s_charset[value % s_charset_len];
            s_chars_done++;
        }
    }
}

static void MarkWaitStart(void)
{
    s_wait_start_ms = SysTick_Drv_Millis();
}

static bool WaitTimedOut(void)
{
    return (SysTick_Drv_Millis() - s_wait_start_ms) > GEN_BLOCK_TIMEOUT_MS;
}

void Mode_Generate_Enter(void)
{
    s_wiped = false;
    ClearSecrets();
    Entropy_Pool_Init(&s_pool);

    s_loaded = Session_LoadTable(&s_table);
    s_sub = GEN_SUB_PROMPT_ID;
    s_chars_done = 0U;
    s_rounds = 0U;
}

void Mode_Generate_Wipe(void)
{
    s_wiped = true;
    ClearSecrets();
}

static bool HandleId(const char *line)
{
    uint32_t id = 0U;
    bool done = false;

    if ((line[0] == '\0') || (line[0] == 'q') || (line[0] == 'Q')) {
        done = true;
    } else if (!ParseUnsigned(line, &id) || (id >= PWD_TABLE_MAX_ENTRIES)) {
        SendText("\r\nInvalid id, expected 0-30.\r\n");
        s_sub = GEN_SUB_PROMPT_ID;
    } else {
        s_id = id;
        if (Password_Table_EntryIsUsed(&s_table.entries[id])) {
            USART_Drv_WaitTxReady();
            (void)snprintf(s_msg, sizeof(s_msg),
                           "\r\nId %lu is in use by '%.15s'. Overwrite? (y/n): ",
                           (unsigned long)id, s_table.entries[id].name);
            SendMsg();
            s_sub = GEN_SUB_WAIT_OVERWRITE;
        } else {
            s_sub = GEN_SUB_PROMPT_NAME;
        }
    }
    return done;
}

static bool HandleName(const char *line)
{
    bool done = (line[0] == '\0');

    if (!done) {
        if (NameIsValid(line)) {
            (void)strncpy(s_name, line, sizeof(s_name) - 1U);
            s_name[sizeof(s_name) - 1U] = '\0';
            s_sub = GEN_SUB_PROMPT_CLASSES;
        } else {
            SendText("\r\nName must be 1-15 printable characters.\r\n");
            s_sub = GEN_SUB_PROMPT_NAME;
        }
    }
    return done;
}

static bool HandleClasses(const char *line)
{
    bool done = (line[0] == '\0');

    if (!done) {
        if (BuildCharset(line)) {
            s_sub = GEN_SUB_PROMPT_LENGTH;
        } else {
            SendText("\r\nPick at least one of l, u, d, s.\r\n");
            s_sub = GEN_SUB_PROMPT_CLASSES;
        }
    }
    return done;
}

static bool HandleLength(const char *line)
{
    uint32_t length = 0U;
    bool done = (line[0] == '\0');

    if (!done) {
        if (ParseUnsigned(line, &length) && (length >= 1U) && (length <= GEN_PWD_MAX_LEN)) {
            s_length = length;
            s_chars_done = 0U;
            s_rounds = 0U;
            Entropy_Pool_Init(&s_pool);
            MarkWaitStart();
            s_sub = GEN_SUB_START_ROUND;
        } else {
            SendText("\r\nLength must be 1-31.\r\n");
            s_sub = GEN_SUB_PROMPT_LENGTH;
        }
    }
    return done;
}

static bool ReportEntropyFault(void)
{
    SendText("\r\nENTROPY SOURCE FAULT - nothing was saved.\r\n");
    return true;
}

/* Returns true only when sampling failed and the mode must end. */
static bool StepSampling(void)
{
    bool failed = false;

    switch (s_sub) {
    case GEN_SUB_START_ROUND:
        ProduceChars();
        if (s_chars_done >= s_length) {
            s_pwd[s_length] = '\0';
            s_sub = GEN_SUB_SAVE;
        } else if (s_rounds >= GEN_MAX_ROUNDS) {
            failed = ReportEntropyFault();
        } else if (ADC_Drv_StartBlock(ADC_DRV_CH_TEMP, s_samples, ENTROPY_BLOCK_SAMPLES)) {
            s_rounds++;
            MarkWaitStart();
            s_sub = GEN_SUB_WAIT_TEMP;
        } else if (WaitTimedOut()) {
            failed = ReportEntropyFault();
        } else {
            /* an earlier block is still in flight; retry on the next pass */
        }
        break;

    case GEN_SUB_WAIT_TEMP:
        if (ADC_Drv_BlockReady()) {
            if (!Entropy_Pool_Absorb(&s_pool, 0U, s_samples, ENTROPY_BLOCK_SAMPLES) ||
                !ADC_Drv_StartBlock(ADC_DRV_CH_LIGHT, s_samples, ENTROPY_BLOCK_SAMPLES)) {
                failed = ReportEntropyFault();
            } else {
                MarkWaitStart();
                s_sub = GEN_SUB_WAIT_LIGHT;
            }
        } else if (WaitTimedOut()) {
            failed = ReportEntropyFault();
        } else {
            /* block still converting */
        }
        break;

    case GEN_SUB_WAIT_LIGHT:
        if (ADC_Drv_BlockReady()) {
            if (!Entropy_Pool_Absorb(&s_pool, 1U, s_samples, ENTROPY_BLOCK_SAMPLES)) {
                failed = ReportEntropyFault();
            } else {
                MarkWaitStart();
                s_sub = GEN_SUB_START_ROUND;
            }
        } else if (WaitTimedOut()) {
            failed = ReportEntropyFault();
        } else {
            /* block still converting */
        }
        break;

    default:
        break;
    }
    return failed;
}

/* The interrupt mask spans the panic check, the table update and the whole
 * save. Partition_Store_Commit() copies the table as its first action, so an
 * unmasked panic could zero s_table midway and publish a mostly-empty table
 * as the newest partition -- the stale-partition failure the project notes
 * warn about. The cost is that a button press during the flash erase is held
 * pending for up to ~2 s; data integrity is worth more than that latency. */
static bool SaveEntry(void)
{
    bool saved = false;
    uint32_t saved_mask = Critical_Enter();

    if (!s_wiped) {
        pwd_entry_t *entry = &s_table.entries[s_id];

        Secure_Zero(entry, sizeof(*entry));
        (void)strncpy(entry->name, s_name, PWD_NAME_LEN - 1U);
        (void)strncpy(entry->password, s_pwd, PWD_SECRET_LEN - 1U);
        /* Session_Save() reports false unless the new partition read back
         * from flash carries the version it just wrote. */
        saved = Session_Save(&s_table);
    }
    Critical_Exit(saved_mask);
    return saved;
}

mode_status_t Mode_Generate_Run(void)
{
    char line[GEN_LINE_LEN];
    mode_status_t status = MODE_RUNNING;
    bool done = false;
    bool have_line = false;
    bool saved;

    if (s_wiped) {
        done = true;
    } else if (!s_loaded) {
        SendText("\r\nNo open session to save into.\r\n");
        done = true;
    } else {
        switch (s_sub) {
        case GEN_SUB_PROMPT_ID:
            SendText("\r\nId to write (0-30), or 'q' to go back: ");
            s_sub = GEN_SUB_WAIT_ID;
            break;
        case GEN_SUB_PROMPT_NAME:
            SendText("\r\nService name (1-15 chars, empty line cancels): ");
            s_sub = GEN_SUB_WAIT_NAME;
            break;
        case GEN_SUB_PROMPT_CLASSES:
            SendText("\r\nCharacter classes: l=lower u=upper d=digit s=symbol (e.g. luds): ");
            s_sub = GEN_SUB_WAIT_CLASSES;
            break;
        case GEN_SUB_PROMPT_LENGTH:
            SendText("\r\nPassword length (1-31): ");
            s_sub = GEN_SUB_WAIT_LENGTH;
            break;

        case GEN_SUB_WAIT_ID:
        case GEN_SUB_WAIT_OVERWRITE:
        case GEN_SUB_WAIT_NAME:
        case GEN_SUB_WAIT_CLASSES:
        case GEN_SUB_WAIT_LENGTH:
            have_line = USART_Drv_RxComplete();
            if (have_line) {
                (void)USART_Drv_TakeLine(line, sizeof(line));
                if (s_sub == GEN_SUB_WAIT_ID) {
                    done = HandleId(line);
                } else if (s_sub == GEN_SUB_WAIT_OVERWRITE) {
                    s_sub = ((line[0] == 'y') || (line[0] == 'Y')) ? GEN_SUB_PROMPT_NAME
                                                                   : GEN_SUB_PROMPT_ID;
                } else if (s_sub == GEN_SUB_WAIT_NAME) {
                    done = HandleName(line);
                } else if (s_sub == GEN_SUB_WAIT_CLASSES) {
                    done = HandleClasses(line);
                } else {
                    done = HandleLength(line);
                }
            }
            break;

        case GEN_SUB_START_ROUND:
        case GEN_SUB_WAIT_TEMP:
        case GEN_SUB_WAIT_LIGHT:
            done = StepSampling();
            break;

        case GEN_SUB_SAVE:
            saved = SaveEntry();
            if (s_wiped) {
                /* The panic button fired during the save: s_pwd has already
                 * been zeroed, so there is nothing safe left to print. */
            } else if (saved) {
                USART_Drv_WaitTxReady();
                (void)snprintf(s_msg, sizeof(s_msg),
                               "\r\nSaved as id %lu (%.15s).\r\nPassword: %.31s\r\n",
                               (unsigned long)s_id, s_name, s_pwd);
                SendMsg();
            } else {
                SendText("\r\nSAVE FAILED - flash did not verify, previous data is intact.\r\n");
            }
            done = true;
            break;

        default:
            s_sub = GEN_SUB_PROMPT_ID;
            break;
        }
    }

    if (done) {
        USART_Drv_WaitTxReady();
        ClearSecrets();
        status = MODE_DONE;
    }
    return status;
}
