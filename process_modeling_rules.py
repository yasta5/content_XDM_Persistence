#!/usr/bin/env python3
"""
Script to update ModelingRules schema files based on input JSON structure.
Updates type from 'string' to 'nested_json' for JSON fields and is_array from false to true for array fields.
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any

# Base paths
PACKS_DIR = Path("Packs")
TEST_DATA_DIR = Path("/Users/yasta/dev/demisto/xql-content/content_source/xql_ingestion_files/xql_ingestion_rules_testdata")
LOG_FILE = Path("ingestion_rules_changes/modifications.log")

def get_highest_version_folder(modeling_rules_path: Path) -> Path | None:
    """Find the subfolder with the highest version number suffix, or the only folder if no version suffix exists."""
    if not modeling_rules_path.exists():
        return None
    
    version_folders = []
    non_version_folders = []
    
    for item in modeling_rules_path.iterdir():
        if item.is_dir():
            # Extract version number from folder name (e.g., "RuleName_1_3" -> (1, 3))
            match = re.search(r'_(\d+)_(\d+)$', item.name)
            if match:
                major, minor = int(match.group(1)), int(match.group(2))
                version_folders.append((major, minor, item))
            else:
                non_version_folders.append(item)
    
    # If we have version folders, return the highest version
    if version_folders:
        version_folders.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return version_folders[0][2]
    
    # If no version folders but we have exactly one non-version folder, return it
    if len(non_version_folders) == 1:
        return non_version_folders[0]
    
    return None

def find_schema_file(version_folder: Path) -> Path | None:
    """Find the schema JSON file in the version folder."""
    for file in version_folder.iterdir():
        if file.is_file() and file.name.endswith('_schema.json'):
            return file
    return None

def find_xif_file(version_folder: Path) -> Path | None:
    """Find the XIF file in the version folder."""
    for file in version_folder.iterdir():
        if file.is_file() and file.name.endswith('.xif'):
            return file
    return None

def extract_dataset_from_xif(xif_file: Path) -> str | None:
    """Extract dataset name from XIF file."""
    try:
        with open(xif_file, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            # Look for [MODEL: dataset=<dataset_name>]
            match = re.search(r'\[MODEL:\s*dataset\s*=\s*([^\]]+)\]', first_line)
            if match:
                dataset = match.group(1).strip()
                # Remove quotes if present
                dataset = dataset.strip('"').strip("'")
                # Remove _raw suffix if present to get the base dataset name
                if dataset.endswith('_raw'):
                    dataset = dataset[:-4]
                return dataset
    except Exception as e:
        print(f"  Warning: Could not read XIF file {xif_file}: {e}")
    return None

def find_test_data_dir(dataset: str) -> Path | None:
    """Find the test data directory matching the dataset name."""
    if not TEST_DATA_DIR.exists():
        return None
    
    # Test directories follow pattern: <vendor>-<product>-<dataset>
    # The dataset should match the last part of the directory name
    for test_dir in TEST_DATA_DIR.iterdir():
        if test_dir.is_dir():
            # Get the last part after the last hyphen
            parts = test_dir.name.split('-')
            if len(parts) >= 3:
                test_dataset = parts[-1]
                # Compare datasets (case-insensitive, ignoring underscores/hyphens)
                if dataset.lower().replace('_', '').replace('-', '') == test_dataset.lower().replace('_', '').replace('-', ''):
                    return test_dir
    return None

def read_input_json_files(test_data_dir: Path) -> List[Tuple[Path, Dict]]:
    """Read all input JSON files with format:json in their header."""
    input_files = []
    
    for file in test_data_dir.iterdir():
        if file.is_file() and file.name.startswith('input_') and file.name.endswith('.json'):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    # Read first line (header)
                    first_line = f.readline().strip()
                    header = json.loads(first_line)
                    
                    # Check if format is "json"
                    if header.get('format', '').lower() == 'json':
                        # Read second line (actual data)
                        second_line = f.readline().strip()
                        if second_line:
                            data = json.loads(second_line)
                            input_files.append((file, data))
            except (json.JSONDecodeError, Exception) as e:
                print(f"  Warning: Could not parse {file}: {e}")
    
    return input_files

def analyze_json_structure(data: Any, prefix: str = "") -> Dict[str, Dict[str, Any]]:
    """
    Analyze JSON structure to identify field types.
    Returns a dict mapping field names to their properties (is_array, is_nested_json).
    
    Rules:
    - If field is a dict (nested JSON object): is_nested_json=True, is_array=False
    - If field is an array of scalars: is_array=True, is_nested_json=False
    - If field is an array of objects: is_array=True, is_nested_json=False (just mark as array)
    - If field is a scalar: is_array=False, is_nested_json=False
    """
    field_info = {}
    
    if isinstance(data, dict):
        for key, value in data.items():
            field_path = f"{prefix}.{key}" if prefix else key
            
            if isinstance(value, dict):
                # This is a nested JSON object (not an array)
                field_info[key] = {'is_array': False, 'is_nested_json': True}
                # Don't recursively analyze - we only care about top-level fields
            elif isinstance(value, list):
                # This is an array - just mark it as array, don't change type to nested_json
                field_info[key] = {'is_array': True, 'is_nested_json': False}
            else:
                # Scalar value
                field_info[key] = {'is_array': False, 'is_nested_json': False}
    
    elif isinstance(data, list):
        # If the root is an array, analyze first element
        if data and isinstance(data[0], dict):
            return analyze_json_structure(data[0], prefix)
    
    return field_info

def update_schema_file(schema_file: Path, field_info: Dict[str, Dict[str, Any]]) -> List[str]:
    """
    Update schema file with correct type and is_array values.
    Returns list of changes made.
    """
    changes = []
    
    try:
        with open(schema_file, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        
        # The schema has a top-level key (e.g., "microsoft_365_defender_raw") containing the fields
        # Find the first (and typically only) top-level key
        if schema:
            dataset_key = list(schema.keys())[0]
            fields = schema[dataset_key]
            
            for field_name, field_config in fields.items():
                if field_name in field_info:
                    info = field_info[field_name]
                    
                    # Update type for nested JSON
                    if info['is_nested_json'] and field_config.get('type') == 'string':
                        field_config['type'] = 'nested_json'
                        changes.append(f"Updated '{field_name}' field type from 'string' to 'nested_json'.")
                    
                    # Update is_array
                    if info['is_array'] and field_config.get('is_array') == False:
                        field_config['is_array'] = True
                        changes.append(f"Updated '{field_name}' field 'is_array' property from 'false' to 'true'.")
        
        # Write back if changes were made
        if changes:
            with open(schema_file, 'w', encoding='utf-8') as f:
                json.dump(schema, f, indent=2, ensure_ascii=False)
                f.write('\n')  # Add trailing newline
    
    except Exception as e:
        print(f"  Error updating schema file {schema_file}: {e}")
    
    return changes

def log_changes(pack_name: str, schema_file: Path, changes: List[str]):
    """Append changes to the modifications log file."""
    if not changes:
        return
    
    # Ensure log directory exists
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Append to log file
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"\nFile: {schema_file}\n")
        f.write("Changes:\n")
        for i, change in enumerate(changes, 1):
            f.write(f"{i}. {change}\n")

def process_version_folder(pack_name: str, version_folder: Path) -> bool:
    """Process a single version folder. Returns True if changes were made."""
    # Find schema file
    schema_file = find_schema_file(version_folder)
    if not schema_file:
        print(f"    No schema file found in {version_folder.name}")
        return False
    
    # Find XIF file to get dataset name
    xif_file = find_xif_file(version_folder)
    if not xif_file:
        print(f"    No XIF file found in {version_folder.name}")
        return False
    
    # Extract dataset from XIF
    dataset = extract_dataset_from_xif(xif_file)
    if not dataset:
        print(f"    Could not extract dataset from {xif_file.name}")
        return False
    
    print(f"    Dataset: {dataset}")
    
    # Find test data directory
    test_data_dir = find_test_data_dir(dataset)
    if not test_data_dir:
        print(f"    No test data directory found for dataset '{dataset}'")
        return False
    
    print(f"    Test data: {test_data_dir.name}")
    
    # Read input JSON files
    input_files = read_input_json_files(test_data_dir)
    if not input_files:
        print(f"    No JSON format input files found in {test_data_dir}")
        return False
    
    print(f"    Found {len(input_files)} JSON input file(s)")
    
    # Analyze all input files and merge field info
    merged_field_info = {}
    for input_file, data in input_files:
        field_info = analyze_json_structure(data)
        # Merge with existing info (union of all fields)
        for field_name, info in field_info.items():
            if field_name not in merged_field_info:
                merged_field_info[field_name] = info
            else:
                # If any file shows it as array or nested_json, mark it as such
                merged_field_info[field_name]['is_array'] |= info['is_array']
                merged_field_info[field_name]['is_nested_json'] |= info['is_nested_json']
    
    # Update schema file
    changes = update_schema_file(schema_file, merged_field_info)
    
    if changes:
        print(f"    ✓ Updated {schema_file.name} with {len(changes)} change(s)")
        log_changes(pack_name, schema_file, changes)
        return True
    else:
        print(f"    No changes needed for {schema_file.name}")
        return False

def process_pack(pack_path: Path) -> int:
    """Process a single pack. Returns number of version folders updated."""
    pack_name = pack_path.name
    modeling_rules_path = pack_path / "ModelingRules"
    
    if not modeling_rules_path.exists():
        return 0
    
    # Find highest version folder
    version_folder = get_highest_version_folder(modeling_rules_path)
    if not version_folder:
        print(f"  No version folder found in {modeling_rules_path}")
        return 0
    
    print(f"  Version folder: {version_folder.name}")
    
    # Process the version folder
    if process_version_folder(pack_name, version_folder):
        return 1
    return 0

def main():
    """Main processing function."""
    print("Starting ModelingRules schema update process...")
    print(f"Packs directory: {PACKS_DIR}")
    print(f"Test data directory: {TEST_DATA_DIR}")
    print()
    
    # Get all packs with ModelingRules
    packs_with_modeling_rules = []
    for pack_path in sorted(PACKS_DIR.iterdir()):
        if pack_path.is_dir() and (pack_path / "ModelingRules").exists():
            packs_with_modeling_rules.append(pack_path)
    
    print(f"Found {len(packs_with_modeling_rules)} packs with ModelingRules")
    print()
    
    # Process each pack
    total_updated = 0
    for i, pack_path in enumerate(packs_with_modeling_rules, 1):
        print(f"[{i}/{len(packs_with_modeling_rules)}] Processing {pack_path.name}...")
        total_updated += process_pack(pack_path)
    
    print()
    print(f"Processing complete! Updated {total_updated} pack(s).")
    print(f"Changes logged to: {LOG_FILE}")

if __name__ == "__main__":
    main()
