/**
 ******************************************************************************
 * @file           : rng_health.c
 * @brief          : Entropy-source health test for the ADC noise generator
 ******************************************************************************
 *
 * See rng_health.h for the tests implemented and how to call them.
 *
 ******************************************************************************
 */

#include "rng_health.h"

void rht_init(rht_state_t *state)
{
    state->last_value = 0U;
    state->repeat_count = 0U;
    state->window_ref_value = 0U;
    state->window_match_count = 0U;
    state->window_pos = 0U;
    state->started = 0U;
}

rht_result_t rht_check(rht_state_t *state, uint16_t sample)
{
    rht_result_t result = RHT_OK;

    if (state->started == 0U)
    {
        /* First sample: nothing to compare against yet. */
        state->started = 1U;
        state->last_value = sample;
        state->repeat_count = 1U;
        state->window_ref_value = sample;
        state->window_match_count = 1U;
        state->window_pos = 1U;
        return RHT_OK;
    }

    /* --- Repetition Count Test --- */
    if (sample == state->last_value)
    {
        state->repeat_count++;
        if (state->repeat_count >= RHT_RCT_CUTOFF)
        {
            result = RHT_FAIL_REPETITION;
        }
    }
    else
    {
        state->last_value = sample;
        state->repeat_count = 1U;
    }

    /* --- Adaptive Proportion Test --- */
    if (sample == state->window_ref_value)
    {
        state->window_match_count++;
        if (state->window_match_count >= RHT_APT_CUTOFF)
        {
            result = RHT_FAIL_ADAPTIVE;
        }
    }

    state->window_pos++;
    if (state->window_pos >= RHT_APT_WINDOW)
    {
        /* Window finished: start a fresh window from the next sample. */
        state->window_ref_value = sample;
        state->window_match_count = 1U;
        state->window_pos = 0U;
    }

    return result;
}
