All scripts in this project are intended to run on Linux (specifically CentOS 7) in a command-line interface (CLI) only environment.

Do not start to rewrite code unless the user says so.

- The target environment runs Python 3.11.
- The `pandas` and `asyncssh` libraries are confirmed to be installed on the target Linux machine.
- The `get_pdkey` function's interactive input will not be an issue in the intended environment.
- The `PDRIVE` variable is a placeholder and will be manually updated on the target system.

## Future Implementation Notes (In Order of Priority)

1.  **Robust Locale/Encoding Setting (Priority: High):**
    *   **File:** `run_cycle.py`
    *   **Description:** While `os.environ['LANG']` has been moved to the top of the script, it should be paired with Python's `locale` module (`locale.setlocale(locale.LC_ALL, '')`) to ensure all parts of the script and its libraries reliably adopt the UTF-8 setting. This is the most robust way to prevent encoding errors.

2.  **One-Time Setup Commands (Priority: Medium):**
    *   **Files:** `network_operations.py`, `config_parsers.py`
    *   **Description:** Implement a feature to run specific commands (e.g., `terminal length 0`) only once after a successful login, not in every loop during cycle mode. This requires adding logic to the parser to identify these commands and modifying the network functions to execute them outside the main cycle loop.

3.  **Code Cleanup (Priority: Low):**
    *   **File:** `run_cycle.py`
    *   **Description:** Address minor code quality issues. This includes defining the `PDRIVE` variable in a single location to avoid duplication and adding a warning message for nodes with an unsupported protocol in cycle mode (currently, they fail silently).

4.  **Full Logout Message Logging (Priority: Very Low):**
    *   **File:** `network_operations.py`
    *   **Description:** The final confirmation messages sent by a server during the logout process (e.g., "Connection closed") are currently read but not written to the log file. This is a minor logging improvement.
