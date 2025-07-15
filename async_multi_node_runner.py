import asyncio
import csv
import os
import re
import sys
import zipfile
from datetime import datetime
from itertools import zip_longest

# Third-party library: asyncssh. Please install it using: pip install asyncssh
try:
    import asyncssh
except ImportError:
    print("Error: The 'asyncssh' library is required for asynchronous SSH connections.")
    print("Please install it by running: pip install asyncssh")
    exit()

# Third-party library: asyncssh. Please install it using: pip install asyncssh
try:
    import asyncssh
except ImportError:
    print("Error: The 'asyncssh' library is required for asynchronous SSH connections.")
    print("Please install it by running: pip install asyncssh")
    exit()

def print_progress_bar(iteration, total, prefix='Progress:', suffix='Complete', length=50, fill='█'):
    """
    Prints a manual, library-free progress bar to the console.
    """
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
    # Define the specific headers that are for configuration.
    # Any other row will be treated as a potential command.
    config_headers = {
        'nodename', 'protocol', 'ip_address', 'login_id', 
        'login_password', 'additional_command_1', 'additional_command_2'
    }

    try:
        with open(file_path, 'r', newline='') as csvfile:
            reader = list(csv.reader(csvfile))
            if not reader:
                return []
            
            transposed_data = list(zip_longest(*reader, fillvalue=''))
            headers = [h.strip() for h in transposed_data[0]]

            for i in range(1, len(transposed_data)):
                node_info = {"commands": []}
                node_column = transposed_data[i]
                commands_ended = False  # Flag to stop processing commands for this node

                for j, header in enumerate(headers):
                    value = node_column[j].strip() if j < len(node_column) else ""

                    if header not in config_headers:
                        # This is a command row.
                        if not value:
                            # If the command is blank, stop adding more commands for this node.
                            commands_ended = True
                        
                        if not commands_ended:
                            node_info["commands"].append(value)
                    else:
                        # This is a configuration row.
                        node_info[header] = value
                
                nodes.append(node_info)

    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return None
    except Exception as e:
        print(f"An error occurred while parsing the CSV: {e}")
        return None
    return nodes

async def execute_telnet_async(node_info):
    """
    Connects to a node using Telnet, waits for prompts, and executes commands.
    """
    output_log = f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via Telnet ---\n"
    prompt_re = re.compile(b'\S+[>#$]\s*

async def execute_ssh_async(node_info):
    """
    Connects to a node using asyncssh, waits for prompts, and executes commands.
    """
    output_log = f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via SSH ---\n"
    # Regex to detect a typical command prompt (e.g., Router>, Switch#, user$ )
    prompt_re = re.compile(r'\S+[>#$]\s*

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
    # --- Configuration ---
    # Set a global timeout in seconds for each node connection and command execution
    NODE_TIMEOUT = 30.0

    # Check for a command-line argument for the CSV file
    if len(sys.argv) > 1:
        if sys.argv[1] in ('-h', '--help'):
            print("Usage: python async_multi_node_runner.py [path_to_your_csv_file]")
            print(f"Default node timeout is {NODE_TIMEOUT} seconds.")
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
        if protocol == 'ssh':
            task = execute_ssh_async(node)
        elif protocol == 'telnet':
            task = execute_telnet_async(node)
        else:
            print(f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***")
        
        if task:
            # Wrap the node-specific task in a global timeout
            tasks.append(asyncio.wait_for(task, timeout=NODE_TIMEOUT))

    total_tasks = len(tasks)
    print(f"Processing {total_tasks} nodes with a {NODE_TIMEOUT}-second timeout per node...")
    print_progress_bar(0, total_tasks)

    results = []
    for i, f in enumerate(asyncio.as_completed(tasks)):
        try:
            result = await f
            results.append(result)
        except asyncio.TimeoutError:
            # This block will be executed if a node task exceeds the NODE_TIMEOUT
            # We need to find which node it was, but the task itself is cancelled.
            # This is a limitation of as_completed. The primary goal is to not hang.
            print(f"\nWarning: A node task timed out after {NODE_TIMEOUT} seconds and was skipped.")
        finally:
            print_progress_bar(i + 1, total_tasks)

    log_files = []
    print("\nProcessing results and writing log files...")
    for nodename, log_content in results:
        log_filename = os.path.join(output_dir, f"{nodename}_{timestamp}.txt")
        try:
            with open(log_filename, 'w', encoding='utf-8') as f:
                f.write(log_content)
            log_files.append(log_filename)
        except Exception as e:
            print(f"Error writing log file for {nodename}. Reason: {e}")
    print(f"All {len(log_files)} log files written successfully.")

    if log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        create_zip_file(log_files, zip_filename)
    else:
        print("No log files were generated to zip.")

    print("\n=====================================================")
    print("Script finished. All operations are complete.")
    print("=====================================================")

if __name__ == "__main__":
    # On Windows, the default event loop policy can cause issues with asyncssh.
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Use the legacy method for Python 3.6 and below
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main()))

    async def read_until_prompt(process, timeout=20):
        """Reads from the process until a prompt is detected or a timeout occurs."""
        full_output = ""
        try:
            while True:
                chunk = await asyncio.wait_for(process.stdout.read(1024), timeout=timeout)
                if not chunk:
                    break
                full_output += chunk
                # Check if the last non-empty line of the current output contains a prompt
                non_empty_lines = [line for line in full_output.splitlines() if line.strip()]
                if non_empty_lines and prompt_re.search(non_empty_lines[-1]):
                    break
        except asyncio.TimeoutError:
            full_output += "\n*** TIMEOUT: Waited too long for a prompt. ***\n"
        return full_output

    try:
        async with asyncssh.connect(
            node_info['ip_address'],
            username=node_info['login_id'],
            password=node_info['login_password'],
            known_hosts=None
        ) as conn:
            async with conn.create_process(term_type='vt100') as process:
                initial_output = await read_until_prompt(process)
                output_log += initial_output
                print(f"\n--- Initial connection to {node_info['nodename']} ---\n{initial_output}\n-------------------------------------")

                if node_info.get('additional_command_1') and ">" in initial_output:
                    cmd = node_info['additional_command_1']
                    output_log += f"\n>>> Executing command: {cmd}\n"
                    process.stdin.write(cmd + '\n')
                    response = await read_until_prompt(process)
                    print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
                    output_log += response

                for cmd in node_info['commands']:
                    output_log += f"\n>>> Executing command: {cmd}\n"
                    process.stdin.write(cmd + '\n')
                    response = await read_until_prompt(process)
                    print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
                    output_log += response
                
                process.stdin.write('exit\n')
                await process.wait()

        output_log += "\n--- Disconnected ---"

    except Exception as e:
        output_log += f"\n*** ERROR: Failed to connect or execute commands on {node_info['nodename']}. Reason: {e} ***\n"

    return node_info['nodename'], output_log

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
    # --- Configuration ---
    # Set a global timeout in seconds for each node connection and command execution
    NODE_TIMEOUT = 30.0

    # Check for a command-line argument for the CSV file
    if len(sys.argv) > 1:
        if sys.argv[1] in ('-h', '--help'):
            print("Usage: python async_multi_node_runner.py [path_to_your_csv_file]")
            print(f"Default node timeout is {NODE_TIMEOUT} seconds.")
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
        if protocol == 'ssh':
            task = execute_ssh_async(node)
        elif protocol == 'telnet':
            task = execute_telnet_async(node)
        else:
            print(f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***")
        
        if task:
            # Wrap the node-specific task in a global timeout
            tasks.append(asyncio.wait_for(task, timeout=NODE_TIMEOUT))

    total_tasks = len(tasks)
    print(f"Processing {total_tasks} nodes with a {NODE_TIMEOUT}-second timeout per node...")
    print_progress_bar(0, total_tasks)

    results = []
    for i, f in enumerate(asyncio.as_completed(tasks)):
        try:
            result = await f
            results.append(result)
        except asyncio.TimeoutError:
            # This block will be executed if a node task exceeds the NODE_TIMEOUT
            # We need to find which node it was, but the task itself is cancelled.
            # This is a limitation of as_completed. The primary goal is to not hang.
            print(f"\nWarning: A node task timed out after {NODE_TIMEOUT} seconds and was skipped.")
        finally:
            print_progress_bar(i + 1, total_tasks)

    log_files = []
    print("\nProcessing results and writing log files...")
    for nodename, log_content in results:
        log_filename = os.path.join(output_dir, f"{nodename}_{timestamp}.txt")
        try:
            with open(log_filename, 'w', encoding='utf-8') as f:
                f.write(log_content)
            log_files.append(log_filename)
        except Exception as e:
            print(f"Error writing log file for {nodename}. Reason: {e}")
    print(f"All {len(log_files)} log files written successfully.")

    if log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        create_zip_file(log_files, zip_filename)
    else:
        print("No log files were generated to zip.")

    print("\n=====================================================")
    print("Script finished. All operations are complete.")
    print("=====================================================")

if __name__ == "__main__":
    # On Windows, the default event loop policy can cause issues with asyncssh.
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Use the legacy method for Python 3.6 and below
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())) # Using bytes for Telnet

    async def read_until_prompt(reader, timeout=20):
        """Reads from the stream until a prompt is detected or a timeout occurs."""
        full_output = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                if not chunk:
                    break
                full_output += chunk
                non_empty_lines = [line for line in full_output.splitlines() if line.strip()]
                if non_empty_lines and prompt_re.search(non_empty_lines[-1]):
                    break
        except asyncio.TimeoutError:
            full_output += b"\n*** TIMEOUT: Waited too long for a prompt. ***\n"
        return full_output.decode('ascii', errors='ignore')

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(node_info['ip_address'], 23),
            timeout=10
        )

        # Handle login
        await asyncio.wait_for(reader.readuntil(b"Username: "), timeout=5)
        writer.write(node_info['login_id'].encode('ascii') + b"\n")
        await writer.drain()
        await asyncio.wait_for(reader.readuntil(b"Password: "), timeout=5)
        writer.write(node_info['login_password'].encode('ascii') + b"\n")
        await writer.drain()

        initial_output = await read_until_prompt(reader)
        output_log += initial_output
        print(f"\n--- Initial connection to {node_info['nodename']} ---\n{initial_output}\n-------------------------------------")

        if node_info.get('additional_command_1') and ">" in initial_output:
            cmd = node_info['additional_command_1']
            output_log += f"\n>>> Executing command: {cmd}\n"
            writer.write(cmd.encode('ascii') + b'\n')
            await writer.drain()
            response = await read_until_prompt(reader)
            print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
            output_log += response

        for cmd in node_info['commands']:
            output_log += f"\n>>> Executing command: {cmd}\n"
            writer.write(cmd.encode('ascii') + b'\n')
            await writer.drain()
            response = await read_until_prompt(reader)
            print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
            output_log += response

        writer.write(b"exit\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        output_log += "\n--- Disconnected ---"

    except Exception as e:
        output_log += f"\n*** ERROR: Failed to connect or execute commands on {node_info['nodename']}. Reason: {e} ***\n"
    
    return node_info['nodename'], output_log

async def execute_ssh_async(node_info):
    """
    Connects to a node using asyncssh, waits for prompts, and executes commands.
    """
    output_log = f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via SSH ---\n"
    # Regex to detect a typical command prompt (e.g., Router>, Switch#, user$ )
    prompt_re = re.compile(r'\S+[>#$]\s*

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
    # --- Configuration ---
    # Set a global timeout in seconds for each node connection and command execution
    NODE_TIMEOUT = 30.0

    # Check for a command-line argument for the CSV file
    if len(sys.argv) > 1:
        if sys.argv[1] in ('-h', '--help'):
            print("Usage: python async_multi_node_runner.py [path_to_your_csv_file]")
            print(f"Default node timeout is {NODE_TIMEOUT} seconds.")
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
        if protocol == 'ssh':
            task = execute_ssh_async(node)
        elif protocol == 'telnet':
            task = execute_telnet_async(node)
        else:
            print(f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***")
        
        if task:
            # Wrap the node-specific task in a global timeout
            tasks.append(asyncio.wait_for(task, timeout=NODE_TIMEOUT))

    total_tasks = len(tasks)
    print(f"Processing {total_tasks} nodes with a {NODE_TIMEOUT}-second timeout per node...")
    print_progress_bar(0, total_tasks)

    results = []
    for i, f in enumerate(asyncio.as_completed(tasks)):
        try:
            result = await f
            results.append(result)
        except asyncio.TimeoutError:
            # This block will be executed if a node task exceeds the NODE_TIMEOUT
            # We need to find which node it was, but the task itself is cancelled.
            # This is a limitation of as_completed. The primary goal is to not hang.
            print(f"\nWarning: A node task timed out after {NODE_TIMEOUT} seconds and was skipped.")
        finally:
            print_progress_bar(i + 1, total_tasks)

    log_files = []
    print("\nProcessing results and writing log files...")
    for nodename, log_content in results:
        log_filename = os.path.join(output_dir, f"{nodename}_{timestamp}.txt")
        try:
            with open(log_filename, 'w', encoding='utf-8') as f:
                f.write(log_content)
            log_files.append(log_filename)
        except Exception as e:
            print(f"Error writing log file for {nodename}. Reason: {e}")
    print(f"All {len(log_files)} log files written successfully.")

    if log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        create_zip_file(log_files, zip_filename)
    else:
        print("No log files were generated to zip.")

    print("\n=====================================================")
    print("Script finished. All operations are complete.")
    print("=====================================================")

if __name__ == "__main__":
    # On Windows, the default event loop policy can cause issues with asyncssh.
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Use the legacy method for Python 3.6 and below
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main()))

    async def read_until_prompt(process, timeout=20):
        """Reads from the process until a prompt is detected or a timeout occurs."""
        full_output = ""
        try:
            while True:
                chunk = await asyncio.wait_for(process.stdout.read(1024), timeout=timeout)
                if not chunk:
                    break
                full_output += chunk
                # Check if the last non-empty line of the current output contains a prompt
                non_empty_lines = [line for line in full_output.splitlines() if line.strip()]
                if non_empty_lines and prompt_re.search(non_empty_lines[-1]):
                    break
        except asyncio.TimeoutError:
            full_output += "\n*** TIMEOUT: Waited too long for a prompt. ***\n"
        return full_output

    try:
        async with asyncssh.connect(
            node_info['ip_address'],
            username=node_info['login_id'],
            password=node_info['login_password'],
            known_hosts=None
        ) as conn:
            async with conn.create_process(term_type='vt100') as process:
                initial_output = await read_until_prompt(process)
                output_log += initial_output
                print(f"\n--- Initial connection to {node_info['nodename']} ---\n{initial_output}\n-------------------------------------")

                if node_info.get('additional_command_1') and ">" in initial_output:
                    cmd = node_info['additional_command_1']
                    output_log += f"\n>>> Executing command: {cmd}\n"
                    process.stdin.write(cmd + '\n')
                    response = await read_until_prompt(process)
                    print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
                    output_log += response

                for cmd in node_info['commands']:
                    output_log += f"\n>>> Executing command: {cmd}\n"
                    process.stdin.write(cmd + '\n')
                    response = await read_until_prompt(process)
                    print(f"\n--- Output from {node_info['nodename']} after '{cmd}' ---\n{response}\n-------------------------------------")
                    output_log += response
                
                process.stdin.write('exit\n')
                await process.wait()

        output_log += "\n--- Disconnected ---"

    except Exception as e:
        output_log += f"\n*** ERROR: Failed to connect or execute commands on {node_info['nodename']}. Reason: {e} ***\n"

    return node_info['nodename'], output_log

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
    # --- Configuration ---
    # Set a global timeout in seconds for each node connection and command execution
    NODE_TIMEOUT = 30.0

    # Check for a command-line argument for the CSV file
    if len(sys.argv) > 1:
        if sys.argv[1] in ('-h', '--help'):
            print("Usage: python async_multi_node_runner.py [path_to_your_csv_file]")
            print(f"Default node timeout is {NODE_TIMEOUT} seconds.")
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
        if protocol == 'ssh':
            task = execute_ssh_async(node)
        elif protocol == 'telnet':
            task = execute_telnet_async(node)
        else:
            print(f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***")
        
        if task:
            # Wrap the node-specific task in a global timeout
            tasks.append(asyncio.wait_for(task, timeout=NODE_TIMEOUT))

    total_tasks = len(tasks)
    print(f"Processing {total_tasks} nodes with a {NODE_TIMEOUT}-second timeout per node...")
    print_progress_bar(0, total_tasks)

    results = []
    for i, f in enumerate(asyncio.as_completed(tasks)):
        try:
            result = await f
            results.append(result)
        except asyncio.TimeoutError:
            # This block will be executed if a node task exceeds the NODE_TIMEOUT
            # We need to find which node it was, but the task itself is cancelled.
            # This is a limitation of as_completed. The primary goal is to not hang.
            print(f"\nWarning: A node task timed out after {NODE_TIMEOUT} seconds and was skipped.")
        finally:
            print_progress_bar(i + 1, total_tasks)

    log_files = []
    print("\nProcessing results and writing log files...")
    for nodename, log_content in results:
        log_filename = os.path.join(output_dir, f"{nodename}_{timestamp}.txt")
        try:
            with open(log_filename, 'w', encoding='utf-8') as f:
                f.write(log_content)
            log_files.append(log_filename)
        except Exception as e:
            print(f"Error writing log file for {nodename}. Reason: {e}")
    print(f"All {len(log_files)} log files written successfully.")

    if log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        create_zip_file(log_files, zip_filename)
    else:
        print("No log files were generated to zip.")

    print("\n=====================================================")
    print("Script finished. All operations are complete.")
    print("=====================================================")

if __name__ == "__main__":
    # On Windows, the default event loop policy can cause issues with asyncssh.
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Use the legacy method for Python 3.6 and below
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())