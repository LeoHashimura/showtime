import asyncio
import csv
import os
import re
import sys
import zipfile
from datetime import datetime
from itertools import zip_longest
import asyncssh


def print_progress_bar(iteration, total, prefix='Progress:', suffix='Complete', length=50, fill='█'):
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration == total:
        print()

def parse_nodes_from_csv(file_path):
    """
    Parses the CSV file where each column represents a node.
    Any row after the initial config is treated as a command.
    Stops reading commands for a node when a blank cell is found.
    """
    nodes = []
    config_headers = {
        'nodename', 'protocol', 'ip_address', 'login_id', 
        'login_password', 'additional_command_1', 'additional_command_2'
    }

    try:
        with open(file_path, 'r', newline='', encoding='utf-8-sig') as csvfile:
            reader = list(csv.reader(csvfile))
            if not reader:
                return []
            
            transposed_data = list(zip_longest(*reader, fillvalue=''))
            headers = [h.strip() for h in transposed_data[0]]

            for i in range(1, len(transposed_data)):
                node_info = {"commands": []}
                node_column = transposed_data[i]
                commands_ended = False

                for j, header in enumerate(headers):
                    value = node_column[j].strip() if j < len(node_column) else ""

                    if header not in config_headers:
                        if not value:
                            commands_ended = True
                        
                        if not commands_ended:
                            node_info["commands"].append(value)
                    else:
                        node_info[header] = value
                
                nodes.append(node_info)

    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return None
    except Exception as e:
        print(f"An error occurred while parsing the CSV: {e}")
        return None
    return nodes

PROMPT_RE = re.compile(b'\S+[>#:$]\s*$')

async def read_until_prompt(stream, timeout=20):
    """
    Reads from a stream until a prompt is detected or a timeout occurs.
    The stream can be an asyncio.StreamReader or an asyncssh.SSHClientProcess's stdout.
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
    Connects to a node using Telnet, waits for prompts, executes commands,
    and writes output to a log file in real-time.
    """
    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        try:
            log_file.write(f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via Telnet ---\n")
            log_file.flush()
            
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(node_info['ip_address'], 23),
                timeout=10
            )

            # More robust, case-insensitive login prompt detection
            buffer = b""
            prompt_found = False
            for _ in range(20):  # Max 10 seconds for username/login
                try:
                    chunk = await asyncio.wait_for(reader.read(100), timeout=0.5)
                    if not chunk:
                        raise ConnectionError("Telnet connection closed while waiting for login prompt.")
                    buffer += chunk
                    if any(p in buffer.lower() for p in [b'username:', b'login:']):
                        prompt_found = True
                        break
                except asyncio.TimeoutError:
                    pass  # No data received in this interval, try again.
            
            if not prompt_found:
                raise asyncio.TimeoutError(f"Timeout waiting for username/login prompt. Received: {buffer.decode(errors='ignore')}")

            writer.write(node_info['login_id'].encode('ascii') + b"\n")
            await writer.drain()

            # More robust, case-insensitive password prompt detection
            buffer = b""
            prompt_found = False
            for _ in range(10):  # Max 5 seconds for password
                try:
                    chunk = await asyncio.wait_for(reader.read(100), timeout=0.5)
                    if not chunk:
                        raise ConnectionError("Telnet connection closed while waiting for password prompt.")
                    buffer += chunk
                    if b'password:' in buffer.lower():
                        prompt_found = True
                        break
                except asyncio.TimeoutError:
                    pass  # No data received in this interval, try again.

            if not prompt_found:
                raise asyncio.TimeoutError(f"Timeout waiting for password prompt. Received: {buffer.decode(errors='ignore')}")

            writer.write(node_info['login_password'].encode('ascii') + b"\n")
            await writer.drain()

            initial_output = await read_until_prompt(reader)
            log_file.write(initial_output)
            log_file.flush()
            print(f"\n--- Initial connection to {node_info['nodename']} ---\n{initial_output}\n-------------------------------------")

            if node_info.get('additional_command_1'):
                cmd = node_info['additional_command_1']
                writer.write(cmd.encode('ascii') + b'\n')
                await writer.drain()
                response = await read_until_prompt(reader)
                print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
                log_file.write(response)
                log_file.flush()
                
                if node_info.get('additional_command_2') and ":" in response:
                    cmd2 = node_info['additional_command_2']
                    writer.write(cmd2.encode('ascii') + b'\n')
                    await writer.drain()
                    response2 = await read_until_prompt(reader)
                    print(f"\n--- Output from {node_info['nodename']} after '{cmd2}' ---\n{response2}\n-------------------------------------")
                    log_file.write(response2)
                    log_file.flush()

            for cmd in node_info['commands']:
                writer.write(cmd.encode('ascii') + b'\n')
                await writer.drain()
                response = await read_until_prompt(reader)
                print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
                log_file.write(response)
                log_file.flush()

            writer.write(b"exit\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
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
    Connects to a node using asyncssh, waits for prompts, executes commands,
    and writes output to a log file in real-time.
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
                    print(f"\n--- Initial connection to {node_info['nodename']} ---\n{initial_output}\n-------------------------------------")

                    if node_info.get('additional_command_1'):
                        cmd = node_info['additional_command_1']
                        process.stdin.write((cmd + '\n').encode('utf-8'))
                        response = await read_until_prompt(process.stdout)
                        print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
                        log_file.write(response)
                        log_file.flush()

                        if node_info.get('additional_command_2') and ":" in response:
                            cmd2 = node_info['additional_command_2']
                            process.stdin.write((cmd2 + '\n').encode('utf-8'))
                            response2 = await read_until_prompt(process.stdout)
                            print(f"\n--- Output from {node_info['nodename']} after '{cmd2}' ---\n{response2}\n-------------------------------------")
                            log_file.write(response2)
                            log_file.flush()

                    for cmd in node_info['commands']:
                        process.stdin.write((cmd + '\n').encode('utf-8'))
                        response = await read_until_prompt(process.stdout)
                        print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
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

def create_zip_file(files_to_zip, zip_filename):
    """
    Creates a zip archive containing the specified files.
    """
    try:
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in files_to_zip:
                zf.write(file, os.path.basename(file))
        print(f"\nSuccessfully created zip file: {zip_filename}")
    except Exception as e:
        print(f"\nError: Failed to create zip file. Reason: {e}")

async def main():
    """
    Main asynchronous function to orchestrate the process.
    """
    BASE_NODE_TIMEOUT = 30.0
    SECONDS_PER_COMMAND = 5.0

    if len(sys.argv) > 1:
        if sys.argv[1] in ('-h', '--help'):
            print(f"Usage: python rtrun.py [path_to_your_csv_file]")
            print(f"Base node timeout is {BASE_NODE_TIMEOUT} seconds, plus {SECONDS_PER_COMMAND} seconds per command.")
            return
        csv_file = sys.argv[1]
        print(f"Using specified CSV file: {csv_file}")
    else:
        csv_file = 'nodes.csv'
        print(f"No CSV file specified, defaulting to '{csv_file}'")

    nodes = parse_nodes_from_csv(csv_file)

    if nodes is None:
        print("Halting script due to CSV parsing errors.")
        return
    if not nodes:
        print(f"No nodes found in the CSV file '{csv_file}'. Please create it.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"output_{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    print(f"Created output directory: {output_dir}")

    tasks = []
    for node in nodes:
        protocol = node.get('protocol', 'ssh').lower()
        task = None
        log_file_path = os.path.join(output_dir, f"{node['nodename']}_{timestamp}.txt")

        if protocol == 'ssh':
            task = execute_ssh_async(node, log_file_path)
        elif protocol == 'telnet':
            task = execute_telnet_async(node, log_file_path)
        else:
            print(f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***")
        
        if task:
            num_commands = len(node.get('commands', []))
            if node.get('additional_command_1'):
                num_commands += 1
            if node.get('additional_command_2'):
                num_commands += 1
            
            node_timeout = BASE_NODE_TIMEOUT + (num_commands * SECONDS_PER_COMMAND)
            print(f"Setting timeout for {node['nodename']} to {node_timeout} seconds ({num_commands} commands).")
            tasks.append(asyncio.wait_for(task, timeout=node_timeout))

    total_tasks = len(tasks)
    print(f"Processing {total_tasks} nodes...")
    print_progress_bar(0, total_tasks)

    successful_log_files = []
    completed_tasks = 0
    for f in asyncio.as_completed(tasks):
        try:
            log_file_path_result = await f
            if log_file_path_result:
                successful_log_files.append(log_file_path_result)
        except asyncio.TimeoutError:
            print(f"\nWarning: A node task timed out and was skipped.")
        except Exception as e:
            print(f"\nAn error occurred in a task: {e}")
        finally:
            completed_tasks += 1
            print_progress_bar(completed_tasks, total_tasks)

    print(f"\nAll {completed_tasks} node operations attempted.")

    if successful_log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        print(f"Zipping {len(successful_log_files)} successful log files...")
        create_zip_file(successful_log_files, zip_filename)
    else:
        print("No log files were successfully generated to zip.")

    print("\n=====================================================")
    print("Script finished. All operations are complete.")
    print("=====================================================")


if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()