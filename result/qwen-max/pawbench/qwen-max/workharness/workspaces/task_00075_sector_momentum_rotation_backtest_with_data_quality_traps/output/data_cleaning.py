import csv
from datetime import datetime
import os

# Function to adjust prices for the 2:1 split on 2024-05-24 for XLK
def adjust_for_split(row, split_date, split_ratio):
    if datetime.strptime(row[0], '%Y-%m-%d').date() >= split_date:
        row[2] = str(float(row[2]) / split_ratio)
        row[3] = str(float(row[3]) / split_ratio)
        row[4] = str(float(row[4]) / split_ratio)
        row[5] = str(float(row[5]) / split_ratio)
    return row

# Read the original CSV file
def clean_data(input_file, output_file):
    with open(input_file, 'r') as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [header] + list(reader)

    # Remove duplicates
    seen_dates = set()
    unique_rows = []
    for row in rows:
        date_ticker = (row[0], row[1])
        if date_ticker not in seen_dates:
            seen_dates.add(date_ticker)
            unique_rows.append(row)

    # Adjust for the XLK split
    split_date = datetime(2024, 5, 24).date()
    split_ratio = 2.0
    for i, row in enumerate(unique_rows):
        if row[1] == 'XLK':
            unique_rows[i] = adjust_for_split(row, split_date, split_ratio)

    # Perform linear interpolation for missing data
    # Assuming the data is sorted by date, we can interpolate between known values
    for i in range(1, len(unique_rows) - 1):
        if unique_rows[i][0] != unique_rows[i-1][0]:
            for j in range(2, 6):  # Columns for open, high, low, close
                if unique_rows[i][j] == '' or unique_rows[i][j] == 'nan':
                    unique_rows[i][j] = str((float(unique_rows[i-1][j]) + float(unique_rows[i+1][j])) / 2)

    # Write the cleaned data to the output file
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(unique_rows)

if __name__ == '__main__':
    input_file = 'data/sector_prices.csv'
    output_file = 'data/sector_prices_cleaned.csv'
    clean_data(input_file, output_file)
