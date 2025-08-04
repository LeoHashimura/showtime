All scripts in this project are intended to run on Linux (specifically CentOS 7) in a command-line interface (CLI) only environment.

Do not start to rewrite code unless the user says so.

- The target environment runs Python 3.11.
- The `pandas` and `asyncssh` libraries are confirmed to be installed on the target Linux machine.
- The `get_pdkey` function's interactive input will not be an issue in the intended environment.
- The `PDRIVE` variable is a placeholder and will be manually updated on the target system.

## Future Implementation Notes

- **One-Time Setup Commands:** In cycle mode, `additional_command_1` and `additional_command_2` should be treated as one-time setup commands (e.g., `terminal length 0`) that run only once after a successful login, not in every cycle loop. This will require modifying `network_operations.py` to accept a new `setup_commands` parameter in the `execute_ssh_async` and `execute_telnet_async` functions.