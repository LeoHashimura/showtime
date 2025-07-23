import asyncio
import os
import sys
import zipfile
from datetime import datetime
from config_parsers import parse_nodes_from_csv, parse_nodes_from_excel
from network_operations import execute_ssh_async, execute_telnet_async

def print_progress_bar(iteration, total, prefix='Progress:', suffix='Complete', length=50, fill='█'):
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration == total:
        print()

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

    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print("Usage: python run_automation.py [path_to_input_file] [options]")
        print("\nArguments:")
        print("  path_to_input_file: Path to the input CSV or Excel file.")
        print("\nOptions:")
        print("  --sheet [sheet_name_or_index]: For Excel files, the name or index of the sheet to use. Defaults to the second sheet (index 1).")
        print(f"\nTimeout settings:")
        print(f"  Base node timeout is {BASE_NODE_TIMEOUT} seconds, plus {SECONDS_PER_COMMAND} seconds per command.")
        return

    input_file = sys.argv[1]
    nodes = None

    if input_file.lower().endswith('.csv'):
        print(f"Using CSV file: {input_file}")
        nodes = parse_nodes_from_csv(input_file)
    elif input_file.lower().endswith(('.xlsx', '.xls')):
        sheet_identifier = 1
        if '--sheet' in sys.argv:
            try:
                sheet_identifier = sys.argv[sys.argv.index('--sheet') + 1]
                if sheet_identifier.isdigit():
                    sheet_identifier = int(sheet_identifier)
            except IndexError:
                print("Error: --sheet option requires a value.")
                return
        print(f"Using Excel file: {input_file}, sheet: '{sheet_identifier}'")
        nodes = parse_nodes_from_excel(input_file, sheet_name=sheet_identifier)
    else:
        print(f"Error: Unsupported file type for {input_file}. Please use a .csv or .xlsx file.")
        return

    if nodes is None:
        print("Halting script due to parsing errors.")
        return
    if not nodes:
        print(f"No nodes found in the input file '{input_file}'.")
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
