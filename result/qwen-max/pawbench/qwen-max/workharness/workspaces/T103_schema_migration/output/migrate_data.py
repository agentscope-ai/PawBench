import sqlite3
from datetime import datetime

# Connect to the old and new databases
old_db = sqlite3.connect('fixtures/test_data.db')
new_db = sqlite3.connect('output/migrated.db')

# Create a cursor object using the cursor() method
old_cursor = old_db.cursor()
new_cursor = new_db.cursor()

# Create tables in the new database according to the new schema
new_cursor.execute('''CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    role_id INTEGER NOT NULL DEFAULT 0,   -- 0=pending, 1=active, 2=admin, 3=inactive
    created_at TEXT NOT NULL
)''')

new_cursor.execute('''CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    display_name TEXT NOT NULL DEFAULT 'Anonymous',
    bio TEXT DEFAULT ''
)''')

new_cursor.execute('''CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    price_cents INTEGER NOT NULL,    -- price in cents (e.g., 2999)
    category TEXT NOT NULL DEFAULT 'general'
)''')

new_cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    total_price_cents INTEGER NOT NULL,
    status_code INTEGER NOT NULL DEFAULT 0,  -- 0=pending, 1=shipped, 2=delivered, 3=cancelled
    ordered_at TEXT NOT NULL
)''')

new_cursor.execute('''CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    rating INTEGER NOT NULL DEFAULT 3 CHECK(rating BETWEEN 1 AND 5),
    comment TEXT DEFAULT '',
    reviewed_at TEXT NOT NULL
)''')

new_cursor.execute('''CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    action TEXT NOT NULL,
    details TEXT DEFAULT '',
    logged_at TEXT NOT NULL
)''')

# Migrate data from the old schema to the new schema
# Accounts table - assuming all users are active by default
old_cursor.execute('SELECT * FROM users')
for row in old_cursor.fetchall():
    new_cursor.execute('INSERT INTO accounts (id, email, role_id, created_at) VALUES (?, ?, 1, ?)',
                      (row[0], row[1] or 'unknown@example.com', (row[7] if len(row) > 7 else None) or datetime.now().isoformat()))

# Profiles table - using full_name as display_name, if available
old_cursor.execute('SELECT * FROM users')
for row in old_cursor.fetchall():
    new_cursor.execute('INSERT INTO profiles (id, account_id, display_name, bio) VALUES (?, ?, ?, ?)',
                      (row[0], row[0], row[2] or 'Anonymous', ''))

# Products table - converting price to cents
old_cursor.execute('SELECT * FROM products')
for row in old_cursor.fetchall():
    new_cursor.execute('INSERT INTO products (id, name, description, price_cents, category) VALUES (?, ?, ?, ?, ?)',
                      (row[0], row[1], row[2] or '', int(row[3] * 100) if row[3] else 0, row[4] or 'general'))

# Orders table - calculating total_price_cents and setting status_code based on status
old_cursor.execute('SELECT * FROM orders')
for row in old_cursor.fetchall():
    # Assuming status codes: 0=pending, 1=shipped, 2=delivered, 3=cancelled
    status_code = {'pending': 0, 'shipped': 1, 'delivered': 2, 'cancelled': 3}.get(str(row[4]).lower(), 0)
    total_price_cents = (int(row[22] * 100) if len(row) > 22 and row[22] is not None else 0)
    new_cursor.execute('INSERT INTO orders (id, account_id, product_id, quantity, total_price_cents, status_code, ordered_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (row[0], row[1], row[2], max(1, row[3]), total_price_cents, status_code, (row[25] if len(row) > 25 else None) or datetime.now().isoformat()))

# Reviews table - ensuring rating is between 1 and 5
old_cursor.execute('SELECT * FROM reviews')
for row in old_cursor.fetchall():
    rating = max(1, min(5, row[3] or 3))
    new_cursor.execute('INSERT INTO reviews (id, account_id, product_id, rating, comment, reviewed_at) VALUES (?, ?, ?, ?, ?, ?)',
                      (row[0], row[1], row[2], rating, row[3] or '', (row[34] if len(row) > 34 else None) or datetime.now().isoformat()))

# Activity log - renamed from audit_log, assuming no changes needed
old_cursor.execute('SELECT * FROM audit_log')
for row in old_cursor.fetchall():
    new_cursor.execute('INSERT INTO activity_log (id, account_id, action, details, logged_at) VALUES (?, ?, ?, ?, ?)',
                      (row[0], row[1], row[2], row[3], row[4] or datetime.now().isoformat()))

# Commit the transaction and close the connections
new_db.commit()
old_db.close()
new_db.close()