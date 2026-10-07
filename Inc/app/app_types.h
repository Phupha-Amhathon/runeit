#ifndef APP_TYPES_H
#define APP_TYPES_H

/**
 * Top-level RUNEIT state machine (RUNEIT_V1 SYSTEM_OVERVIEW). The states
 * with several steps (FIRST_MEET_xxx, MK_AUTH_xxx, RETRIEVE, CHANGE_MK)
 * keep their own sub-states inside their mode module, like the nested
 * diagrams in the design document.
 *
 * Every state after MK_AUTH requires an authorized session; App_Run()
 * sends the machine back to INIT the moment the session is gone (panic
 * button), and INIT decides between FIRST_MEET and MK_AUTH again.
 */
typedef enum {
    APP_STATE_INIT = 0,
    APP_STATE_FIRST_MEET,
    APP_STATE_MK_AUTH,
    APP_STATE_MODE_SELECTION,
    APP_STATE_RETRIEVE_MODE,
    APP_STATE_GENERATE_MODE,   /* ADC-noise password, see mode_generate.h */
    APP_STATE_CHANGE_MK_MODE,
} app_state_t;

/** Result of one step of a mode's sub-state machine. */
typedef enum {
    MODE_RUNNING = 0,
    MODE_DONE,
    MODE_CANCELLED,
} mode_status_t;

#endif /* APP_TYPES_H */
