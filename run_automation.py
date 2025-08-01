import asyncio
import os
import sys
import zipfile
from datetime import datetime
import time
from config_parsers import parse_nodes_from_csv, parse_nodes_from_excel
from network_operations import execute_ssh_async, execute_telnet_async

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

def print_progress_bar(iteration, total, prefix='Progress:', suffix='Complete', length=50, fill='█'):
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration == total:
        print()
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
    BASE_NODE_TIMEOUT = 30.0 #基本1ノードにつき30秒確保します。
    SECONDS_PER_COMMAND = 5.0 #各コマンド実行に5秒の余裕を持たせます。
    PDRIVE = "ls -l" #ここは任意のコマンドを実行するための変数です。あれに変更してください。
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):

        print("Usage: python3 run_automation.py [path_to_input_file] [options]")
        print("\nArguments:")
        print("  path_to_input_file: コマンドファイルのファイルパス")
        print("\nOptions:")
        print("  --sheet [sheet_index]:エクセルのみ。対象シートを選べます。 デフォは2番目です")
        print(f"\nTimeout settings:")
        print(f"  1ノード辺りの基本タイムアウト時間： {BASE_NODE_TIMEOUT} 秒, コマンド1つにつき {SECONDS_PER_COMMAND} 追加します。")
        return
    input_file = sys.argv[1]
    nodes = None
    if input_file.lower().endswith('.csv'):
        print(f"\rCSV file: {input_file}                 _")
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
        print(f"\rExcel file: {input_file}, sheet: '{sheet_identifier}'")
        nodes = parse_nodes_from_excel(input_file, sheet_name=sheet_identifier)
    else:
        print(f"\r {input_file}は対象外です      ")
        return
    if nodes is None:
        print("\rファイルの構文エラーがあるかもしれません      ")
        return
    if not nodes:
        print(f"'{input_file}'の中にノードの指定が見つかりませんでした'{input_file}'.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"output_{timestamp}"
    if os.path.dirname(input_file): #入力ファイルの場所と同じ場所に出力する。この辺り権限で色々エラー怖い
        output_dir = os.path.join(os.path.dirname(input_file), output_dir)
    print(f"Output directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True) #ないわけないんだけどね、のこしときます
    #print(f"Created output directory: {output_dir}")

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
            print(f"\nタイムアウトでノードをスキップしました")
        except Exception as e:
            print(f"\nエラー: {e}")
        finally:
            completed_tasks += 1
            print_progress_bar(completed_tasks, total_tasks)

    print(f"\n全 {completed_tasks} のノードの取得が完了しました。")

    if successful_log_files:
        zip_filename = f"command_output_{timestamp}.zip"
        zip_destination = os.path.join(output_dir, zip_filename)
        create_zip_file(successful_log_files, zip_destination)
        post_command = f"{PDRIVE}{pdkey} {zip_destination}\n" 
        os.system(post_command)
if __name__ == "__main__":
    os.environ['LANG'] = 'ja_JP.UTF-8'  # 日本語環境を設定 本当は元にもどすべきなんでしょうね
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
