import asyncio
import os
import sys
import zipfile
from datetime import datetime
import time
import curses
import argparse
import threading
from config_parsers import parse_nodes_from_csv, parse_nodes_from_excel
from network_operations import execute_ssh_async, execute_telnet_async, PromptTimeoutError, LogoutFailedError

# ANSI escape codes for cursor control
CURSOR_UP = '\x1b[1A'
CLEAR_LINE = '\x1b[2K'

# ANSI escape codes for colors
COLOR_YELLOW = '\x1b[33m'
COLOR_GREEN = '\x1b[32m'
COLOR_RED = '\x1b[31m'
COLOR_BLUE = '\x1b[34m'
COLOR_MAGENTA = '\x1b[35m'
COLOR_RESET = '\x1b[0m'

class ProgressDisplay:
    def __init__(self, all_nodes, status_queue, is_cycle_mode=False, nodes_with_timeouts=None):
        self.all_node_names = [node['nodename'] for node in all_nodes]
        self.node_statuses = {name: 'pending' for name in self.all_node_names}
        self.status_queue = status_queue
        self.is_cycle_mode = is_cycle_mode
        self.completed_count = 0
        self.total_nodes = len(self.all_node_names)
        self.nodes_with_timeouts = nodes_with_timeouts

    async def update(self, stdscr):
        stdscr.nodelay(True)
        
        while True:
            while not self.status_queue.empty():
                update_info = await self.status_queue.get()
                self.node_statuses[update_info['node']] = update_info['status']

            node_status_bar = ""
            for node_name in self.all_node_names:
                status = self.node_statuses.get(node_name, 'pending')
                char, color = self.get_status_char_and_color(status)
                node_status_bar += f"{color}{char}{COLOR_RESET}"

            if self.is_cycle_mode:
                line1 = f"Press 'q' to quit. Status: {node_status_bar}"
                line2 = ""
            else:
                percent = (100 * (self.completed_count / self.total_nodes)) if self.total_nodes > 0 else 0
                failed_nodes = [name for name, status in self.node_statuses.items() if status not in ['success', 'pending', 'executing_commands']]
                line1 = f"[{node_status_bar}] {percent:.1f}% Complete"
                line2 = f"Errors In: { ', '.join(failed_nodes)}" if failed_nodes else "All nodes running..."

            try:
                stdscr.addstr(0, 0, line1)
                stdscr.addstr(1, 0, line2)
                stdscr.clrtoeol()
                stdscr.refresh()
            except curses.error:
                pass

            await asyncio.sleep(0.2)

    def get_status_char_and_color(self, status):
        if status == 'connecting' or status == 'authenticating':
            return 'C', COLOR_YELLOW
        elif status == 'executing_commands':
            return 'E', COLOR_GREEN
        elif status == 'success':
            return 'S', COLOR_GREEN
        elif status == 'error':
            return 'F', COLOR_RED
        elif status == 'timeout':
            return 'T', COLOR_RED
        elif status == 'no_prompt':
            return 'P', COLOR_BLUE
        elif status == 'logout_failed':
            return 'L', COLOR_MAGENTA
        else:
            return '.', COLOR_RESET

def handle_post_processing(log_files, output_dir, timestamp):
    if not log_files:
        print("\nNo successful logs to process.")
        return

    pdkey = get_pdkey()
    PDRIVE = "ls -l"

    zip_filename = f"command_output_{timestamp}.zip"
    zip_destination = os.path.join(output_dir, zip_filename)
    
    try:
        with zipfile.ZipFile(zip_destination, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in log_files:
                zf.write(file, os.path.basename(file))
        print(f"\nzipに固めましたよ: {zip_destination}")
        
        post_command = f"{PDRIVE}{pdkey} {zip_destination}\n"
        os.system(post_command)
    except Exception as e:
        print(f"\nError during post-processing: {e}")

def get_pdkey():
    kf, ma = '.pdkey', 5 * 24 * 60 * 60
    if os.path.exists(kf) and time.time() - os.path.getmtime(kf) < ma:
        return open(kf).read().strip()
    while True:
        k = input("xxxxのキーもしくはURLを入力してください: ").split("key=")[-1]
        if k:
            open(kf, 'w').write(k)
            return k

async def main_wrapped(stdscr):
    parser = argparse.ArgumentParser(description="Run automation tasks on network nodes.")
    parser.add_argument("input_file", help="Path to the input file (CSV or Excel).")
    parser.add_argument("--sheet", help="Specify the sheet name or index for Excel files.", default=2)
    parser.add_argument("--interval", help="Run in cycle mode with this interval in milliseconds.", type=int, default=-1)
    args = parser.parse_args()

    nodes = []
    if args.input_file.lower().endswith('.csv'):
        nodes = parse_nodes_from_csv(args.input_file)
    elif args.input_file.lower().endswith(('.xlsx', '.xls')):
        nodes = parse_nodes_from_excel(args.input_file, sheet_name=args.sheet)
    else:
        print(f"Error: {args.input_file} is not a supported file type.")
        return

    if not nodes:
        print(f"Error: No nodes found in '{args.input_file}'.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"output_{timestamp}"
    os.makedirs(output_dir, exist_ok=True)

    stop_event = asyncio.Event()
    status_queue = asyncio.Queue()

    if args.interval == -1:
        await single_run_mode(stdscr, nodes, status_queue, output_dir, timestamp)
    else:
        await cycle_mode(stdscr, nodes, status_queue, stop_event, args.interval, output_dir, timestamp)

async def single_run_mode(stdscr, nodes, status_queue, output_dir, timestamp):
    BASE_NODE_TIMEOUT = 30.0
    SECONDS_PER_COMMAND = 5.0
    nodes_with_timeouts = []
    for node in nodes:
        num_commands = len(node.get('commands', [])) + len([k for k in node if k.startswith('additional_command')])
        node_timeout = BASE_NODE_TIMEOUT + (num_commands * SECONDS_PER_COMMAND)
        nodes_with_timeouts.append((node['nodename'], node_timeout))

    display = ProgressDisplay(nodes, status_queue, nodes_with_timeouts=nodes_with_timeouts)
    updater_task = asyncio.create_task(display.update(stdscr))

    async def run_node_task(node):
        node_timeout = next((t for n, t in nodes_with_timeouts if n == node['nodename']), BASE_NODE_TIMEOUT)
        log_file_path = os.path.join(output_dir, f"{node['nodename']}_{timestamp}.txt")
        protocol = node.get('protocol', 'ssh').lower()
        try:
            if protocol == 'ssh':
                return await execute_ssh_async(node, log_file_path, status_queue)
            elif protocol == 'telnet':
                return await execute_telnet_async(node, log_file_path, status_queue)
            else:
                raise ValueError(f"Unknown protocol: {protocol}")
        except (asyncio.TimeoutError, PromptTimeoutError, LogoutFailedError) as e:
            return node['nodename'], None, type(e).__name__
        except Exception as e:
            return node['nodename'], None, str(e)

    tasks = [run_node_task(node) for node in nodes]
    successful_logs = []
    for future in asyncio.as_completed(tasks):
        node_name, result, error = await future
        display.completed_count += 1
        if error:
            display.node_statuses[node_name] = error.lower()
        else:
            display.node_statuses[node_name] = 'success'
            if result:
                successful_logs.append(result)

    updater_task.cancel()
    handle_post_processing(successful_logs, output_dir, timestamp)

async def cycle_mode(stdscr, nodes, status_queue, stop_event, interval, output_dir, timestamp):
    def keyboard_interrupt_handler():
        stop_event.set()
    
    listener = keyboard.Listener(on_press=lambda key: keyboard_interrupt_handler() if key == keyboard.Key.esc else None)
    listener.start()

    display = ProgressDisplay(nodes, status_queue, is_cycle_mode=True)
    updater_task = asyncio.create_task(display.update(stdscr))

    async def run_node_cycle(node):
        log_file_path = os.path.join(output_dir, f"{node['nodename']}_{timestamp}.txt")
        protocol = node.get('protocol', 'ssh').lower()
        try:
            if protocol == 'ssh':
                await execute_ssh_async(node, log_file_path, status_queue, interval, stop_event)
            elif protocol == 'telnet':
                await execute_telnet_async(node, log_file_path, status_queue, interval, stop_event)
            else:
                raise ValueError(f"Unknown protocol: {protocol}")
        except (asyncio.TimeoutError, PromptTimeoutError, LogoutFailedError) as e:
            await status_queue.put({'node': node['nodename'], 'status': type(e).__name__.lower()})
        except Exception as e:
            await status_queue.put({'node': node['nodename'], 'status': 'error'})

    tasks = [run_node_cycle(node) for node in nodes]
    await asyncio.gather(*tasks)
    
    updater_task.cancel()
    log_files = [os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".txt")]
    handle_post_processing(log_files, output_dir, timestamp)
    listener.stop()

if __name__ == "__main__":
    try:
        curses.wrapper(main_wrapped)
    except Exception as e:
        print(f"Failed to run: {e}")