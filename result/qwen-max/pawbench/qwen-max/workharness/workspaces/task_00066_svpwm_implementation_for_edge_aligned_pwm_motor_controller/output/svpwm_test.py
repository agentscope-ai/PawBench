import math

# Constants
PWM_PERIOD = 1000
DC_BUS_VOLTAGE = 310.0

# Sector determination
def determine_sector(Valpha, Vbeta):
    theta = math.atan2(Vbeta, Valpha)
    if -math.pi / 3 <= theta < math.pi / 3:
        return 1
    elif math.pi / 3 <= theta < 2 * math.pi / 3:
        return 2
    elif 2 * math.pi / 3 <= theta < math.pi:
        return 3
    elif -math.pi <= theta < -2 * math.pi / 3:
        return 4
    elif -2 * math.pi / 3 <= theta < -math.pi / 3:
        return 5
    elif math.pi <= theta < 4 * math.pi / 3:
        return 6
    return 0

# Dwell time calculation
def calculate_dwell_times(Valpha, Vbeta, sector):
    T1, T2 = 0.0, 0.0
    if sector == 1 or sector == 4:
        T1 = (Valpha + Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD
        T2 = (-Valpha + Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD
    elif sector == 2 or sector == 5:
        T1 = (Valpha - Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD
        T2 = (-Valpha - Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD
    elif sector == 3 or sector == 6:
        T1 = (-Valpha - Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD
        T2 = (Valpha - Vbeta) / DC_BUS_VOLTAGE * PWM_PERIOD
    T0 = PWM_PERIOD - T1 - T2
    # T0 distribution (split into 4 parts)
    T0_part = T0 / 4.0
    return T1, T2, T0, T0_part

# Compare value calculation
def calculate_compare_values(T1, T2, T0, T0_part, sector):
    CMP_U, CMP_V, CMP_W = 0.0, 0.0, 0.0
    if sector == 1:
        CMP_U = 500 + (T1 - T0_part) / 2.0
        CMP_V = 500 + (T2 - T0_part) / 2.0
        CMP_W = 500 - (T1 + T2 + T0_part) / 2.0
    elif sector == 2:
        CMP_U = 500 + (T1 - T0_part) / 2.0
        CMP_V = 500 - (T1 + T2 + T0_part) / 2.0
        CMP_W = 500 + (T2 - T0_part) / 2.0
    elif sector == 3:
        CMP_U = 500 - (T1 + T2 + T0_part) / 2.0
        CMP_V = 500 + (T1 - T0_part) / 2.0
        CMP_W = 500 + (T2 - T0_part) / 2.0
    elif sector == 4:
        CMP_U = 500 - (T1 + T2 + T0_part) / 2.0
        CMP_V = 500 + (T2 - T0_part) / 2.0
        CMP_W = 500 + (T1 - T0_part) / 2.0
    elif sector == 5:
        CMP_U = 500 + (T2 - T0_part) / 2.0
        CMP_V = 500 - (T1 + T2 + T0_part) / 2.0
        CMP_W = 500 + (T1 - T0_part) / 2.0
    elif sector == 6:
        CMP_U = 500 + (T2 - T0_part) / 2.0
        CMP_V = 500 + (T1 - T0_part) / 2.0
        CMP_W = 500 - (T1 + T2 + T0_part) / 2.0
    # Clamping compare values to [0, 1000]
    CMP_U = max(0, min(1000, CMP_U))
    CMP_V = max(0, min(1000, CMP_V))
    CMP_W = max(0, min(1000, CMP_W))
    # Adjust for the effective period base of 500 and T0 distribution
    CMP_U = 500 + (CMP_U - 500) * 2 - T0_part
    CMP_V = 500 + (CMP_V - 500) * 2 - T0_part
    CMP_W = 500 + (CMP_W - 500) * 2 - T0_part
    return int(CMP_U), int(CMP_V), int(CMP_W)

# Main function to compute SVPWM output
def svpwm_compute(Valpha, Vbeta):
    sector = determine_sector(Valpha, Vbeta)
    T1, T2, T0, T0_part = calculate_dwell_times(Valpha, Vbeta, sector)
    CMP_U, CMP_V, CMP_W = calculate_compare_values(T1, T2, T0, T0_part, sector)
    return sector, CMP_U, CMP_V, CMP_W

# Test vectors
test_vectors = [
    (0.0000, 0.0000, 0, 500, 500, 500),
    (86.6025, 50.0000, 1, 779, 500, 221),
    (0.0000, 100.0000, 2, 500, 779, 221),
    (-86.6025, 50.0000, 3, 221, 779, 500),
    (-86.6025, -50.0000, 4, 221, 500, 779),
    (-0.0000, -100.0000, 5, 500, 221, 779),
    (86.6025, -50.0000, 6, 779, 221, 500),
    (162.3336, 0.0000, 6, 893, 107, 107),
    (81.1668, 140.5850, 1, 893, 893, 107),
    (20.0000, 10.0000, 1, 562, 494, 438),
    (40.1209, 69.2121, 1, 694, 693, 306),
    (-120.0000, 40.0000, 3, 154, 846, 623),
]

# Verify the implementation
for i, (Valpha, Vbeta, expected_sector, expected_duty_u, expected_duty_v, expected_duty_w) in enumerate(test_vectors, 1):
    sector, duty_u, duty_v, duty_w = svpwm_compute(Valpha, Vbeta)
    print(f'T{i:02}: Sector={sector}, U={duty_u}, V={duty_v}, W={duty_w} (Expected: Sector={expected_sector}, U={expected_duty_u}, V={expected_duty_v}, W={expected_duty_w})')