import numpy as np
from r2r_simulator import R2RSimulator

# Initialize the simulator to get the initial state and parameters
sim = R2RSimulator()
params = sim.get_params()
EA, J, R, fb, L, v0, dt, num_sections = params['EA'], params['J'], params['R'], params['fb'], params['L'], params['v0'], params['dt'], params['num_sections']

# Get the initial state (tensions and velocities)
x_ref, u_ref = sim.get_reference()
T_ref = x_ref[:num_sections]
v_ref = x_ref[num_sections:]

# Define the state and input vectors
x = np.concatenate([T_ref, v_ref])
u = u_ref

# Define the Jacobian matrices for the state and input
A = np.zeros((12, 12))
B = np.zeros((12, 6))

# Tension dynamics: dT_i/dt = (EA/L)*(v_i - v_{i-1}) + (1/L)*(v_{i-1}*T_{i-1} - v_i*T_i)
for i in range(num_sections):
    A[i, i] = -v_ref[i] / L
    if i > 0:
        A[i, i-1] = v_ref[i-1] / L
    A[i, i + num_sections] = EA / L - T_ref[i] / L
    if i > 0:
        A[i, i-1 + num_sections] = -EA / L + T_ref[i-1] / L

# Velocity dynamics: dv_i/dt = (R^2/J)*(T_{i+1} - T_i) + (R/J)*u_i - (fb/J)*v_i
for i in range(num_sections):
    A[i + num_sections, i] = -R**2 / J
    if i < num_sections - 1:
        A[i + num_sections, i + 1] = R**2 / J
    A[i + num_sections, i + num_sections] = -fb / J
    B[i + num_sections, i] = R / J

# Save the A and B matrices to a JSON file
controller_params = {
    'A_matrix': A.tolist(),
    'B_matrix': B.tolist()
}

with open('controller_params.json', 'w') as f:
    json.dump(controller_params, f, indent=4)
