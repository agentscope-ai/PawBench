import datetime
from scripts.password_generator_example import generate_daily_password, verify_password, get_password_components

# Debug print to ensure the date constructor is working
print(datetime.date(2025, 1, 1))

test_dates = [
    (datetime.date(2025, 1, 1), 'Wednesday', '01013'),
    (datetime.date(2025, 2, 28), 'Friday', '02285'),
    (datetime.date(2025, 3, 15), 'Saturday', '03156'),
    (datetime.date(2025, 4, 20), 'Sunday', '04207'),
    (datetime.date(2025, 5, 17), 'Saturday', '05176'),
    (datetime.date(2025, 6, 1), 'Sunday', '06017'),
    (datetime.date(2025, 7, 4), 'Friday', '07045'),
    (datetime.date(2025, 8, 23), 'Saturday', '08236'),
    (datetime.date(2025, 9, 9), 'Tuesday', '09092'),
    (datetime.date(2025, 10, 13), 'Monday', '10131'),
    (datetime.date(2025, 11, 30), 'Sunday', '11307'),
    (datetime.date(2025, 12, 25), 'Thursday', '12254')
]

for test_date, expected_day, expected_password in test_dates:
    components = get_password_components(test_date)
    generated = generate_daily_password(target_date=test_date)
    verified = verify_password(expected_password, target_date=test_date)

    status = 'PASS' if (generated == expected_password and verified) else 'FAIL'
    print(f'  [{status}] {test_date.isoformat()} ({expected_day:9s}) -> MMDD={components["mmdd"]} + WD={components["weekday_digit"]} = {generated} (expected: {expected_password})')