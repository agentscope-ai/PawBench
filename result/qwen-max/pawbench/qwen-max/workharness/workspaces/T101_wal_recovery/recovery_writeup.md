# Recovery Writeup

## Corruption Issue
The WAL file `test.db-wal` was found to be missing. Without the WAL file, we cannot recover the additional 6 records and the updates to the existing 2 records.

## Fix
The script read and exported the 5 base records from the `test.db` file. The database now contains only these 5 records, as the WAL file is not available.

## Exported Data
The 5 base records have been exported to `output/recovered.json`.