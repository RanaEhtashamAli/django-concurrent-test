"""
Utility functions for timing file management in django-concurrent-test.
"""

import json
import os
import warnings
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def load_test_timings(file_path="test_timings.json") -> Dict[str, Any]:
    """
    Load test timing data from a JSON file.
    
    Args:
        file_path (str): Path to the timing file
        
    Returns:
        Dict[str, Any]: Timing data dictionary, empty dict if file doesn't exist
    """
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        return {}
    except (json.JSONDecodeError, IOError) as e:
        warnings.warn(f"Failed to load test timings from {file_path}: {e}")
        logger.warning(f"Failed to load test timings from {file_path}: {e}")
        return {}


def save_test_timings(timings: Dict[str, Any], file_path="test_timings.json") -> bool:
    """
    Save test timing data to a JSON file.
    
    Args:
        timings (Dict[str, Any]): Timing data to save
        file_path (str): Path to the timing file
        
    Returns:
        bool: True if save was successful, False otherwise
    """
    try:
        with open(file_path, 'w') as f:
            json.dump(timings, f, indent=2)
        return True
    except IOError as e:
        warnings.warn(f"Failed to save test timings to {file_path}: {e}")
        logger.warning(f"Failed to save test timings to {file_path}: {e}")
        return False


def update_test_timing(timings: Dict[str, Any], test_id: str, duration: float) -> Dict[str, Any]:
    """
    Update timing data for a specific test.
    
    Args:
        timings (Dict[str, Any]): Current timing data
        test_id (str): Test identifier
        duration (float): Test duration in seconds
        
    Returns:
        Dict[str, Any]: Updated timing data
    """
    timings[test_id] = duration
    return timings


def get_average_timing(timings: Dict[str, Any]) -> float:
    """
    Calculate average timing from timing data.
    
    Args:
        timings (Dict[str, Any]): Timing data dictionary
        
    Returns:
        float: Average timing in seconds, 0.0 if no data
    """
    if not timings:
        return 0.0
    
    values = list(timings.values())
    return sum(values) / len(values)


def get_slowest_tests(timings: Dict[str, Any], count: int = 10) -> list:
    """
    Get the slowest tests from timing data.
    
    Args:
        timings (Dict[str, Any]): Timing data dictionary
        count (int): Number of slowest tests to return
        
    Returns:
        list: List of (test_id, duration) tuples, sorted by duration descending
    """
    if not timings:
        return []
    
    sorted_tests = sorted(timings.items(), key=lambda x: x[1], reverse=True)
    return sorted_tests[:count]


def get_fastest_tests(timings: Dict[str, Any], count: int = 10) -> list:
    """
    Get the fastest tests from timing data.
    
    Args:
        timings (Dict[str, Any]): Timing data dictionary
        count (int): Number of fastest tests to return
        
    Returns:
        list: List of (test_id, duration) tuples, sorted by duration ascending
    """
    if not timings:
        return []
    
    sorted_tests = sorted(timings.items(), key=lambda x: x[1])
    return sorted_tests[:count]


def merge_timing_dicts(timings1: Dict[str, Any], timings2: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two timing dictionaries, preferring newer data.
    
    Args:
        timings1 (Dict[str, Any]): First timing dictionary
        timings2 (Dict[str, Any]): Second timing dictionary
        
    Returns:
        Dict[str, Any]: Merged timing dictionary
    """
    merged = timings1.copy()
    merged.update(timings2)
    return merged


def filter_timings_by_pattern(timings: Dict[str, Any], pattern: str) -> Dict[str, Any]:
    """
    Filter timing data by test ID pattern.
    
    Args:
        timings (Dict[str, Any]): Timing data dictionary
        pattern (str): Pattern to match against test IDs
        
    Returns:
        Dict[str, Any]: Filtered timing data
    """
    import re
    
    filtered = {}
    regex = re.compile(pattern)
    
    for test_id, duration in timings.items():
        if regex.search(test_id):
            filtered[test_id] = duration
    
    return filtered


def export_timings_csv(timings: Dict[str, Any], file_path: str) -> bool:
    """
    Export timing data to CSV format.
    
    Args:
        timings (Dict[str, Any]): Timing data dictionary
        file_path (str): Path to the CSV file
        
    Returns:
        bool: True if export was successful, False otherwise
    """
    try:
        import csv
        
        with open(file_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['test_id', 'duration_seconds'])
            
            for test_id, duration in timings.items():
                writer.writerow([test_id, duration])
        
        return True
    except IOError as e:
        warnings.warn(f"Failed to export timings to CSV {file_path}: {e}")
        logger.warning(f"Failed to export timings to CSV {file_path}: {e}")
        return False


def import_timings_csv(file_path: str) -> Dict[str, Any]:
    """
    Import timing data from CSV format.
    
    Args:
        file_path (str): Path to the CSV file
        
    Returns:
        Dict[str, Any]: Timing data dictionary
    """
    try:
        import csv
        
        timings = {}
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                test_id = row.get('test_id', '')
                duration = float(row.get('duration_seconds', 0))
                if test_id:
                    timings[test_id] = duration
        
        return timings
    except (IOError, ValueError) as e:
        warnings.warn(f"Failed to import timings from CSV {file_path}: {e}")
        logger.warning(f"Failed to import timings from CSV {file_path}: {e}")
        return {}


# Enhanced timing utilities with better structure
def load_timings(file_path: str) -> Dict[str, Any]:
    """
    Load timing data from JSON file with enhanced error handling.
    
    Args:
        file_path: Path to the JSON timing file
        
    Returns:
        Dictionary containing timing data
    """
    return load_test_timings(file_path)


def save_timings(timings: Dict[str, Any], file_path: str) -> bool:
    """
    Save timing data to JSON file with enhanced error handling.
    
    Args:
        timings: Timing data to save
        file_path: Path to the JSON file
        
    Returns:
        True if successful, False otherwise
    """
    return save_test_timings(timings, file_path)


def filter_timings(timings: Dict[str, Any], min_duration: float = 0.0, max_duration: float = float('inf')) -> Dict[str, Any]:
    """
    Filter timing data by duration range.
    
    Args:
        timings: Timing data dictionary
        min_duration: Minimum duration threshold
        max_duration: Maximum duration threshold
        
    Returns:
        Filtered timing data
    """
    filtered = {}
    for test_id, timing_data in timings.items():
        if isinstance(timing_data, dict) and 'duration' in timing_data:
            duration = timing_data['duration']
        elif isinstance(timing_data, (int, float)):
            duration = timing_data
        else:
            continue
            
        if min_duration <= duration <= max_duration:
            filtered[test_id] = timing_data
    
    return filtered


def merge_timings(timings1: Dict[str, Any], timings2: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two timing datasets, preferring newer data.
    
    Args:
        timings1: First timing dataset
        timings2: Second timing dataset
        
    Returns:
        Merged timing dataset
    """
    merged = timings1.copy()
    merged.update(timings2)
    return merged


def import_timings_from_csv(file_path: str) -> Dict[str, Any]:
    """
    Import timing data from CSV file.
    
    Args:
        file_path: Path to the CSV file
        
    Returns:
        Dictionary containing timing data
    """
    return import_timings_csv(file_path)


def export_timings_to_csv(timings: Dict[str, Any], file_path: str) -> bool:
    """
    Export timing data to CSV file.
    
    Args:
        timings: Timing data to export
        file_path: Path to the CSV file
        
    Returns:
        True if successful, False otherwise
    """
    return export_timings_csv(timings, file_path) 