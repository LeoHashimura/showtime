import pandas as pd
import csv
from itertools import zip_longest

def parse_nodes_from_csv(file_path):
    nodes = []
    config_headers = {
        'nodename', 'protocol', 'ip_address', 'login_id', 
        'login_password', 'additional_command_1', 'additional_command_2'
    }

    try:
        with open(file_path, 'r', newline='', encoding='utf-8-sig') as csvfile:
            reader = list(csv.reader(csvfile))
            if not reader:
                return []
            
            transposed_data = list(zip_longest(*reader, fillvalue=''))
            headers = [h.strip() for h in transposed_data[0]]

            for i in range(1, len(transposed_data)):
                node_info = {"commands": []}
                node_column = transposed_data[i]
                commands_ended = False

                for j, header in enumerate(headers):
                    value = node_column[j].strip() if j < len(node_column) else ""

                    if header not in config_headers:
                        if not value:
                            commands_ended = True
                        
                        if not commands_ended:
                            node_info["commands"].append(value)
                    else:
                        node_info[header] = value
                
                if node_info.get('nodename'):
                    nodes.append(node_info)

    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return None
    except Exception as e:
        print(f"An error occurred while parsing the CSV: {e}")
        return None
    return nodes

def parse_nodes_from_excel(file_path, sheet_name=1):
    nodes = []
    config_headers = {
        'nodename', 'protocol', 'ip_address', 'login_id',
        'login_password', 'additional_command_1', 'additional_command_2'
    }

    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=None, keep_default_na=False)

        if df.empty:
            return []

        headers = [str(h).strip() for h in df.iloc[:, 0]]

        for i in range(1, df.shape[1]):
            node_info = {"commands": []}
            node_column = df.iloc[:, i]
            commands_ended = False

            for j, header in enumerate(headers):
                value = str(node_column.iloc[j]).strip() if j < len(node_column) else ""

                if header not in config_headers:
                    if not value:
                        commands_ended = True
                    
                    if not commands_ended:
                        node_info["commands"].append(value)
                else:
                    node_info[header] = value
            
            if node_info.get('nodename'):
                nodes.append(node_info)

    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return None
    except Exception as e:
        print(f"An error occurred while parsing the Excel file: {e}")
        return None
    return nodes
