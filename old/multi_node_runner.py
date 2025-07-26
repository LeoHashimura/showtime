import csv
import os
import telnetlib
import zipfile
from datetime import datetime
import time

# Third-party library: paramiko. Please install it using: pip install paramiko
try:
    import paramiko
except ImportError:
    print("Error: The 'paramiko' library is required for SSH connections.")
    print("Please install it by running: pip install paramiko")
    exit()

def parse_nodes_from_csv(file_path):
    """
    Parses the CSV file where each column represents a node.
    It transposes the data so we can work with it more easily.
    """
    nodes = []
    try:
        with open(file_path, 'r', newline='') as csvfile:
            reader = list(csv.reader(csvfile))
            if not reader:
                return []

            # Transpose the data: columns become rows
            transposed_data = list(map(list, zip(*reader)))

            # First column (now first row) contains the field names
            headers = [h.strip() for h in transposed_data[0]]
            
            # Process each subsequent column (now a row) as a node
            for i in range(1, len(transposed_data)):
                node_info = {"commands": []}
                node_column = transposed_data[i]
                
                for j, header in enumerate(headers):
                    value = node_column[j].strip() if j < len(node_column) else ""
                    if header.startswith("command"):
                        if value: # Only add non-empty commands
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

def execute_telnet(node_info):
    """
    Connects to a node using Telnet and executes commands.
    """
    output_log = f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via Telnet ---\n"
    try:
        tn = telnetlib.Telnet(node_info['ip_address'], timeout=10)

        # Wait for login prompt and send username
        tn.read_until(b"Username: ", timeout=5)
        tn.write(node_info['login_id'].encode('ascii') + b"\n")
        
        # Wait for password prompt and send password
        tn.read_until(b"Password: ", timeout=5)
        tn.write(node_info['login_password'].encode('ascii') + b"\n")
        
        time.sleep(1) # Wait for login to complete
        
        # Read initial output until we see a prompt
        initial_output = tn.read_very_eager().decode('ascii')
        output_log += initial_output

        # Check if the prompt is '>' for additional commands
        if ">" in initial_output:
            output_log += f"\n>>> Prompt is '>'. Sending additional command.\n"
            tn.write(node_info['additional_command_1'].encode('ascii') + b"\n")
            time.sleep(1)
            output_log += tn.read_very_eager().decode('ascii')

        # Execute main commands
        for cmd in node_info['commands']:
            output_log += f"\n>>> Executing command: {cmd}\n"
            tn.write(cmd.encode('ascii') + b"\n")
            time.sleep(2) # Give time for command to execute
            output_log += tn.read_very_eager().decode('ascii')
            
        tn.write(b"exit\n")
        tn.close()
        output_log += "\n--- Disconnected ---"
        
    except Exception as e:
        output_log += f"\n*** ERROR: Failed to connect or execute commands on {node_info['nodename']}. Reason: {e} ***\n"
        
    return output_log

def execute_ssh(node_info):
    """
    Connects to a node using SSH and executes commands.
    """
    output_log = f"--- Connecting to {node_info['nodename']} ({node_info['ip_address']}) via SSH ---\n"
    try:
        client = paramiko.SSHClient()
        # In a real-world scenario, you should manage host keys properly.
        # For this script, we will automatically add the key.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        client.connect(
            node_info['ip_address'],
            port=22,
            username=node_info['login_id'],
            password=node_info['login_password'],
            timeout=10,
            look_for_keys=False,
            allow_agent=False
        )

        # Invoke an interactive shell
        shell = client.invoke_shell()
        time.sleep(1)
        
        # Read the initial banner/prompt
        initial_output = shell.recv(65535).decode('utf-8')
        output_log += initial_output
        
        # Check if the prompt is '>' for additional commands
        if ">" in initial_output:
            output_log += f"\n>>> Prompt is '>'. Sending additional command.\n"
            shell.send(node_info['additional_command_1'] + '\n')
            time.sleep(2)
            output_log += shell.recv(65535).decode('utf-8')

        # Execute main commands
        for cmd in node_info['commands']:
            output_log += f"\n>>> Executing command: {cmd}\n"
            shell.send(cmd + '\n')
            time.sleep(2) # Give time for command to execute
            output_log += shell.recv(65535).decode('utf-8')
            
        shell.close()
        client.close()
        output_log += "\n--- Disconnected ---"

    except Exception as e:
        output_log += f"\n*** ERROR: Failed to connect or execute commands on {node_info['nodename']}. Reason: {e} ***\n"
        
    return output_log

def create_zip_file(files_to_zip, zip_filename):
    """
    Creates a zip archive containing the specified files.
    """
    try:
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in files_to_zip:
                zf.write(file, os.path.basename(file))
        print(f"Successfully created zip file: {zip_filename}")
    except Exception as e:
        print(f"Error: Failed to create zip file. Reason: {e}")


def main():
    """
    Main function to orchestrate the process.
    """
    csv_file = 'nodes.csv'
    
    # Generate a unique timestamp for the output files
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"command_output_{timestamp}.zip"
    
    nodes = parse_nodes_from_csv(csv_file)
    
    if nodes is None:
        print("Halting script due to CSV parsing errors.")
        return

    if not nodes:
        print(f"No nodes found in the CSV file '{csv_file}'. Please create it.")
        return

    log_files = []
    # Create a directory to store output files
    output_dir = f"output_{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    print(f"Created output directory: {output_dir}")

    for node in nodes:
        print(f"Processing node: {node['nodename']}...")
        log = ""
        protocol = node.get('protocol', 'ssh').lower() # Default to ssh if not specified
        
        if protocol == 'ssh':
            log = execute_ssh(node)
        elif protocol == 'telnet':
            log = execute_telnet(node)
        else:
            log = f"*** SKIPPING: Unknown protocol '{protocol}' for node {node['nodename']} ***"
        
        # Create individual log file for the node
        log_filename = os.path.join(output_dir, f"{node['nodename']}_{timestamp}.txt")
        try:
            with open(log_filename, 'w') as f:
                f.write(log)
            print(f"Output for {node['nodename']} saved to: {log_filename}")
            log_files.append(log_filename)
        except Exception as e:
            print(f"Error writing log file for {node['nodename']}. Reason: {e}")

    # Create a zip file with all the individual logs
    if log_files:
        create_zip_file(log_files, zip_filename)
    else:
        print("No log files were generated to zip.")

    print("\n=====================================================")
    print("Script finished. All operations are complete.")
    print("=====================================================")


if __name__ == "__main__":
    main()
