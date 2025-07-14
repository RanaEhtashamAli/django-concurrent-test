"""
Pytest plugin for django-concurrent-test package.
"""

import pytest
import logging
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.test import TestCase
from django.db import connection

from .runner import ConcurrentTestRunner
from .exceptions import ConcurrentTestException, TestTimeoutException
from .timing_utils import load_timings, save_timings, filter_timings, merge_timings, import_timings_from_csv, export_timings_to_csv

# Configure logging for concurrent tests
logger = logging.getLogger(__name__)

def pytest_addoption(parser):
    """Add command-line options for concurrent testing."""
    group = parser.getgroup('concurrent', 'Concurrent testing options')
    
    group.addoption(
        '--concurrent',
        action='store_true',
        default=False,
        help='Enable concurrent test execution'
    )
    
    group.addoption(
        '--workers',
        type=int,
        default=None,
        help='Number of worker processes (default: auto-detect)'
    )
    
    group.addoption(
        '--timeout',
        type=int,
        default=300,
        help='Global timeout in seconds for all tests (default: 300)'
    )
    
    group.addoption(
        '--test-timeout',
        type=int,
        default=60,
        help='Timeout in seconds for individual tests (default: 60)'
    )
    
    group.addoption(
        '--worker-timeout',
        type=int,
        default=120,
        help='Timeout in seconds for worker processes (default: 120)'
    )
    
    group.addoption(
        '--export-timings',
        type=str,
        default=None,
        help='Export test timings to JSON file'
    )
    
    group.addoption(
        '--import-timings',
        type=str,
        default=None,
        help='Import test timings from JSON file'
    )
    
    group.addoption(
        '--export-timings-csv',
        type=str,
        default=None,
        help='Export test timings to CSV file'
    )
    
    group.addoption(
        '--import-timings-csv',
        type=str,
        default=None,
        help='Import test timings from CSV file'
    )

def pytest_configure(config):
    """Configure pytest for concurrent testing."""
    if config.getoption('--concurrent'):
        # Register the concurrent test runner
        config.pluginmanager.register(ConcurrentTestPlugin(config), 'concurrent_test_plugin')
        
        # Set up logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [CONCURRENT] %(levelname)s: %(message)s'
        )

def pytest_sessionstart(session):
    """Handle session start for concurrent testing."""
    if session.config.getoption('--concurrent'):
        logger.info("Starting concurrent test session")
        
        # Import timings if specified
        import_timings_path = session.config.getoption('--import-timings')
        if import_timings_path:
            try:
                timings = load_timings(import_timings_path)
                logger.info(f"Imported {len(timings)} timing records from {import_timings_path}")
            except Exception as e:
                logger.warning(f"Failed to import timings from {import_timings_path}: {e}")

def pytest_sessionfinish(session, exitstatus):
    """Handle session finish for concurrent testing."""
    if session.config.getoption('--concurrent'):
        logger.info("Finishing concurrent test session")
        
        # Export timings if specified
        export_timings_path = session.config.getoption('--export-timings')
        if export_timings_path:
            try:
                # Get timings from the plugin if available
                plugin = session.config.pluginmanager.get_plugin('concurrent_test_plugin')
                if hasattr(plugin, 'timings') and plugin.timings:
                    save_timings(plugin.timings, export_timings_path)
                    logger.info(f"Exported {len(plugin.timings)} timing records to {export_timings_path}")
            except Exception as e:
                logger.warning(f"Failed to export timings to {export_timings_path}: {e}")
        
        # Export CSV timings if specified
        export_csv_path = session.config.getoption('--export-timings-csv')
        if export_csv_path:
            try:
                plugin = session.config.pluginmanager.get_plugin('concurrent_test_plugin')
                if hasattr(plugin, 'timings') and plugin.timings:
                    export_timings_to_csv(plugin.timings, export_csv_path)
                    logger.info(f"Exported {len(plugin.timings)} timing records to CSV {export_csv_path}")
            except Exception as e:
                logger.warning(f"Failed to export CSV timings to {export_csv_path}: {e}")

class ConcurrentTestPlugin:
    """Pytest plugin for concurrent test execution."""
    
    def __init__(self, config):
        self.config = config
        self.runner = None
        self.timings = {}
        self._lock = threading.Lock()
        
        # Get configuration options
        self.workers = config.getoption('--workers')
        self.timeout = config.getoption('--timeout')
        self.test_timeout = config.getoption('--test-timeout')
        self.worker_timeout = config.getoption('--worker-timeout')
        
        # Import CSV timings if specified
        import_csv_path = config.getoption('--import-timings-csv')
        if import_csv_path:
            try:
                csv_timings = import_timings_from_csv(import_csv_path)
                with self._lock:
                    self.timings.update(csv_timings)
                logger.info(f"Imported {len(csv_timings)} timing records from CSV {import_csv_path}")
            except Exception as e:
                logger.warning(f"Failed to import CSV timings from {import_csv_path}: {e}")
    
    def pytest_runtest_protocol(self, item, nextitem):
        """Execute test with concurrent runner."""
        if not self.runner:
            self.runner = ConcurrentTestRunner(
                workers=self.workers,
                timeout=self.timeout,
                test_timeout=self.test_timeout,
                worker_timeout=self.worker_timeout
            )
        
        # Check if this is a Django test case
        if not hasattr(item, 'obj') or not hasattr(item.obj, '__self__'):
            return None  # Let pytest handle non-Django tests
        
        test_instance = item.obj.__self__
        if not isinstance(test_instance, TestCase):
            return None  # Let pytest handle non-TestCase tests
        
        # Execute test with timeout
        start_time = time.time()
        test_name = f"{test_instance.__class__.__name__}.{item.name}"
        
        logger.info(f"Starting test: {test_name}")
        
        try:
            # Execute test with timeout context manager
            with self.runner._timeout_context(self.test_timeout):
                result = item.runtest()
            
            # Record timing
            duration = time.time() - start_time
            with self._lock:
                self.timings[test_name] = {
                    'duration': duration,
                    'status': 'passed',
                    'timestamp': time.time()
                }
            
            logger.info(f"Completed test: {test_name} in {duration:.2f}s")
            return result
            
        except TestTimeoutException:
            duration = time.time() - start_time
            with self._lock:
                self.timings[test_name] = {
                    'duration': duration,
                    'status': 'timeout',
                    'timestamp': time.time()
                }
            
            logger.error(f"Test timed out: {test_name} after {duration:.2f}s")
            pytest.fail(f"Test timed out after {self.test_timeout} seconds")
            
        except Exception as e:
            duration = time.time() - start_time
            with self._lock:
                self.timings[test_name] = {
                    'duration': duration,
                    'status': 'failed',
                    'error': str(e),
                    'timestamp': time.time()
                }
            
            logger.error(f"Test failed: {test_name} after {duration:.2f}s - {e}")
            raise

def _execute_test_with_timeout(test_func, timeout_seconds):
    """Execute a test function with a timeout using threading."""
    result = {'success': False, 'error': None, 'duration': 0}
    
    def run_test():
        try:
            start_time = time.time()
            test_func()
            result['duration'] = time.time() - start_time
            result['success'] = True
        except Exception as e:
            result['error'] = str(e)
    
    thread = threading.Thread(target=run_test)
    thread.daemon = True
    thread.start()
    thread.join(timeout=timeout_seconds)
    
    if thread.is_alive():
        raise TestTimeoutException(f"Test timed out after {timeout_seconds} seconds")
    
    if not result['success']:
        raise Exception(result['error'])
    
    return result['duration']

def _get_worker_log_prefix(worker_id: int, database_name: str) -> str:
    """Generate a consistent log prefix for worker identification."""
    return f"[WORKER-{worker_id:02d}:{database_name}]"

def _log_worker_start(worker_id: int, database_name: str, test_count: int):
    """Log worker startup information."""
    logger.info(f"{_get_worker_log_prefix(worker_id, database_name)} Starting with {test_count} tests")

def _log_worker_complete(worker_id: int, database_name: str, completed_tests: int, total_duration: float):
    """Log worker completion information."""
    logger.info(f"{_get_worker_log_prefix(worker_id, database_name)} Completed {completed_tests} tests in {total_duration:.2f}s")

def _log_test_result(worker_id: int, database_name: str, test_name: str, duration: float, status: str):
    """Log individual test results."""
    logger.info(f"{_get_worker_log_prefix(worker_id, database_name)} {test_name}: {status} ({duration:.2f}s)")

def _generate_benchmark_json(timings: Dict[str, Any], output_path: str):
    """Generate benchmark JSON output with detailed statistics."""
    if not timings:
        logger.warning("No timing data available for benchmark output")
        return
    
    # Calculate statistics
    durations = [t['duration'] for t in timings.values() if 'duration' in t]
    
    if not durations:
        logger.warning("No valid duration data for benchmark statistics")
        return
    
    benchmark_data = {
        'summary': {
            'total_tests': len(timings),
            'passed_tests': len([t for t in timings.values() if t.get('status') == 'passed']),
            'failed_tests': len([t for t in timings.values() if t.get('status') == 'failed']),
            'timeout_tests': len([t for t in timings.values() if t.get('status') == 'timeout']),
            'total_duration': sum(durations),
            'average_duration': sum(durations) / len(durations),
            'min_duration': min(durations),
            'max_duration': max(durations),
            'median_duration': sorted(durations)[len(durations) // 2]
        },
        'tests': timings,
        'metadata': {
            'generated_at': time.time(),
            'version': '1.0.0'
        }
    }
    
    try:
        with open(output_path, 'w') as f:
            json.dump(benchmark_data, f, indent=2)
        logger.info(f"Benchmark data exported to {output_path}")
    except Exception as e:
        logger.error(f"Failed to export benchmark data: {e}") 