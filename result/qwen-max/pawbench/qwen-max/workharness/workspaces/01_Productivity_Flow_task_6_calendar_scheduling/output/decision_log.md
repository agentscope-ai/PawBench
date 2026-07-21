### Scheduling Decision Log

### Summary
- Total requests: 8
- Scheduled: 4
- Unscheduled: 4
- Total priority weight achieved: 12

### High Priority Decisions
- `req_008` — Customer Sync could not be scheduled because it conflicts with a higher-priority meeting, Quarterly Planning.
- `req_007` — Skip Level could not be scheduled as Bob has already reached the daily limit of 4 meetings.
- `req_006` — Carol Vacation Coverage could not be scheduled since Carol is unavailable on Fridays.
- `req_004` — Lunch Brainstorm could not be scheduled due to a hard constraint that no meetings are allowed during the lunch break (12:00-13:00).

### Unscheduled Requests
- `req_004` — Overlaps with the lunch break (12:00-13:00)
- `req_006` — Carol is unavailable on Friday
- `req_007` — Daily limit exceeded for Bob
- `req_008` — Lower priority than competing request (Quarterly Planning)