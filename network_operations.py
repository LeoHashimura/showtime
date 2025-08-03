import asyncio
import re
import asyncssh

# Custom exception for prompt timeouts
class PromptTimeoutError(Exception):
    pass

# Custom exception for logout failures
class LogoutFailedError(Exception):
    pass

PROMPT_RE = re.compile(b'\S+[>#$]\s*$')

async def _update_status(queue, node_name, status, message=""):
    """Helper to safely put status updates on the queue."""
    if queue:
        await queue.put({'node': node_name, 'status': status, 'message': message})

async def read_until_prompt(stream, timeout=40):
    full_output = b""
    try:
        while True:
            chunk = await asyncio.wait_for(stream.read(1024), timeout=timeout)
            if not chunk:
                # Connection closed, return what we have
                break
            full_output += chunk
            non_empty_lines = [line for line in full_output.splitlines() if line.strip()]
            if non_empty_lines and PROMPT_RE.search(non_empty_lines[-1]):
                break
    except asyncio.TimeoutError:
        # Raise our custom exception instead of returning a message
        raise PromptTimeoutError(f"Timeout waiting for prompt after {timeout} seconds.")
    
    return full_output.decode('utf-8', errors='ignore')

async def execute_telnet_async(node_info, log_file_path, status_queue=None):
    node_name = node_info['nodename']
    writer = None
    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        try:
            await _update_status(status_queue, node_name, 'connecting')
            log_file.write(f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via Telnet ---\n")
            log_file.flush()
            
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(node_info['ip_address'], 23),
                timeout=10
            )

            # --- Basic Telnet Negotiation and Login (omitted for brevity) ---
            # ... (The existing login logic remains here) ...

            # --- Command Execution ---
            for cmd in node_info['commands']:
                writer.write(cmd.encode('ascii') + b'\r\n')
                await writer.drain()
                response = await read_until_prompt(reader)
                log_file.write(response)
                log_file.flush()

            # --- New Robust Logout Procedure ---
            logout_attempts = ['exit', 'logout']
            for attempt in logout_attempts:
                log_file.write(f"--- Attempting logout with '{attempt}' ---\n")
                writer.write(attempt.encode('ascii') + b'\r\n')
                await writer.drain()
                
                try:
                    data = await asyncio.wait_for(reader.read(1024), timeout=5.0)
                    if not data:
                        log_file.write("--- Server closed connection. Logout successful. ---\n")
                        await _update_status(status_queue, node_name, 'success')
                        return log_file_path
                except asyncio.TimeoutError:
                    continue

            raise LogoutFailedError("Failed to disconnect from server after sending exit/logout.")

        except (asyncio.TimeoutError, PromptTimeoutError, LogoutFailedError):
            raise
        except Exception as e:
            error_message = f"An unexpected error occurred during Telnet: {e}"
            await _update_status(status_queue, node_name, 'error', error_message)
            log_file.write(f"\n*** ERROR: {error_message} ***\n")
            return None
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except AttributeError:
                    pass

async def execute_ssh_async(node_info, log_file_path, status_queue=None):
    node_name = node_info['nodename']
    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        try:
            await _update_status(status_queue, node_name, 'connecting')
            log_file.write(f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via SSH ---\n")
            log_file.flush()
            
            async with asyncssh.connect(
                node_info['ip_address'],
                username=node_info['login_id'],
                password=node_info['login_password'],
                known_hosts=None
            ) as conn:
                await _update_status(status_queue, node_name, 'authenticating')
                async with conn.create_process(term_type='vt100', encoding=None) as process:
                    initial_output = await read_until_prompt(process.stdout)
                    log_file.write(initial_output)
                    log_file.flush()

                    await _update_status(status_queue, node_name, 'executing_commands')
                    for cmd in node_info['commands']:
                        process.stdin.write((cmd + '\n').encode('utf-8'))
                        response = await read_until_prompt(process.stdout)
                        log_file.write(response)
                        log_file.flush()
                    
                    # --- New Robust Logout Procedure ---
                    logout_attempts = ['exit', 'logout']
                    for attempt in logout_attempts:
                        log_file.write(f"--- Attempting logout with '{attempt}' ---\n")
                        process.stdin.write((attempt + '\n').encode('utf-8'))
                        try:
                            response = await asyncio.wait_for(process.stdout.read(1024), timeout=5.0)
                            if not response:
                                log_file.write("--- Server closed connection. Logout successful. ---\n")
                                await _update_status(status_queue, node_name, 'success')
                                return log_file_path
                        except asyncio.TimeoutError:
                            continue

                    raise LogoutFailedError("Failed to disconnect from server after sending exit/logout.")

        except (asyncio.TimeoutError, PromptTimeoutError, LogoutFailedError):
            raise
        except asyncssh.Error as e:
            error_message = f"SSH connection failed: {e}"
            await _update_status(status_queue, node_name, 'error', error_message)
            log_file.write(f"\n*** SSH ERROR: {error_message} ***\n")
            return None
        except Exception as e:
            error_message = f"An unexpected error occurred during SSH: {e}"
            await _update_status(status_queue, node_name, 'error', error_message)
            log_file.write(f"\n*** ERROR: {error_message} ***\n")
            return None
