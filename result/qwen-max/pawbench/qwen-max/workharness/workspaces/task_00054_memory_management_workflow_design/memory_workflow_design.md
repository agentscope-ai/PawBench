## Audit of the Current State

### Configuration Files
- **`./config/assistant_config.yaml`**:
  - Memory backend is set to `local_json`.
  - Maximum memory items are set to 500.
  - Retention days are set to 90.
  - Cleanup schedule is set to `weekly`.
  - Storage path for memories is `./data/memories.json`.
  - The embedding model is `text-embedding-ada-002`, and vector DB is not enabled.
  - Similarity threshold for embeddings is 0.85.

- **`./config/cron_schedules.ini`**:
  - Weekly cleanup (`memory_cleanup`) is scheduled at 2 AM every Sunday.
  - Summarization (`memory_summary`) is disabled.
  - Health check (`health_check`) runs every 30 minutes.
  - Daily backup (`backup`) is scheduled at 3 AM every day.

- **`./config/embedding_config.yaml`**:
  - Embedding model is `text-embedding-ada-001`.
  - Vector dimensions are 1024.
  - Batch size is 64.
  - Cache TTL is 168 hours (7 days).
  - Similarity threshold is 0.85, and the index type is `flat`.

- **`./config/storage_limits.json`**:
  - Max memory items: 5000.
  - Retention days: 30.
  - Max recurring items: 50.
  - Max ad-hoc items: 200.
  - Storage quota: 256 MB.
  - Warning threshold: 80%.
  - Critical threshold: 95%.

### Data Files
- **`./data/memories.json`**:
  - Contains a list of memory items with fields such as `id`, `type`, `content`, `created_at`, `last_accessed`, `priority`, `tags`, and `expired`.
  - Some items are marked as `expired` but still present in the file.
  - There are both `recurring` and `ad_hoc` types of items.

- **`./data/memory_categories.json`**:
  - Defines categories and subcategories with default retention days and priority weights.
  - Categories include `work`, `personal`, `reference`, and `temporary`.

- **`./data/recurring_schedule.csv`**:
  - Lists recurring items with their frequency, time of day, and category.
  - Some items are marked as inactive (`active: false`).

- **`./data/user_feedback.csv`**:
  - Contains user feedback, including issues with outdated information, duplicates, and missing summarization.

### Documentation
- **`./docs/memory_architecture_v1.md`**:
  - Describes the initial architecture, which uses a flat JSON file for storage.
  - Highlights limitations such as no deduplication, no summarization, and no distinction between recurring and ad-hoc items.

- **`./docs/user_requirements.md`**:
  - Outlines user requirements, including support for recurring items, configurable retention policies, duplicate detection, and summarization.

### Scripts
- **`./scripts/cleanup_pseudocode.txt`**:
  - Pseudocode for the cleanup script, which identifies and removes expired memories.
  - Known issues include no deduplication, no summarization, and a race condition during cleanup.

## Diagnosis of Issues

### Inconsistent Configurations
- **Retention Days**:
  - `assistant_config.yaml` sets retention days to 90.
  - `storage_limits.json` sets retention days to 30.
  - **Resolution**: Use the value from `storage_limits.json` (30 days) as it is more recent and aligns with the user requirements for shorter retention periods.

- **Max Memory Items**:
  - `assistant_config.yaml` sets max memory items to 500.
  - `storage_limits.json` sets max memory items to 5000.
  - **Resolution**: Use the value from `storage_limits.json` (5000) as it is more recent and aligns with the user requirements for handling more items.

- **Embedding Model**:
  - `assistant_config.yaml` uses `text-embedding-ada-002`.
  - `embedding_config.yaml` uses `text-embedding-ada-001`.
  - **Resolution**: Use the value from `embedding_config.yaml` (text-embedding-ada-001) as it is more detailed and specific.

### Issues with the Memory System
- **Outdated Information**:
  - Memories are not being updated or refreshed, leading to stale content.
  - **Solution**: Implement a periodic review process to refresh or update memories.

- **Duplicates**:
  - No deduplication logic exists, leading to multiple entries for the same information.
  - **Solution**: Add a deduplication step using semantic similarity (embeddings) before storing new memories.

- **Missed Cleanups**:
  - Some expired memories are not being removed, possibly due to the race condition in the cleanup script.
  - **Solution**: Address the race condition by using a more robust method to handle concurrent writes.

- **No Summarization**:
  - Important information is lost when memories expire without summarization.
  - **Solution**: Implement a summarization step before deleting expired memories.

## Redesigned Workflow

### Memory Management
- **Recurring vs. Ad-Hoc Items**:
  - Recurring items should persist indefinitely unless explicitly removed.
  - Ad-hoc items should follow the configurable retention policy based on category and priority.

- **Retention Policies**:
  - Default retention periods per category.
  - Override capability per individual memory.
  - Recurring items exempt from automatic expiry.

- **Deduplication**:
  - Use embeddings to detect and merge similar memories.
  - Prompt the user before merging, or auto-merge if confidence is high.

- **Summarization**:
  - Generate summaries of old memories before deletion.
  - Summaries should preserve key facts and dates.

- **Categorization and Search**:
  - Auto-categorize memories using predefined taxonomy.
  - Full-text search across memory content.
  - Tag-based filtering.
  - Semantic search via embeddings.

### Scheduling and Cleanup
- **Cleanup Schedule**:
  - Run cleanup weekly, as specified in `cron_schedules.ini`.
  - Ensure the cleanup script handles concurrent writes and does not lose new memories.

- **Health Check**:
  - Run a daily self-check to verify memory store integrity, detect anomalies, and report on storage health.
  - Check for corrupted entries, orphaned references, and storage limits.

### Self-Check Procedures
- **Automatic Checks**:
  - **Frequency**: Daily.
  - **Checks**:
    - Verify memory store integrity.
    - Detect and log any corrupted entries.
    - Check for orphaned references.
    - Monitor storage usage and alert if approaching the warning or critical thresholds.
    - Validate that scheduled jobs (cleanup, summarization, health check) are executing as intended.

- **Alert Conditions**:
  - Approaching storage limits (warning at 80%, critical at 95%).
  - Failed cleanups or detected near-duplicates.
  - Detected data corruption or anomalies.

### Implementation Plan
- **Update Configuration Files**:
  - Align `assistant_config.yaml` with `storage_limits.json` for retention days and max memory items.
  - Use the embedding model from `embedding_config.yaml`.

- **Implement Deduplication and Summarization**:
  - Add deduplication logic using embeddings.
  - Implement summarization before deleting expired memories.

- **Enhance Cleanup Script**:
  - Address the race condition to handle concurrent writes.
  - Ensure the cleanup script respects category-specific retention policies.

- **Add Self-Check Mechanism**:
  - Implement a daily self-check to monitor the health of the memory system.
  - Set up alerts for critical conditions.