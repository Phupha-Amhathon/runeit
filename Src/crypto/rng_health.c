#include "rng_health.h"

void RNG_Health_Init(rng_health_state_t *state)
{
    state->last_value = 0U;
    state->repeat_count = 0U;
    state->window_ref_value = 0U;
    state->window_match_count = 0U;
    state->window_pos = 0U;
    state->started = 0U;
}

rng_health_result_t RNG_Health_Check(rng_health_state_t *state, uint16_t sample)
{
    rng_health_result_t result = RNG_HEALTH_OK;

    if (state->started == 0U) {
        /* First sample: nothing to compare against yet. */
        state->started = 1U;
        state->last_value = sample;
        state->repeat_count = 1U;
        state->window_ref_value = sample;
        state->window_match_count = 1U;
        state->window_pos = 1U;
    } else {
        /* Repetition Count Test */
        if (sample == state->last_value) {
            state->repeat_count++;
            if (state->repeat_count >= RNG_HEALTH_RCT_CUTOFF) {
                result = RNG_HEALTH_FAIL_REPETITION;
            }
        } else {
            state->last_value = sample;
            state->repeat_count = 1U;
        }

        /* Adaptive Proportion Test */
        if (sample == state->window_ref_value) {
            state->window_match_count++;
            if (state->window_match_count >= RNG_HEALTH_APT_CUTOFF) {
                result = RNG_HEALTH_FAIL_ADAPTIVE;
            }
        }

        state->window_pos++;
        if (state->window_pos >= RNG_HEALTH_APT_WINDOW) {
            /* Window finished: start a fresh one from the next sample. */
            state->window_ref_value = sample;
            state->window_match_count = 1U;
            state->window_pos = 0U;
        }
    }

    return result;
}
