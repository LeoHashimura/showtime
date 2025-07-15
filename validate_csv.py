import csv
import sys
from itertools import zip_longest

def validate_csv(file_path):
    """
    Validates the structure and content of the node CSV file to ensure
    it's compatible with the runner scripts.
    """
    print(f"--- Starting validation of {file_path} ---")
    is_valid = True

    try:
        with open(file_path, 'r', newline='') as csvfile:
            reader = list(csv.reader(csvfile))
            if not reader:
                print("Error: CSV file is empty.")
                return False
    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return False
    except Exception as e:
        print(f"Error: Failed to read CSV file. Reason: {e}")
        return False

    # Transpose data to validate by columns (nodes)
    transposed_data = list(zip_longest(*reader, fillvalue=''))
    
    # 1. Validate Headers
    headers = [h.strip().lower() for h in transposed_data[0]]
    required_headers = {'nodename', 'protocol', 'ip_address', 'login_id', 'login_password'}
    
    missing_headers = required_headers - set(headers)
    if missing_headers:
        print(f"Error: CSV is missing required headers: {', '.join(missing_headers)}")
        is_valid = False
    else:
        print("✓ Headers are valid.")

    # 2. Validate each node column
    if len(transposed_data) < 2:
        print("Warning: CSV file contains headers but no node data.")
        return is_valid

    # Skip header column (index 0)
    for i in range(1, len(transposed_data)):
        node_column = transposed_data[i]
        # Create a dictionary for the current node for easier validation
        node_info = {headers[j]: node_column[j].strip() for j in range(len(headers)) if j < len(node_column)}
        
        node_identifier = node_info.get('nodename') or f"Column {i+1}"

        # Rule: Must have a nodename
        if not node_info.get('nodename'):
            print(f"Error in {node_identifier}: 'nodename' is missing or empty.")
            is_valid = False
        
        # Rule: Must have an ip_address
        if not node_info.get('ip_address'):
            print(f"Error in node '{node_identifier}': 'ip_address' is missing or empty.")
            is_valid = False

        # Rule: Protocol must be 'ssh' or 'telnet' if specified
        protocol = node_info.get('protocol', 'ssh').lower()
        if protocol and protocol not in ('ssh', 'telnet'):
            print(f"Error in node '{node_identifier}': Invalid protocol '{protocol}'. Must be 'ssh' or 'telnet'.")
            is_valid = False

    if is_valid:
        print(f"✓ All {len(transposed_data) - 1} node entries appear to be correctly formatted.")
    
    return is_valid


def main():
    """
    Main function to run the validator.
    """
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]
    else:
        csv_file = 'nodes.csv'

    if validate_csv(csv_file):
        print("\n--- Validation successful ---")
        sys.exit(0)
    else:
        print("\n--- Validation failed. Please correct the errors listed above. ---")
        sys.exit(1)

if __name__ == "__main__":
    main()
