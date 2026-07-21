import sqlite3
import json

# Connect to the database
with sqlite3.connect('file:fixtures/test.db?mode=ro', uri=True) as db:
    cursor = db.cursor()
    cursor.execute('SELECT * FROM items ORDER BY id;')
    records = cursor.fetchall()

# Export the records to JSON
with open('output/recovered.json', 'w') as f:
    json.dump([{'id': r[0], 'name': r[1], 'value': r[2]} for r in records], f, indent=4)

# Print the number of exported records
print(f'Exported {len(records)} records to output/recovered.json')
