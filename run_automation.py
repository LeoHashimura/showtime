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
    def __init__(self, all_nodes, status_queue, nodes_with_timeouts):
        self.all_node_names = [node['nodename'] for node in all_nodes]
        self.node_statuses = {name: 'pending' for name in self.all_node_names}
        self.status_queue = status_queue
        self.completed_count = 0
        self.total_nodes = len(self.all_node_names)
        self.display_limit = 20
        self.displayed_nodes = []
        self.shimmer_state = 0

        if nodes_with_timeouts:
            sorted_by_timeout = sorted(nodes_with_timeouts, key=lambda x: x[1])
            self.shortest_node = sorted_by_timeout[0][0]
            self.longest_node = sorted_by_timeout[-1][0]
        else:
            self.shortest_node = "N/A"
            self.longest_node = "N/A"

        self._print_initial_lines()

    def _print_initial_lines(self):
        # Reserve two lines, then move cursor to the start of the first one.
        sys.stdout.write('\n\n')
        sys.stdout.write(f'{CURSOR_UP}{CURSOR_UP}\r')
        sys.stdout.flush()

    def get_status_line_text(self):
        timed_out_nodes = [name for name, status in self.node_statuses.items() if status == 'timeout']

        if not timed_out_nodes:
            max_len = 40
            s_node = self.shortest_node
            l_node = self.longest_node
            if len(s_node) > max_len: s_node = s_node[:max_len-3] + '...'
            if len(l_node) > max_len: l_node = l_node[:max_len-3] + '...'
            return f"Shortest: {s_node}, Longest: {l_node}"
        else:
            terminal_width = 120
            prefix = "Timeouts: "
            available_width = terminal_width - len(prefix)
            
            nodes_str = ", ".join(timed_out_nodes)
            if len(nodes_str) > available_width:
                truncated_nodes = []
                current_len = 0
                for node in timed_out_nodes:
                    if current_len + len(node) + 2 > available_width - 3:
                        break
                    truncated_nodes.append(node)
                    current_len += len(node) + 2
                nodes_str = ", ".join(truncated_nodes) + "..."
            return f"{prefix}{nodes_str}"

    def _update_displayed_nodes(self):
        active_nodes = [name for name, status in self.node_statuses.items() if status not in ['success', 'error', 'timeout']]
        new_displayed_nodes = [node for node in self.displayed_nodes if node in active_nodes]

        for node_name in self.all_node_names:
            if node_name not in new_displayed_nodes and node_name in active_nodes:
                if len(new_displayed_nodes) < self.display_limit:
                    new_displayed_nodes.append(node_name)
                else:
                    break

        if len(new_displayed_nodes) < self.display_limit:
            completed_nodes = [name for name, status in self.node_statuses.items() if status in ['success', 'error', 'timeout']]
            for node_name in completed_nodes:
                if node_name not in new_displayed_nodes and len(new_displayed_nodes) < self.display_limit:
                    new_displayed_nodes.append(node_name)
                else:
                    break
        self.displayed_nodes = new_displayed_nodes[:self.display_limit]

    async def update(self):
        self.shimmer_state = (self.shimmer_state + 1) % 4 # Cycle through 4 states for shimmer
        
        # Process all pending status updates from the queue
        while not self.status_queue.empty():
            update_info = await self.status_queue.get()
            self.node_statuses[update_info['node']] = update_info['status']

        self._update_displayed_nodes()

        sys.stdout.write(CLEAR_LINE)
        percent = ("{0:.1f}").format(100 * (self.completed_count / float(self.total_nodes))) if self.total_nodes > 0 else "0.0"
        fill = '█'
        length = 50
        filled_length = int(length * self.completed_count // self.total_nodes) if self.total_nodes > 0 else 0
        bar = fill * filled_length + '-' * (length - filled_length)
        progress_bar_str = f'|{bar}| {percent}% Complete'

        node_status_bar = ""
        shimmer_chars = ['▓', '▒', '░', '▒'] # Characters for the shimmer effect
        for node_name in self.displayed_nodes:
            status = self.node_statuses.get(node_name, 'pending')
            char = '█'
            color = COLOR_RESET
            if status == 'connecting' or status == 'authenticating':
                char = '█'
                color = COLOR_YELLOW
            elif status == 'executing_commands':
                char = shimmer_chars[self.shimmer_state]
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
            node_status_bar += f"{color}{char}{COLOR_RESET}"
        
        line1_str = f"{progress_bar_str} {node_status_bar}"
        sys.stdout.write(line1_str + '\n')

        sys.stdout.write(CLEAR_LINE)
        line2_str = self.get_status_line_text()
        sys.stdout.write(line2_str)
        
        sys.stdout.write(f'{CURSOR_UP}\r')
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
        except Exception as e:
            return node['nodename'], None, str(e)

    async def display_updater(d):
        while d.completed_count < d.total_nodes:
            await d.update()
            await asyncio.sleep(0.2) # Refresh rate

    tasks = [run_node_task(node) for node in nodes]
    updater_task = asyncio.create_task(display_updater(display))

    successful_log_files = []
    for future in asyncio.as_completed(tasks):
        node_name, result, error = await future
        display.completed_count += 1

        if error == "TimeoutError":
            display.node_statuses[node_name] = 'timeout'
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
        os.system(post_command)

if __name__ == "__main__":
    os.environ['LANG'] = 'ja_JP.UTF-8'
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
