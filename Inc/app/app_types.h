#ifndef APP_TYPES_H
#define APP_TYPES_H

/**
 * Top-level RUNEIT state machine (RUNEIT_V1 SYSTEM_OVERVIEW).
 *
 * FIRST_MEET_xxx and MK_AUTH_xxx are intentionally not part of this round's
 * implementation yet (Stage C follow-up): App_HandleInit() currently
 * auto-provisions a hardcoded input_mk so RETRIEVE_MODE can be built and
 * tested first, per the agreed staged plan.
 */
typedef enum {
    APP_STATE_INIT = 0,
    APP_STATE_MODE_SELECTION,
    APP_STATE_RETRIEVE_MODE,
    APP_STATE_GENERATE_MODE,   /* ADC-noise password, see mode_generate.h */
    APP_STATE_CHANGE_MK_MODE,  /* stub: real auth (Stage C) needed first */
    APP_STATE_TOGGLE_PARTITION, /* commits the RAM table to the other partition after a write mode */
} app_state_t;

#endif /* APP_TYPES_H */
