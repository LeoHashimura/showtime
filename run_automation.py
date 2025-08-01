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
    def __init__(self, total, nodes_with_timeouts):
        self.total = total
        self.completed = 0
        self.error_message = None
        self.nodes_with_timeouts = nodes_with_timeouts
        self.shortest_node = min(nodes_with_timeouts, key=lambda x: x[1])
        self.longest_node = max(nodes_with_timeouts, key=lambda x: x[1])
        self._print_initial_lines()

    def _print_initial_lines(self):
        # Print two blank lines to reserve space
        print()
        print()

    def update(self, completed_increment=0, error_node=None):
        self.completed += completed_increment
        if error_node:
            self.error_message = f"Status: Timeout on {error_node}. Continuing..."

        # Move cursor up two lines to redraw
        sys.stdout.write(CURSOR_UP)
        sys.stdout.write(CURSOR_UP)

        # Draw top line (status or min/max timeout info)
        sys.stdout.write(CLEAR_LINE)
        if self.error_message:
            print(self.error_message)
        else:
            print(f"Shortest: {self.shortest_node[0]} ({self.shortest_node[1]}s) | Longest: {self.longest_node[0]} ({self.longest_node[1]}s)")

        # Draw bottom line (progress bar)
        percent = ("{0:.1f}").format(100 * (self.completed / float(self.total)))
        fill = '█'
        length = 50
        filled_length = int(length * self.completed // self.total)
        bar = fill * filled_length + '-' * (length - filled_length)
        sys.stdout.write(CLEAR_LINE)
        print(f'Progress: |{bar}| {percent}% Complete')
        sys.stdout.flush()

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

        future = asyncio.wait_for(task, timeout=node_timeout)
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

    display = ProgressDisplay(len(tasks), nodes_with_timeouts)
    display.update()

    successful_log_files = []
    completed_count = 0
    while completed_count < len(tasks):
        node_name, result, error = await results_queue.get()
        completed_count += 1

        if error == "TimeoutError":
            display.update(completed_increment=1, error_node=node_name)
        elif error:
            display.update(completed_increment=1, error_node=f"{node_name} (Error: {error})")
        else:
            if result:
                successful_log_files.append(result)
            display.update(completed_increment=1)

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