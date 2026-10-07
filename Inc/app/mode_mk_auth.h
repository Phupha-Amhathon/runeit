#ifndef MODE_MK_AUTH_H
#define MODE_MK_AUTH_H

#include "app_types.h"

/**
 * MK_AUTH_PROMPT -> MK_AUTH_CHECK of RUNEIT_V1. Attempts are unlimited; what
 * throttles guessing is the key derivation itself, which takes seconds per
 * try (see crypto/kdf.h). MODE_DONE means the session is open.
 * MODE_CANCELLED means there is no stored data (go back to INIT).
 */
void Mode_MkAuth_Enter(void);
mode_status_t Mode_MkAuth_Run(void);

/** Nothing secret is kept between steps; provided for symmetry with the other modes. */
void Mode_MkAuth_Wipe(void);

#endif /* MODE_MK_AUTH_H */
