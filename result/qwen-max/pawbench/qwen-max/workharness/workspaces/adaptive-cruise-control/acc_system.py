import yaml
from pid_controller import PIDController

class AdaptiveCruiseControl:
    def __init__(self, config):
        self.set_speed = config['acc_settings']['set_speed']
        self.time_headway = config['acc_settings']['time_headway']
        self.min_gap = config['acc_settings']['min_gap']
        self.emergency_ttc_threshold = config['acc_settings']['emergency_ttc_threshold']
        self.acceleration_limits = config['vehicle_specs']['acceleration_limits']

        # Initialize PID controllers for speed and distance
        self.pid_speed = PIDController(
            config['pid_gains']['speed']['kp'],
            config['pid_gains']['speed']['ki'],
            config['pid_gains']['speed']['kd']
        )
        self.pid_distance = PIDController(
            config['pid_gains']['distance']['kp'],
            config['pid_gains']['distance']['ki'],
            config['pid_gains']['distance']['kd']
        )

    def compute(self, ego_speed, lead_speed, distance, dt):
        if lead_speed is None:
            # No lead vehicle, cruise at set speed
            speed_error = self.set_speed - ego_speed
            acceleration_cmd = self.pid_speed.compute(speed_error, dt)
            mode = 'cruise'
            distance_error = None
        else:
            # Lead vehicle present
            ttc = distance / (ego_speed - lead_speed) if ego_speed > lead_speed else float('inf')
            desired_distance = self.time_headway * ego_speed + self.min_gap
            distance_error = desired_distance - distance

            if ttc < self.emergency_ttc_threshold:
                # Emergency braking
                acceleration_cmd = self.acceleration_limits[0]
                mode = 'emergency'
            else:
                # Follow lead vehicle
                acceleration_cmd = self.pid_distance.compute(distance_error, dt)
                mode = 'follow'

        # Clamp acceleration command to limits
        acceleration_cmd = max(min(acceleration_cmd, self.acceleration_limits[1]), self.acceleration_limits[0])

        return acceleration_cmd, mode, distance_error