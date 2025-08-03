import asyncio
import os
import sys
import zipfile
from datetime import datetime
import time
from config_parsers import parse_nodes_from_csv, parse_nodes_from_excel
from network_operations import execute_ssh_async, execute_telnet_async, PromptTimeoutError
#this is a test version of run_automation.py.
#since we established that network connections seems fine,
#this file is to test UI enhancements, xlsx parsing, and other features.
#after checking on local windows computer, we will merge this into the main run_automation.py file.
#main change for conversion to linux is to delete asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy()) 
#and uncomment the os.environ['LANG'] = 'ja_JP.UTF-8' line, and os,system(post_command) line.
# This script is designed to run automation tasks on multiple nodes
# and display progress in a terminal-friendly manner.
# It supports SSH and Telnet protocols, handles timeouts, and provides a visual progress display.
# It also allows for zipping the output files after completion.
# Ensure the script is run with Python 3.1+ for asyncio compatibility    
# Ensure the necessary libraries are installed:
# pip install asyncio  openpyxl  telnetlib3

# ANSI escape codes for cursor control
CURSOR_UP = '\x1b[1A'
CLEAR_LINE = '\x1b[2K'

# ANSI escape codes for colors
COLOR_YELLOW = '\x1b[33m'
COLOR_GREEN = '\x1b[32m'
COLOR_RED = '\x1b[31m'
COLOR_BLUE = '\x1b[34m'
COLOR_RESET = '\x1b[0m'
COLOR_FLASH = '\x1b[5m' # For flashing green

def get_pdkey():
    kf, ma = '.pdkey', 5 * 24 * 60 * 60
    if os.path.exists(kf) and time.time() - os.path.getmtime(kf) < ma:
        return open(kf).read().strip()
    while True:
        k = input("xxxxのキーもしくはURLを入力してください: ").split("key=")[-1]
        if k:
            open(kf, 'w').write(k)
            return k
class ProgressDisplay:
    def __init__(self, all_nodes, status_queue, nodes_with_timeouts):
        self.all_node_names = [node['nodename'] for node in all_nodes]
        self.nodes_with_timeouts = dict(nodes_with_timeouts)
        self.node_statuses = {name: 'pending' for name in self.all_node_names}
        self.status_queue = status_queue
        self.completed_count = 0
        self.total_nodes = len(self.all_node_names)
        self.shimmer_state = 0
        self.has_printed_once = False

        if nodes_with_timeouts:
            sorted_by_timeout = sorted(nodes_with_timeouts, key=lambda x: x[1])
            self.shortest_node_name, self.shortest_node_timeout = sorted_by_timeout[0]
            self.longest_node_name, self.longest_node_timeout = sorted_by_timeout[-1]
        else:
            self.shortest_node_name, self.shortest_node_timeout = "なんかおかしい", 0
            self.longest_node_name, self.longest_node_timeout = "なんかおかしい", 0

    def get_status_line_text(self):
        failed_nodes = [name for name, status in self.node_statuses.items() if status in ['error', 'timeout', 'no_prompt']]

        if not failed_nodes:
            s_node = self.shortest_node_name
            l_node = self.longest_node_name
            s_timeout = self.shortest_node_timeout
            l_timeout = self.longest_node_timeout
            return f"最短Timeout {s_node} ({s_timeout:.1f}s), 最長Timeout: {l_node} ({l_timeout:.1f}s)"
        else:
            prefix = "Errors In: "
            nodes_str = ", ".join(failed_nodes)
            return f"{prefix}{nodes_str}"

    async def update(self):
        if not self.has_printed_once:
            sys.stdout.write('\n\n') # Reserve two lines on the first run
            self.has_printed_once = True

        self.shimmer_state = (self.shimmer_state + 1) % 4
        
        while not self.status_queue.empty():
            update_info = await self.status_queue.get()
            self.node_statuses[update_info['node']] = update_info['status']

        # --- Prepare Line 1: Node Status Bar and Percentage ---
        percent = ("{0:.1f}").format(100 * (self.completed_count / float(self.total_nodes))) if self.total_nodes > 0 else "0.0"
        percent_str = f" {percent}%"

        node_status_bar = ""
        shimmer_chars = ['▓', '▒', '░', '▒']
        for i, node_name in enumerate(self.all_node_names):
            status = self.node_statuses.get(node_name, 'pending')
            char = '█'
            color = COLOR_RESET
            if status == 'connecting' or status == 'authenticating':
                char = '█'
                color = COLOR_YELLOW
            elif status == 'executing_commands':
                offset = hash(node_name) % 4
                char = shimmer_chars[(self.shimmer_state + offset) % 4]
                color = COLOR_GREEN
            elif status == 'success':
                char = '█'
                color = COLOR_GREEN
            elif status == 'error':
                char = '█'
                color = COLOR_RED
            elif status == 'timeout':
                char = 'X'
                color = COLOR_RED
            elif status == 'no_prompt':
                char = '█'
                color = COLOR_BLUE
            node_status_bar += f"{color}{char}{COLOR_RESET}"
        
        line1_str = f"{node_status_bar}{percent_str}"
        
        # --- Prepare Line 2: Status Text ---
        line2_str = self.get_status_line_text()

        # --- Cursors and Printing (Final, robust method) ---
        sys.stdout.write(f'\r{CLEAR_LINE}{line1_str}\n')
        sys.stdout.write(f'\r{CLEAR_LINE}{line2_str}')
        sys.stdout.write(CURSOR_UP) # Move back to the start of the second line
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
    sheet_identifier = None # Initialize sheet_identifier
    if input_file.lower().endswith('.csv'):
        nodes = parse_nodes_from_csv(input_file)
    elif input_file.lower().endswith(('.xlsx', '.xls')):
        sheet_identifier = 2
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

    if input_file.lower().endswith(('.xlsx', '.xls')):
        print(f"--- Processing sheet '{sheet_identifier}' from {os.path.basename(input_file)} ---")
    else:
        print(f"--- Processing file: {os.path.basename(input_file)} ---")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"output_{timestamp}"
    if os.path.dirname(input_file):
        output_dir = os.path.join(os.path.dirname(input_file), output_dir)
    os.makedirs(output_dir, exist_ok=True)

    status_queue = asyncio.Queue()
    results_queue = asyncio.Queue()
    nodes_with_timeouts = []

    for node in nodes:
        num_commands = len(node.get('commands', [])) + len([k for k in node if k.startswith('additional_command')])
        node_timeout = BASE_NODE_TIMEOUT + (num_commands * SECONDS_PER_COMMAND)
        nodes_with_timeouts.append((node['nodename'], node_timeout))

    display = ProgressDisplay(nodes, status_queue, nodes_with_timeouts)

    async def run_node_task(node):
        node_timeout = next((t for n, t in nodes_with_timeouts if n == node['nodename']), BASE_NODE_TIMEOUT)
        log_file_path = os.path.join(output_dir, f"{node['nodename']}_{timestamp}.txt")
        protocol = node.get('protocol', 'ssh').lower()
        task = None
        if protocol == 'ssh':
            task = execute_ssh_async(node, log_file_path, status_queue)
        elif protocol == 'telnet':
            task = execute_telnet_async(node, log_file_path, status_queue)
        else:
            await status_queue.put({'node': node['nodename'], 'status': 'error', 'message': f'Unknown protocol: {protocol}'})
            return node['nodename'], None, f"Unknown protocol: {protocol}"
        
        try:
            result = await asyncio.wait_for(task, timeout=node_timeout)
            return node['nodename'], result, None
        except asyncio.TimeoutError:
            return node['nodename'], None, "TimeoutError"
        except PromptTimeoutError:
            return node['nodename'], None, "PromptTimeoutError"
        except Exception as e:
            import traceback
            print(f"\n*** UNEXPECTED ERROR CAUGHT IN GENERIC BLOCK ***\n")
            traceback.print_exc()
            print(f"\n*** END OF TRACEBACK ***\n")
            return node['nodename'], None, str(e)

    async def display_updater(d):
        while d.completed_count < d.total_nodes:
            await d.update()
            await asyncio.sleep(0.2) # Refresh rate

    tasks = [run_node_task(node) for node in nodes]
    updater_task = asyncio.ensure_future(display_updater(display))

    successful_log_files = []
    for future in asyncio.as_completed(tasks):
        node_name, result, error = await future
        display.completed_count += 1

        if error == "TimeoutError":
            display.node_statuses[node_name] = 'timeout'
        elif error == "PromptTimeoutError":
            display.node_statuses[node_name] = 'no_prompt'
        elif error:
            display.node_statuses[node_name] = 'error'
        else:
            if result:
                successful_log_files.append(result)
            display.node_statuses[node_name] = 'success'
    
    updater_task.cancel()
    try:
        await updater_task # Allow updater to finish final render
    except asyncio.CancelledError:
        pass
    await display.update() # One final update to show 100%

    # Move cursor below the display area before printing final message
    sys.stdout.write('\n\n') 
    print(f"全 {len(tasks)} のノードの取得が完了しました。")

    if successful_log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        zip_destination = os.path.join(output_dir, zip_filename)
        create_zip_file(successful_log_files, zip_destination)
        post_command = f"{PDRIVE}{pdkey} {zip_destination}\n"
#        os.system(post_command)

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
