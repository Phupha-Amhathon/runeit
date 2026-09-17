#ifndef APP_H
#define APP_H

/** Wires the panic-button callback; call after all drivers are initialized. */
void App_Init(void);

/** Runs the RUNEIT state machine forever. Never returns. */
void App_Run(void);

#endif /* APP_H */
