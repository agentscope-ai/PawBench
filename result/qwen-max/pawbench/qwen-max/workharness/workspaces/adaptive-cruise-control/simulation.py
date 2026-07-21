import csv
import yaml
from acc_system import AdaptiveCruiseControl

def load_config(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

def load_sensor_data(file_path):
    with open(file_path, 'r') as file:
        reader = csv.DictReader(file)
        return list(reader)

def save_simulation_results(file_path, results):
    with open(file_path, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['time', 'ego_speed', 'acceleration_cmd', 'mode', 'distance_error', 'distance', 'ttc'])
        writer.writerows(results)

def main():
    # Load configurations and sensor data
    config = load_config('vehicle_params.yaml')
    tuning_results = load_config('tuning_results.yaml')
    config['pid_gains'] = tuning_results
    sensor_data = load_sensor_data('sensor_data.csv')

    # Initialize ACC system
    acc = AdaptiveCruiseControl(config)

    # Initialize simulation variables
    ego_speed = 0.0
    results = []

    # Run simulation
    for i, row in enumerate(sensor_data):
        time = float(row['time'])
        lead_speed = float(row['lead_speed']) if row['lead_speed'] != '' else None
        distance = float(row['distance'])
        dt = 0.1  # Timestep

        # Compute ACC command
        acceleration_cmd, mode, distance_error = acc.compute(ego_speed, lead_speed, distance, dt)

        # Update ego speed
        ego_speed += acceleration_cmd * dt

        # Calculate TTC
        ttc = distance / (ego_speed - lead_speed) if ego_speed > lead_speed else float('inf')

        # Store results
        results.append([time, ego_speed, acceleration_cmd, mode, distance_error, distance, ttc])

    # Save results
    save_simulation_results('simulation_results.csv', results)

if __name__ == '__main__':
    main()