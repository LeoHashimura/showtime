

import os
from config_parsers import parse_nodes_from_excel
import pprint

# Get the absolute path to the directory where this script is located
script_dir = os.path.dirname(os.path.abspath(__file__))
# Construct the full path to the nodes.xlsx file
xlsx_file_path = os.path.join(script_dir, "nodes.xlsx")

print(f"--- Attempting to parse: {xlsx_file_path} ---")

try:
    # Call the function we want to debug
    parsed_data = parse_nodes_from_excel(xlsx_file_path)

    if parsed_data is None:
        print("\n--- Result: The function returned None (indicating an error was handled internally) ---")
    elif not parsed_data:
        print("\n--- Result: The function returned an empty list (no nodes found or file is empty) ---")
    else:
        print("\n--- Result: Success! Parsed Data Below ---")
        pprint.pprint(parsed_data)

except Exception as e:
    print(f"\n--- An unhandled exception occurred! ---")
    # Print the full traceback to diagnose the issue
    import traceback
    traceback.print_exc()


