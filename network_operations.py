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
                break
            full_output += chunk
            non_empty_lines = [line for line in full_output.splitlines() if line.strip()]
            if non_empty_lines and PROMPT_RE.search(non_empty_lines[-1]):
                break
    except asyncio.TimeoutError:
        raise PromptTimeoutError(f"Timeout waiting for prompt after {timeout} seconds.")
    
    return full_output.decode('utf-8', errors='ignore')

async def execute_telnet_async(node_info, log_file_path, status_queue=None, cycle_interval=-1, stop_event=None):
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

            # --- Full Telnet Login Sequence ---
            IAC, DONT, DO, WONT, WILL = b'\xff', b'\xfe', b'\xfd', b'\xfc', b'\xfb'
            buffer = b''
            negotiation_attempts = 0
            await _update_status(status_queue, node_name, 'authenticating')
            while negotiation_attempts < 10:
                try:
                    data = await asyncio.wait_for(reader.read(1024), timeout=0.5)
                    if not data:
                        break
                    response = b''
                    i = 0
                    clean_chunk = b''
                    while i < len(data):
                        if data[i:i+1] == IAC:
                            command = data[i+1:i+2]
                            option = data[i+2:i+3]
                            if command == WILL:
                                response += IAC + DONT + option
                            elif command == DO:
                                response += IAC + WONT + option
                            i += 3
                        else:
                            clean_chunk += data[i:i+1]
                            i += 1
                    if response:
                        writer.write(response)
                        await writer.drain()
                    buffer += clean_chunk
                    if any(p in buffer.lower() for p in [b'username:', b'login:']):
                        break
                except asyncio.TimeoutError:
                    break
                finally:
                    negotiation_attempts += 1

            log_file.write(f"Received: {buffer.decode(errors='ignore')}\n")
            log_file.flush()
            if not any(p in buffer.lower() for p in [b'username:', b'login:']):
                raise asyncio.TimeoutError("Timeout waiting for username/login prompt.")

            writer.write(node_info['login_id'].encode('ascii') + b"\r\n")
            await writer.drain()
            buffer = b""
            prompt_found = False
            for _ in range(30):
                try:
                    chunk = await asyncio.wait_for(reader.read(100), timeout=0.5)
                    if not chunk:
                        break
                    buffer += chunk.replace(b'\x00', b'')
                    if 'password:' in buffer.decode('utf-8', errors='ignore').lower():
                        prompt_found = True
                        break
                except asyncio.TimeoutError:
                    pass
            if not prompt_found:
                raise asyncio.TimeoutError("Timeout waiting for password prompt.")

            writer.write(node_info['login_password'].encode('ascii') + b"\r\n")
            await writer.drain()
            initial_output = await read_until_prompt(reader)
            log_file.write(initial_output)
            log_file.flush()

            # --- Command Execution Logic ---
            async def run_commands():
                await _update_status(status_queue, node_name, 'executing_commands')
                if node_info.get('additional_command_1'):
                    cmd = node_info['additional_command_1']
                    log_file.write(f"--- Sending additional_command_1: {cmd} ---\n")
                    log_file.flush()
                    writer.write(cmd.encode('ascii') + b'\r\n')
                    await writer.drain()
                    response = await read_until_prompt(reader)
                    log_file.write(response)
                    log_file.flush()
                    if node_info.get('additional_command_2') and ":" in response:
                        cmd2 = node_info['additional_command_2']
                        log_file.write(f"--- Sending additional_command_2: {cmd2} ---\n")
                        log_file.flush()
                        writer.write(cmd2.encode('ascii') + b'\r\n')
                        await writer.drain()
                        response2 = await read_until_prompt(reader)
                        log_file.write(response2)
                        log_file.flush()
                for cmd in node_info['commands']:
                    log_file.write(f"--- Sending command: {cmd} ---\n")
                    log_file.flush()
                    writer.write(cmd.encode('ascii') + b'\r\n')
                    await writer.drain()
                    response = await read_until_prompt(reader)
                    log_file.write(response)
                    log_file.flush()

            if cycle_interval == -1:
                # Single run mode for run_automation.py
                await run_commands()
            else:
                # Cycle mode for run_cycle.py
                while not stop_event.is_set():
                    await run_commands()
                    await asyncio.sleep(cycle_interval / 1000)

            # --- Robust Logout Procedure ---
            logout_commands = ['exit', 'logout']
            for command in logout_commands:
                log_file.write(f"--- Attempting logout with '{command}' ---\n")
                writer.write(command.encode('ascii') + b'\r\n')
                await writer.drain()
                for _ in range(5):
                    try:
                        data = await asyncio.wait_for(reader.read(1024), timeout=3.0)
                        if not data:
                            log_file.write("--- Server closed connection. Logout successful. ---\n")
                            await _update_status(status_queue, node_name, 'success')
                            return log_file_path
                    except asyncio.TimeoutError:
                        log_file.write("--- Waiting for connection to close... ---\n")
                        pass
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

async def execute_ssh_async(node_info, log_file_path, status_queue=None, cycle_interval=-1, stop_event=None):
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

                    # --- Command Execution Logic ---
                    async def run_commands():
                        await _update_status(status_queue, node_name, 'executing_commands')
                        if node_info.get('additional_command_1'):
                            cmd = node_info['additional_command_1']
                            log_file.write(f"--- Sending additional_command_1: {cmd} ---\n")
                            log_file.flush()
                            process.stdin.write((cmd + '\n').encode('utf-8'))
                            response = await read_until_prompt(process.stdout)
                            log_file.write(response)
                            log_file.flush()
                            if node_info.get('additional_command_2') and ":" in response:
                                cmd2 = node_info['additional_command_2']
                                log_file.write(f"--- Sending additional_command_2: {cmd2} ---\n")
                                log_file.flush()
                                process.stdin.write((cmd2 + '\n').encode('utf-8'))
                                response2 = await read_until_prompt(process.stdout)
                                log_file.write(response2)
                                log_file.flush()
                        for cmd in node_info['commands']:
                            log_file.write(f"--- Sending command: {cmd} ---\n")
                            log_file.flush()
                            process.stdin.write((cmd + '\n').encode('utf-8'))
                            response = await read_until_prompt(process.stdout)
                            log_file.write(response)
                            log_file.flush()

                    if cycle_interval == -1:
                        # Single run mode for run_automation.py
                        await run_commands()
                    else:
                        # Cycle mode for run_cycle.py
                        while not stop_event.is_set():
                            await run_commands()
                            await asyncio.sleep(cycle_interval / 1000)
                    
                    # --- Robust Logout Procedure ---
                    logout_commands = ['exit', 'logout']
                    for command in logout_commands:
                        log_file.write(f"--- Attempting logout with '{command}' ---\n")
                        process.stdin.write((command + '\n').encode('utf-8'))
                        for _ in range(5):
                            try:
                                response = await asyncio.wait_for(process.stdout.read(1024), timeout=3.0)
                                if not response:
                                    log_file.write("--- Server closed connection. Logout successful. ---\n")
                                    await _update_status(status_queue, node_name, 'success')
                                    return log_file_path
                            except asyncio.TimeoutError:
                                log_file.write("--- Waiting for connection to close... ---\n")
                                pass
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