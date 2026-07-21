# Migration Write-Up

## Data Quality Fixes Applied

- **Accounts Table**:
  - If an email is missing, it is set to 'unknown@example.com'.
  - The `role_id` is set to 1 (active) by default.
  - If `created_at` is missing, the current timestamp is used.

- **Profiles Table**:
  - If `full_name` is missing, `display_name` is set to 'Anonymous'.
  - `bio` is left empty as there is no corresponding field in the old schema.

- **Products Table**:
  - If `description` is missing, it is set to an empty string.
  - `price_cents` is converted from the real number `price` to cents.
  - If `category` is missing, it is set to 'general'.

- **Orders Table**:
  - `quantity` is ensured to be greater than 0 through the `CHECK` constraint.
  - `total_price_cents` is calculated from the `total_price` by converting it to cents.
  - `status_code` is mapped from the text `status` to an integer code.
  - If `ordered_at` is missing, the current timestamp is used.

- **Reviews Table**:
  - `rating` is clamped between 1 and 5.
  - If `comment` is missing, it is set to an empty string.
  - If `reviewed_at` is missing, the current timestamp is used.

- **Activity Log Table**:
  - This table is a direct copy of the `audit_log` table with no changes needed.

The migration script ensures that all data is transformed to match the new schema exactly while preserving the integrity and quality of the data.