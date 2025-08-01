import asyncio
import os
import sys
import zipfile
from datetime import datetime
import time
from config_parsers import parse_nodes_from_csv, parse_nodes_from_excel
from network_operations import execute_ssh_async, execute_telnet_async

# ANSI escape codes for cursor control
CURSOR_UP = '\x1b[1A'
CLEAR_LINE = '\x1b[2K'

# ANSI escape codes for colors
COLOR_YELLOW = '\x1b[33m'
COLOR_GREEN = '\x1b[32m'
COLOR_RED = '\x1b[31m'
COLOR_RESET = '\x1b[0m'
COLOR_FLASH = '\x1b[5m' # For flashing green

def get_pdkey():
    kf, ma = '.pdkey', 5 * 24 * 60 * 60
    if os.path.exists(kf) and time.time() - os.path.getmtime(kf) < ma:
        with open(kf, 'r') as f:
            return f.read().strip()
    else:
        while True:
            nk = input("Enter new key or URL: ")
            k = nk.split("key=")[-1]
            if k:
                with open(kf, 'w') as f:
                    f.write(k)
                return k

class ProgressDisplay:
    def __init__(self, all_nodes, status_queue):
        self.all_node_names = [node['nodename'] for node in all_nodes]
        self.node_statuses = {name: 'pending' for name in self.all_node_names}
        self.status_queue = status_queue
        self.completed_count = 0
        self.total_nodes = len(self.all_node_names)
        self.display_limit = 20
        self.displayed_nodes = [] # List of node names currently displayed
        self._print_initial_lines()

    def _print_initial_lines(self):
        # Print two blank lines to reserve space for progress bar and node statuses
        print()
        print()

    async def update(self):
        # Process all pending status updates from the queue
        while not self.status_queue.empty():
            update_info = await self.status_queue.get()
            node_name = update_info['node']
            status = update_info['status']
            self.node_statuses[node_name] = status

        # Determine which nodes to display
        self._update_displayed_nodes()

        # Move cursor up two lines to redraw
        sys.stdout.write(CURSOR_UP)
        sys.stdout.write(CURSOR_UP)

        # Draw top line (node statuses)
        sys.stdout.write(CLEAR_LINE)
        status_line = ""
        for node_name in self.displayed_nodes:
            status = self.node_statuses.get(node_name, 'pending')
            char = '█' # Default for unknown/pending
            color = COLOR_RESET

            if status == 'connecting' or status == 'authenticating':
                char = '█' # Yellow block
                color = COLOR_YELLOW
            elif status == 'executing_commands':
                char = '█' # Flashing green
                color = COLOR_GREEN + COLOR_FLASH
            elif status == 'success':
                char = '█' # Solid green
                color = COLOR_GREEN
            elif status == 'error':
                char = '█' # Red
                color = COLOR_RED
            elif status == 'timeout':
                char = 'X'
                color = COLOR_RED

            status_line += f"{color}{char}{COLOR_RESET}"
        print(f"Node Status: {status_line}")

        # Draw bottom line (progress bar without "Progress: ")
        percent = ("{0:.1f}").format(100 * (self.completed_count / float(self.total_nodes)))
        fill = '█'
        length = 50
        filled_length = int(length * self.completed_count // self.total_nodes)
        bar = fill * filled_length + '-' * (length - filled_length)
        sys.stdout.write(CLEAR_LINE)
        print(f'|{bar}| {percent}% Complete')
        sys.stdout.flush()

    def _update_displayed_nodes(self):
        # Keep track of currently active nodes
        active_nodes = [name for name, status in self.node_statuses.items() if status not in ['success', 'error', 'timeout']]
        
        # Prioritize nodes that are already being displayed and are still active
        new_displayed_nodes = [node for node in self.displayed_nodes if node in active_nodes]

        # Fill up the remaining slots with new active nodes
        for node_name in self.all_node_names:
            if node_name not in new_displayed_nodes and node_name in active_nodes:
                if len(new_displayed_nodes) < self.display_limit:
                    new_displayed_nodes.append(node_name)
                else:
                    break # Display limit reached

        # If there are still empty slots, fill with completed nodes (just to show something)
        if len(new_displayed_nodes) < self.display_limit:
            completed_nodes = [name for name, status in self.node_statuses.items() if status in ['success', 'error', 'timeout']]
            for node_name in completed_nodes:
                if node_name not in new_displayed_nodes and len(new_displayed_nodes) < self.display_limit:
                    new_displayed_nodes.append(node_name)
                else:
                    break
        
        self.displayed_nodes = new_displayed_nodes[:self.display_limit]

def create_zip_file(files_to_zip, zip_filename):
    try:
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in files_to_zip:
                zf.write(file, os.path.basename(file))
        print(f"\nzipに固めましたよ: {zip_filename}")
    except Exception as e:
        print(f"\nError: 次の理由でzip固め損ねました: {e}")

async def main():
    pdkey = get_pdkey()
    BASE_NODE_TIMEOUT = 30.0
    SECONDS_PER_COMMAND = 5.0
    PDRIVE = "ls -l"

    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print("Usage: python3 run_automation.py [path_to_input_file] [options]")
        return

    input_file = sys.argv[1]
    nodes = None
    if input_file.lower().endswith('.csv'):
        nodes = parse_nodes_from_csv(input_file)
    elif input_file.lower().endswith(('.xlsx', '.xls')):
        sheet_identifier = 1
        if '--sheet' in sys.argv:
            try:
                sheet_identifier = sys.argv[sys.argv.index('--sheet') + 1]
                if sheet_identifier.isdigit():
                    sheet_identifier = int(sheet_identifier)
            except IndexError:
                print("Error: --sheet の後に対象シートを指定してください")
                return
        nodes = parse_nodes_from_excel(input_file, sheet_name=sheet_identifier)
    else:
        print(f" {input_file}は対象外です")
        return

    if not nodes:
        print(f"'{input_file}'の中にノードの指定が見つかりませんでした.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"output_{timestamp}"
    if os.path.dirname(input_file):
        output_dir = os.path.join(os.path.dirname(input_file), output_dir)
    os.makedirs(output_dir, exist_ok=True)

    tasks = []
    nodes_with_timeouts = []
    status_queue = asyncio.Queue()
    results_queue = asyncio.Queue() # To store results and node names

    for node in nodes:
        num_commands = len(node.get('commands', [])) + len([k for k in node if k.startswith('additional_command')])
        node_timeout = BASE_NODE_TIMEOUT + (num_commands * SECONDS_PER_COMMAND)
        nodes_with_timeouts.append((node['nodename'], node_timeout))

        protocol = node.get('protocol', 'ssh').lower()
        log_file_path = os.path.join(output_dir, f"{node['nodename']}_{timestamp}.txt")
        
        task = None
        if protocol == 'ssh':
            task = execute_ssh_async(node, log_file_path, status_queue)
        elif protocol == 'telnet':
            task = execute_telnet_async(node, log_file_path, status_queue)
        else:
            print(f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***")
            continue

        # Create a task (Future) from the coroutine returned by asyncio.wait_for
        future = asyncio.ensure_future(asyncio.wait_for(task, timeout=node_timeout))
        tasks.append(future)

        # Add a done callback to store the result and node name
        def done_callback(fut, node_name=node['nodename']):
            try:
                result = fut.result()
                results_queue.put_nowait((node_name, result, None)) # (node_name, result, error)
            except asyncio.TimeoutError:
                results_queue.put_nowait((node_name, None, "TimeoutError"))
            except Exception as e:
                results_queue.put_nowait((node_name, None, str(e)))

        future.add_done_callback(done_callback)

    if not tasks:
        print("No valid tasks to run.")
        return

    display = ProgressDisplay(nodes, status_queue)

    successful_log_files = []
    
    for _ in range(len(tasks)):
        node_name, result, error = await results_queue.get()
        display.completed_count += 1 # Update completed count for progress bar

        if error == "TimeoutError":
            display.node_statuses[node_name] = 'timeout'
        elif error:
            display.node_statuses[node_name] = 'error'
        else:
            if result:
                successful_log_files.append(result)
            display.node_statuses[node_name] = 'success' # Ensure final status is success
        
        await display.update() # Update the display after processing each result

    print(f"\n全 {len(tasks)} のノードの取得が完了しました。")

    if successful_log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        zip_destination = os.path.join(output_dir, zip_filename)
        create_zip_file(successful_log_files, zip_destination)
        post_command = f"{PDRIVE}{pdkey} {zip_destination}\n"
        os.system(post_command)

if __name__ == "__main__":
    os.environ['LANG'] = 'ja_JP.UTF-8'
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
