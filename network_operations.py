import asyncio
import re
import asyncssh

PROMPT_RE = re.compile(b'\S+[>#:$]\s*$')

async def read_until_prompt(stream, timeout=40):
    """
    Reads from a stream until a prompt is detected or a timeout occurs.
    """
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
        full_output += b"\n*** TIMEOUT: Waited too long for a prompt. ***\n"
    
    return full_output.decode('utf-8', errors='ignore')

async def execute_telnet_async(node_info, log_file_path):
    """
    Connects to a node using Telnet, handles negotiation, executes commands,
    and writes output to a log file.
    """
    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        try:
            log_file.write(f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via Telnet ---\n")
            log_file.flush()
            
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(node_info['ip_address'], 23),
                timeout=10
            )

            # --- Basic Telnet Negotiation ---
            IAC, DONT, DO, WONT, WILL = b'\xff', b'\xfe', b'\xfd', b'\xfc', b'\xfb'
            buffer = b''
            negotiation_attempts = 0
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

            log_file.write(f"--- Sending login ID: {node_info['login_id']} ---\n")
            log_file.flush()
            writer.write(node_info['login_id'].encode('ascii') + b"\r\n")
            await writer.drain()

            # Password prompt detection
            buffer = b""
            prompt_found = False
            log_file.write("--- Waiting for password prompt ---\n")
            # Increased loop range for a total timeout of 15 seconds (30 * 0.5s)
            for _ in range(30):
                try:
                    chunk = await asyncio.wait_for(reader.read(100), timeout=0.5)
                    if not chunk:
                        # If connection closes, stop trying
                        break
                    buffer += chunk.replace(b'\x00', b'') # Clean null bytes
                    
                    # More robust check: decode with error handling, then check lowercased string.
                    if 'password:' in buffer.decode('utf-8', errors='ignore').lower():
                        prompt_found = True
                        break
                except asyncio.TimeoutError:
                    # No data received in this interval, continue waiting
                    pass

            log_file.write(f"Received: {buffer.decode(errors='ignore')}\n")
            log_file.flush()
            if not prompt_found:
                raise asyncio.TimeoutError("Timeout waiting for password prompt.")

            log_file.write("--- Sending password ---\n")
            log_file.flush()
            writer.write(node_info['login_password'].encode('ascii') + b"\r\n")
            await writer.drain()

            initial_output = await read_until_prompt(reader)
            log_file.write(initial_output)
            log_file.flush()

            if node_info.get('additional_command_1'):
                cmd = node_info['additional_command_1']
                writer.write(cmd.encode('ascii') + b'\r\n')
                await writer.drain()
                response = await read_until_prompt(reader)
                log_file.write(response)
                log_file.flush()
                
                if node_info.get('additional_command_2') and ":" in response:
                    cmd2 = node_info['additional_command_2']
                    writer.write(cmd2.encode('ascii') + b'\r\n')
                    await writer.drain()
                    response2 = await read_until_prompt(reader)
                    log_file.write(response2)
                    log_file.flush()

            for cmd in node_info['commands']:
                writer.write(cmd.encode('ascii') + b'\r\n')
                await writer.drain()
                response = await read_until_prompt(reader)
                log_file.write(response)
                log_file.flush()

            writer.write(b"exit\r\n")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except AttributeError:
                pass # For Python < 3.7 compatibility
            log_file.write("\n--- Disconnected ---")
            log_file.flush()
            return log_file_path

        except Exception as e:
            error_message = f"\n*** ERROR: Failed to connect or execute commands on {node_info['nodename']}. Reason: {e} ***\n"
            log_file.write(error_message)
            log_file.flush()
            return None


async def execute_ssh_async(node_info, log_file_path):
    """
    Connects to a node using asyncssh, executes commands,
    and writes output to a log file.
    """
    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        try:
            log_file.write(f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via SSH ---\n")
            log_file.flush()
            
            async with asyncssh.connect(
                node_info['ip_address'],
                username=node_info['login_id'],
                password=node_info['login_password'],
                known_hosts=None
            ) as conn:
                async with conn.create_process(term_type='vt100', encoding=None) as process:
                    initial_output = await read_until_prompt(process.stdout)
                    log_file.write(initial_output)
                    log_file.flush()

                    if node_info.get('additional_command_1'):
                        cmd = node_info['additional_command_1']
                        process.stdin.write((cmd + '\n').encode('utf-8'))
                        response = await read_until_prompt(process.stdout)
                        log_file.write(response)
                        log_file.flush()

                        if node_info.get('additional_command_2') and ":" in response:
                            cmd2 = node_info['additional_command_2']
                            process.stdin.write((cmd2 + '\n').encode('utf-8'))
                            response2 = await read_until_prompt(process.stdout)
                            log_file.write(response2)
                            log_file.flush()

                    for cmd in node_info['commands']:
                        process.stdin.write((cmd + '\n').encode('utf-8'))
                        response = await read_until_prompt(process.stdout)
                        log_file.write(response)
                        log_file.flush()
                    
                    process.stdin.write(b'exit\n')
                    await process.wait()

            log_file.write("\n--- Disconnected ---")
            log_file.flush()
            return log_file_path

        except Exception as e:
            error_message = f"\n*** ERROR: Failed to connect or execute commands on {node_info['nodename']}. Reason: {e} ***\n"
            log_file.write(error_message)
            log_file.flush()
            return None