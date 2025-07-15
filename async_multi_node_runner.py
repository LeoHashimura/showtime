import asyncio
import csv
import os
import sys
import zipfile
from datetime import datetime
from itertools import zip_longest # <--- Added import

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
    """
    nodes = []
    try:
        with open(file_path, 'r', newline='') as csvfile:
            reader = list(csv.reader(csvfile))
            if not reader:
                return []
            # Transpose the data safely, filling missing values with an empty string
            transposed_data = list(zip_longest(*reader, fillvalue=''))
            headers = [h.strip() for h in transposed_data[0]]
            for i in range(1, len(transposed_data)):
                node_info = {"commands": []}
                node_column = transposed_data[i]
                for j, header in enumerate(headers):
                    value = node_column[j].strip() if j < len(node_column) else ""
                    if header.startswith("command"):
                        if value:
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

async def execute_telnet_async(node_info):
    """
    Connects to a node using asyncio-based Telnet and executes commands.
    """
    output_log = f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via Telnet ---\n"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(node_info['ip_address'], 23),
            timeout=10
        )

        async def read_until(prompt_bytes, timeout=5):
            return await asyncio.wait_for(reader.readuntil(prompt_bytes), timeout=timeout)

        await read_until(b"Username: ")
        writer.write(node_info['login_id'].encode('ascii') + b"\n")
        await writer.drain()

        await read_until(b"Password: ")
        writer.write(node_info['login_password'].encode('ascii') + b"\n")
        await writer.drain()

        await asyncio.sleep(1)
        initial_output = await reader.read(65535)
        output_log += initial_output.decode('ascii', errors='ignore')

        if ">" in output_log:
            output_log += f"\n>>> Prompt is '>'. Sending additional command.\n"
            writer.write(node_info['additional_command_1'].encode('ascii') + b'\n')
            await writer.drain()
            await asyncio.sleep(1)
            output_log += (await reader.read(65535)).decode('ascii', errors='ignore')

        for cmd in node_info['commands']:
            output_log += f"\n>>> Executing command: {cmd}\n"
            writer.write(cmd.encode('ascii') + b'\n')
            await writer.drain()
            await asyncio.sleep(2)
            output_log += (await reader.read(65535)).decode('ascii', errors='ignore')

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
    Connects to a node using asyncssh and executes commands.
    """
    output_log = f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via SSH ---\n"
    try:
        async with asyncssh.connect(
            node_info['ip_address'],
            username=node_info['login_id'],
            password=node_info['login_password'],
            known_hosts=None
        ) as conn:
            async with conn.create_process() as process:
                await asyncio.sleep(1)
                initial_output = await process.stdout.read(65535)
                output_log += initial_output

                if ">" in initial_output:
                    output_log += f"\n>>> Prompt is '>'. Sending additional command.\n"
                    process.stdin.write(node_info['additional_command_1'] + '\n')
                    await asyncio.sleep(2)
                    output_log += await process.stdout.read(65535)

                for cmd in node_info['commands']:
                    output_log += f"\n>>> Executing command: {cmd}\n"
                    process.stdin.write(cmd + '\n')
                    await asyncio.sleep(2)
                    output_log += await process.stdout.read(65535)
                
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
    # Check for a command-line argument for the CSV file
    if len(sys.argv) > 1:
        # Check for help flag
        if sys.argv[1] in ('-h', '--help'):
            print("Usage: python async_multi_node_runner.py [path_to_your_csv_file]")
            print("If no file is provided, the script will look for 'nodes.csv'.")
            return # Exit the main function
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
        if protocol == 'ssh':
            tasks.append(execute_ssh_async(node))
        elif protocol == 'telnet':
            tasks.append(execute_telnet_async(node))
        else:
            print(f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***")

    total_tasks = len(tasks)
    print(f"Processing {total_tasks} nodes...")
    print_progress_bar(0, total_tasks)

    results = []
    for i, f in enumerate(asyncio.as_completed(tasks)):
        result = await f
        results.append(result)
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