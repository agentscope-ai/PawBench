# Skill: Diagnose Scheduled Notification Failure

## Description
This skill helps diagnose issues when a scheduled notification (e.g., daily book recommendation) fails to go out. It checks for common causes such as missing schedulers, misconfigured scripts, and other environmental issues.

## Usage
Invoke this skill when a scheduled notification fails and you need to investigate the cause.

## Steps
1. Check if the environment has `crontab` or `systemctl` available.
2. Look for logs or alternative scheduling mechanisms.
3. Create a diagnosis report with findings and recommendations.
4. If needed, create a placeholder script for the notification and provide instructions for scheduling it.

## Example
```bash
/skill diagnose_scheduled_notification_failure
```

## Dependencies
- `crontab` or `systemctl` for checking system-level scheduled tasks.
- Access to relevant logs and scripts.