## Server Workspace Audit Skill

### Description
This skill provides a repeatable procedure for auditing a web server workspace. It inventories hosted sites, checks SSL certificate expiry, identifies pending config changes, and flags security issues.

### Inputs
- `site_data_path`: Path to the JSON file containing site data (e.g., `www/server/panel/data/site.json`).
- `config_root`: Root directory where the server configurations are stored.
- `output_path`: Path to the JSON file where the audit results will be written (e.g., `server_audit.json`).
- `state_output_path`: Path to the JSON file where the current server state will be saved (e.g., `.openclaw/workspace-state.json`).

### Outputs
- A JSON file at the specified `output_path` with the following structure:
  - `hosted_sites`: List of all hosted sites.
  - `ssl_certificates`: Information about SSL certificates including expiry status.
  - `pending_configs`: Details on any `.new` config files and what they change compared to the current configs.
  - `security_issues`: Any credentials or security issues found in config files.
- A JSON file at the specified `state_output_path` with the current server state.

### Steps
1. Inventory all hosted sites from the provided `site_data_path`.
2. Check SSL certificate expiry for each site.
3. Identify any `.new` config files and compare them to the current configs.
4. Search for any credentials or security issues in the config files.
5. Write the audit results to `output_path` and the current server state to `state_output_path`.