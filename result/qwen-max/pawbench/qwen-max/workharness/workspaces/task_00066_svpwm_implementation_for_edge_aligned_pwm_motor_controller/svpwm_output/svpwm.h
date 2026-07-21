// svpwm_output/svpwm.h
#ifndef SVPWM_H
#define SVPWM_H

#include <stdint.h>

// Constants
#define PWM_PERIOD 1000
#define DC_BUS_VOLTAGE 310.0f

// Input structure for SVPWM
typedef struct {
    float Valpha; // Alpha-axis voltage component (V)
    float Vbeta;  // Beta-axis voltage component (V)
} SVPWM_Input;

// Output structure for SVPWM
typedef struct {
    uint16_t CMP_U; // Compare value for U phase
    uint16_t CMP_V; // Compare value for V phase
    uint16_t CMP_W; // Compare value for W phase
} SVPWM_Output;

// Function prototype for SVPWM computation
SVPWM_Output svpwm_compute(SVPWM_Input input);

#endif // SVPWM_H