// svpwm_output/svpwm.c
#include "svpwm.h"
#include <math.h>

// Constants for sector determination
#define SECTOR_1 1
#define SECTOR_2 2
#define SECTOR_3 3
#define SECTOR_4 4
#define SECTOR_5 5
#define SECTOR_6 6

// Function to compute the SVPWM output
SVPWM_Output svpwm_compute(SVPWM_Input input) {
    // Sector determination
    int sector = 0;
    float theta = atan2(input.Vbeta, input.Valpha);
    if (theta >= -M_PI / 3 && theta < M_PI / 3) {
        sector = SECTOR_1;
    } else if (theta >= M_PI / 3 && theta < M_PI) {
        sector = SECTOR_2;
    } else if (theta >= M_PI && theta < 4 * M_PI / 3) {
        sector = SECTOR_3;
    } else if (theta >= 4 * M_PI / 3 && theta < 5 * M_PI / 3) {
        sector = SECTOR_4;
    } else if (theta >= 5 * M_PI / 3 && theta < 2 * M_PI) {
        sector = SECTOR_5;
    } else if (theta >= -M_PI && theta < -M_PI / 3) {
        sector = SECTOR_6;
    }

    // Dwell time calculation
    float T1, T2, T0;
    if (sector == SECTOR_1 || sector == SECTOR_4) {
        T1 = (input.Valpha + input.Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD;
        T2 = (-input.Valpha + input.Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD;
    } else if (sector == SECTOR_2 || sector == SECTOR_5) {
        T1 = (input.Valpha - input.Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD;
        T2 = (-input.Valpha - input.Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD;
    } else if (sector == SECTOR_3 || sector == SECTOR_6) {
        T1 = (-input.Valpha - input.Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD;
        T2 = (input.Valpha - input.Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD;
    }
    T0 = PWM_PERIOD - T1 - T2;

    // T0 distribution (split into 4 parts)
    float T0_part = T0 / 4.0f;

    // Compare value calculation
    SVPWM_Output output = {0};
    switch (sector) {
        case SECTOR_1:
            output.CMP_U = 500 + (T1 - T0_part) / 2.0f;
            output.CMP_V = 500 + (T2 - T0_part) / 2.0f;
            output.CMP_W = 500 - (T1 + T2 + T0_part) / 2.0f;
            break;
        case SECTOR_2:
            output.CMP_U = 500 + (T1 - T0_part) / 2.0f;
            output.CMP_V = 500 - (T1 + T2 + T0_part) / 2.0f;
            output.CMP_W = 500 + (T2 - T0_part) / 2.0f;
            break;
        case SECTOR_3:
            output.CMP_U = 500 - (T1 + T2 + T0_part) / 2.0f;
            output.CMP_V = 500 + (T1 - T0_part) / 2.0f;
            output.CMP_W = 500 + (T2 - T0_part) / 2.0f;
            break;
        case SECTOR_4:
            output.CMP_U = 500 - (T1 + T2 + T0_part) / 2.0f;
            output.CMP_V = 500 + (T2 - T0_part) / 2.0f;
            output.CMP_W = 500 + (T1 - T0_part) / 2.0f;
            break;
        case SECTOR_5:
            output.CMP_U = 500 + (T2 - T0_part) / 2.0f;
            output.CMP_V = 500 - (T1 + T2 + T0_part) / 2.0f;
            output.CMP_W = 500 + (T1 - T0_part) / 2.0f;
            break;
        case SECTOR_6:
            output.CMP_U = 500 + (T2 - T0_part) / 2.0f;
            output.CMP_V = 500 + (T1 - T0_part) / 2.0f;
            output.CMP_W = 500 - (T1 + T2 + T0_part) / 2.0f;
            break;
    }

    // Clamping compare values to [0, 1000]
    output.CMP_U = (output.CMP_U > 1000) ? 1000 : (output.CMP_U < 0) ? 0 : output.CMP_U;
    output.CMP_V = (output.CMP_V > 1000) ? 1000 : (output.CMP_V < 0) ? 0 : output.CMP_V;
    output.CMP_W = (output.CMP_W > 1000) ? 1000 : (output.CMP_W < 0) ? 0 : output.CMP_W;

    return output;
}