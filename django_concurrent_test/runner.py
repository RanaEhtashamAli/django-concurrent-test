"""
Production-grade concurrent test runner for Django with enhanced metrics, 
JUnit reporting, adaptive workload balancing, and template database caching.
"""

import os
import sys
import time
import json
import logging
import threading
import signal
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict, deque

from django.test.runner import DiscoverRunner
from django.conf import settings
from django.db import connection, connections
from django.test.utils import override_settings
from django.db.backends.base.base import BaseDatabaseWrapper

from .exceptions import (
    DatabaseTemplateException,
    WorkerTimeout,
    SecurityException,
    UnsupportedDatabase,
    WorkerRetryException,
)
from .security import (
    validate_environment,
    get_safe_worker_count,
    check_telemetry_disabled,
)
from .db import (
    setup_test_databases,
    setup_test_databases_with_connections,
    teardown_test_databases,
    teardown_test_databases_with_connections,
    worker_database,
    worker_database_with_isolation,
    verify_database_isolation,
    clear_connection_pool,
    get_connection_pool_stats,
    get_database_cloner,
)

logger = logging.getLogger(__name__)


class TestTimeoutError(Exception):
    """Custom timeout exception for test execution."""
    pass


@contextmanager
def time_limit(seconds):
    """Context manager for timeout handling."""
    def signal_handler(signum, frame):
        raise TestTimeoutError(f"Operation timed out after {seconds} seconds")
    
    # Set the signal handler and a 5-second alarm
    old_handler = signal.signal(signal.SIGALRM, signal_handler)
    signal.alarm(seconds)
    
    try:
        yield
    finally:
        # Disable the alarm
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


@dataclass
class TestMetrics:
    """Metrics for a single test execution."""
    test_id: str
    duration: float
    queries: int
    query_time: float
    writes: int
    reads: int
    memory_usage: float
    status: str  # 'success', 'failure', 'skip', 'error'
    error_message: Optional[str] = None
    worker_id: Optional[int] = None


@dataclass
class WorkerMetrics:
    """Metrics for a worker thread."""
    worker_id: int
    start_time: float
    end_time: float
    tests_run: int
    tests_failed: int
    tests_skipped: int
    total_queries: int
    total_query_time: float
    total_writes: int
    total_reads: int
    memory_peak: float
    connection_errors: int
    retry_count: int
    database_name: str


class AdaptiveChunker:
    """Adaptive test chunking based on historical timing data."""
    
    def __init__(self, timing_file: str = "test_timings.json"):
        self.timing_file = timing_file
        self.timings = self._load_timings()
        self._lock = threading.Lock()
    
    def _load_timings(self) -> Dict[str, float]:
        """Load historical test timings."""
        try:
            if os.path.exists(self.timing_file):
                with open(self.timing_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load test timings: {e}")
        return {}
    
    def _save_timings(self):
        """Save current timings to file."""
        try:
            with open(self.timing_file, 'w') as f:
                json.dump(self.timings, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save test timings: {e}")
    
    def update_timing(self, test_id: str, duration: float):
        """Update timing for a test."""
        with self._lock:
            # Exponential moving average with alpha=0.3
            if test_id in self.timings:
                self.timings[test_id] = 0.7 * self.timings[test_id] + 0.3 * duration
            else:
                self.timings[test_id] = duration
    
    def chunk_tests(self, test_suites: List, worker_count: int) -> List[List]:
        """Chunk tests using bin packing algorithm based on historical timings."""
        if not test_suites:
            return []
        
        # Flatten test suites and get timing estimates
        all_tests = []
        for suite in test_suites:
            for test in suite:
                test_id = f"{test.__class__.__module__}.{test.__class__.__name__}.{test._testMethodName}"
                estimated_time = self.timings.get(test_id, 1.0)  # Default 1 second
                all_tests.append((test, estimated_time))
        
        # Sort by estimated time (largest first for better bin packing)
        all_tests.sort(key=lambda x: x[1], reverse=True)
        
        # Initialize worker chunks
        chunks = [[] for _ in range(worker_count)]
        chunk_times = [0.0] * worker_count
        
        # Bin packing algorithm
        for test, estimated_time in all_tests:
            # Find chunk with minimum current time
            min_chunk_idx = min(range(worker_count), key=lambda i: chunk_times[i])
            chunks[min_chunk_idx].append(test)
            chunk_times[min_chunk_idx] += estimated_time
        
        # Log chunk distribution
        for i, (chunk, total_time) in enumerate(zip(chunks, chunk_times)):
            logger.info(f"Chunk {i}: {len(chunk)} tests, estimated time: {total_time:.2f}s")
        
        return chunks


class ConnectionManager:
    """Thread-safe connection management with health checks and recycling."""
    
    def __init__(self):
        self._connections = {}
        self._connection_stats = defaultdict(lambda: {'operations': 0, 'last_check': 0})
        self._lock = threading.Lock()
        self.max_operations = int(os.environ.get('DJANGO_TEST_MAX_OPERATIONS', 1000))
        self.health_check_interval = int(os.environ.get('DJANGO_TEST_HEALTH_CHECK_INTERVAL', 60))
    
    @contextmanager
    def use_connection(self, worker_id: int, database_name: str, connection_obj: BaseDatabaseWrapper):
        """Context manager for connection switching with health checks."""
        original_connection = connections['default']
        
        try:
            # Health check before use
            if self._should_check_health(worker_id):
                self._health_check(connection_obj)
            
            # Switch to worker connection
            connections['default'] = connection_obj
            
            yield connection_obj
            
            # Update operation count
            with self._lock:
                self._connection_stats[worker_id]['operations'] += 1
                
                # Recycle connection if needed
                if self._connection_stats[worker_id]['operations'] >= self.max_operations:
                    self._recycle_connection(worker_id, connection_obj)
                    
        finally:
            # Restore original connection
            connections['default'] = original_connection
    
    def _should_check_health(self, worker_id: int) -> bool:
        """Check if health check is needed."""
        current_time = time.time()
        last_check = self._connection_stats[worker_id]['last_check']
        return (current_time - last_check) > self.health_check_interval
    
    def _health_check(self, connection_obj: BaseDatabaseWrapper):
        """Perform connection health check."""
        try:
            with connection_obj.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            
            with self._lock:
                self._connection_stats[connection_obj.settings_dict.get('NAME', 'unknown')]['last_check'] = time.time()
                
        except Exception as e:
            logger.warning(f"Connection health check failed: {e}")
            raise WorkerRetryException(f"Connection health check failed: {e}")
    
    def _recycle_connection(self, worker_id: int, connection_obj: BaseDatabaseWrapper):
        """Recycle connection by closing and reconnecting."""
        try:
            connection_obj.close()
            connection_obj.ensure_connection()
            
            # Add connection validation after recycle
            self._health_check(connection_obj)
            
            with self._lock:
                self._connection_stats[worker_id]['operations'] = 0
                
            logger.info(f"Recycled connection for worker {worker_id}")
            
        except Exception as e:
            logger.warning(f"Failed to recycle connection for worker {worker_id}: {e}")
            # Mark connection as unhealthy for next health check
            with self._lock:
                self._connection_stats[worker_id]['last_check'] = 0


class JUnitReporter:
    """JUnit XML report generator."""
    
    def __init__(self, output_file: str):
        self.output_file = output_file
        self.test_results = []
    
    def add_test_result(self, test_id: str, status: str, duration: float, 
                       error_message: Optional[str] = None, worker_id: Optional[int] = None):
        """Add a test result."""
        self.test_results.append({
            'test_id': test_id,
            'status': status,
            'duration': duration,
            'error_message': error_message,
            'worker_id': worker_id
        })
    
    def generate_report(self):
        """Generate JUnit XML report."""
        if not self.output_file:
            return
        
        root = ET.Element("testsuites")
        
        # Group by test class
        test_classes = defaultdict(list)
        for result in self.test_results:
            class_name = '.'.join(result['test_id'].split('.')[:-1])
            test_classes[class_name].append(result)
        
        total_tests = len(self.test_results)
        total_failures = sum(1 for r in self.test_results if r['status'] == 'failure')
        total_errors = sum(1 for r in self.test_results if r['status'] == 'error')
        total_skipped = sum(1 for r in self.test_results if r['status'] == 'skip')
        total_time = sum(r['duration'] for r in self.test_results)
        
        # Add summary attributes
        root.set("tests", str(total_tests))
        root.set("failures", str(total_failures))
        root.set("errors", str(total_errors))
        root.set("skipped", str(total_skipped))
        root.set("time", f"{total_time:.3f}")
        
        # Add test suites
        for class_name, class_results in test_classes.items():
            suite = ET.SubElement(root, "testsuite")
            suite.set("name", class_name)
            suite.set("tests", str(len(class_results)))
            suite.set("failures", str(sum(1 for r in class_results if r['status'] == 'failure')))
            suite.set("errors", str(sum(1 for r in class_results if r['status'] == 'error')))
            suite.set("skipped", str(sum(1 for r in class_results if r['status'] == 'skip')))
            suite.set("time", f"{sum(r['duration'] for r in class_results):.3f}")
            
            # Add test cases
            for result in class_results:
                test_case = ET.SubElement(suite, "testcase")
                test_case.set("name", result['test_id'].split('.')[-1])
                test_case.set("classname", class_name)
                test_case.set("time", f"{result['duration']:.3f}")
                
                if result['worker_id'] is not None:
                    system_out = ET.SubElement(test_case, "system-out")
                    system_out.text = f"Worker ID: {result['worker_id']}"
                
                if result['status'] in ('failure', 'error') and result['error_message']:
                    failure = ET.SubElement(test_case, "failure" if result['status'] == 'failure' else "error")
                    failure.set("message", result['error_message'])
                    failure.text = result['error_message']
        
        # Write to file
        try:
            ET.indent(root)
            with open(self.output_file, "wb") as f:
                f.write(ET.tostring(root, encoding='utf-8'))
            logger.info(f"JUnit report written to {self.output_file}")
        except Exception as e:
            logger.error(f"Failed to write JUnit report: {e}")


class PrometheusMetrics:
    """Prometheus metrics collection."""
    
    def __init__(self):
        self.metrics = {
            'test_duration_seconds': [],
            'test_queries_total': [],
            'test_writes_total': [],
            'worker_duration_seconds': [],
            'database_operations_total': [],
            'connection_errors_total': 0,
            'worker_retries_total': 0,
        }
        self._lock = threading.Lock()
    
    def record_test_metrics(self, metrics: TestMetrics):
        """Record metrics for a single test."""
        with self._lock:
            self.metrics['test_duration_seconds'].append(metrics.duration)
            self.metrics['test_queries_total'].append(metrics.queries)
            self.metrics['test_writes_total'].append(metrics.writes)
    
    def record_worker_metrics(self, metrics: WorkerMetrics):
        """Record metrics for a worker."""
        with self._lock:
            self.metrics['worker_duration_seconds'].append(metrics.end_time - metrics.start_time)
            self.metrics['database_operations_total'].append(metrics.total_queries)
            self.metrics['connection_errors_total'] += metrics.connection_errors
            self.metrics['worker_retries_total'] += metrics.retry_count
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        with self._lock:
            return {
                'test_duration_avg': sum(self.metrics['test_duration_seconds']) / len(self.metrics['test_duration_seconds']) if self.metrics['test_duration_seconds'] else 0,
                'test_duration_p95': sorted(self.metrics['test_duration_seconds'])[int(len(self.metrics['test_duration_seconds']) * 0.95)] if self.metrics['test_duration_seconds'] else 0,
                'total_queries': sum(self.metrics['test_queries_total']),
                'total_writes': sum(self.metrics['test_writes_total']),
                'worker_duration_avg': sum(self.metrics['worker_duration_seconds']) / len(self.metrics['worker_duration_seconds']) if self.metrics['worker_duration_seconds'] else 0,
                'connection_errors': self.metrics['connection_errors_total'],
                'worker_retries': self.metrics['worker_retries_total'],
            }


class ConcurrentTestRunner(DiscoverRunner):
    """
    Production-grade concurrent test runner with enhanced metrics, JUnit reporting,
    adaptive workload balancing, and template database caching.
    
    Features:
    - Zero-config parallel testing
    - Secure database templating with caching
    - Production safeguards
    - Enhanced metrics and JUnit reporting
    - Adaptive workload balancing
    - Connection health monitoring
    - Worker retry mechanism
    - Prometheus metrics
    - Template database caching
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Configuration
        self.worker_count = get_safe_worker_count()
        self.timeout = int(os.environ.get('DJANGO_TEST_TIMEOUT', 300))
        self.benchmark = kwargs.get('benchmark', False) or os.environ.get('DJANGO_TEST_BENCHMARK', 'False') == 'True'
        self.junit_xml = kwargs.get('junitxml')
        self.max_retries = int(os.environ.get('DJANGO_TEST_MAX_RETRIES', 3))
        self.retry_delay = float(os.environ.get('DJANGO_TEST_RETRY_DELAY', 1.0))
        
        # Dynamic scaling configuration
        self.dynamic_scaling = kwargs.get('dynamic_scaling', False) or os.environ.get('DJANGO_TEST_DYNAMIC_SCALING', 'False') == 'True'
        self.min_workers = int(os.environ.get('DJANGO_TEST_MIN_WORKERS', 2))
        self.max_workers = int(os.environ.get('DJANGO_TEST_MAX_WORKERS', 16))
        
        # Timeout hierarchy configuration
        self.test_timeout = int(os.environ.get('DJANGO_TEST_TIMEOUT_PER_TEST', 30))
        self.worker_timeout = int(os.environ.get('DJANGO_TEST_TIMEOUT_PER_WORKER', self.timeout))
        self.global_timeout = int(os.environ.get('DJANGO_TEST_TIMEOUT_GLOBAL', self.timeout * 2))
        
        # Validate timeout hierarchy: test < worker < global
        if self.test_timeout >= self.worker_timeout:
            logger.warning(f"[CONCURRENT] Test timeout ({self.test_timeout}s) should be less than worker timeout ({self.worker_timeout}s)")
        if self.worker_timeout >= self.global_timeout:
            logger.warning(f"[CONCURRENT] Worker timeout ({self.worker_timeout}s) should be less than global timeout ({self.global_timeout}s)")
        
        # Enhanced components
        self.connection_manager = ConnectionManager()
        self.adaptive_chunker = AdaptiveChunker()
        self.junit_reporter = JUnitReporter(self.junit_xml) if self.junit_xml else None
        self.prometheus_metrics = PrometheusMetrics()
        
        # Template cache warmup during initialization
        if self._template_exists():
            logger.info("[CONCURRENT] Warming up template cache")
            try:
                self._clone_from_template(1)  # Create single clone to warm cache
                logger.info("[CONCURRENT] Template cache warmed up successfully")
            except Exception as e:
                logger.warning(f"[CONCURRENT] Template cache warmup failed: {e}")
        
        # Performance tracking
        self.start_time = None
        self.end_time = None
        self.worker_results = {}
        self.test_metrics = []
        self.worker_metrics = []
        
        # Thread safety
        self._lock = threading.Lock()
        self._template_cache = {}
        self._template_cache_lock = threading.Lock()
    
    def run_tests(self, test_labels, **kwargs):
        """
        Run tests concurrently with enhanced metrics and reporting.
        
        Args:
            test_labels: Test labels to run
            **kwargs: Additional arguments
            
        Returns:
            int: Number of failures
            
        Raises:
            SecurityException: If security checks fail
            UnsupportedDatabase: If database is not supported
        """
        try:
            # Security validation
            validate_environment()
            check_telemetry_disabled()
            
            # Performance tracking
            self.start_time = time.time()
            
            logger.info(f"[CONCURRENT] Starting production-grade concurrent test run with {self.worker_count} workers")
            
            # Split test suites using adaptive chunking
            test_suites = self.split_suites(test_labels)
            
            # Dynamic worker scaling
            if self.dynamic_scaling:
                self.worker_count = self._calculate_optimal_workers(test_suites)
                logger.info(f"[CONCURRENT] Dynamic scaling: using {self.worker_count} workers")
            
            # Apply adaptive chunking
            test_suites = self.adaptive_chunker.chunk_tests(test_suites, self.worker_count)
            
            if len(test_suites) == 1:
                logger.info("[CONCURRENT] Single test suite detected, running sequentially")
                return self.run_suite(test_suites[0])
            
            # Setup test databases with connections
            worker_connections = self._setup_databases()
            
            try:
                # Verify database isolation if multiple workers
                if len(worker_connections) > 1:
                    logger.info("[CONCURRENT] Verifying database isolation")
                    verify_database_isolation(worker_connections)
                
                # Run tests concurrently with enhanced features
                failures = self._run_concurrent_tests(test_suites, worker_connections)
                
                # Generate reports
                if self.benchmark:
                    self._generate_benchmark_report()
                
                if self.junit_reporter:
                    self.junit_reporter.generate_report()
                
                # Log Prometheus metrics
                self._log_prometheus_metrics()
                
                return failures
                
            finally:
                # Cleanup test databases and connections
                self._teardown_databases(worker_connections)
                
        except (SecurityException, UnsupportedDatabase) as e:
            logger.warning(f"[CONCURRENT] Security check failed: {e}")
            logger.info("[CONCURRENT] Falling back to sequential testing")
            return self._fallback_to_sequential(test_labels, **kwargs)
        
        except Exception as e:
            logger.error(f"[CONCURRENT] Unexpected error: {e}")
            logger.info("[CONCURRENT] Falling back to sequential testing")
            return self._fallback_to_sequential(test_labels, **kwargs)
    
    def split_suites(self, test_labels):
        """
        Split test labels into suites for parallel execution.
        
        Args:
            test_labels: Test labels to split
            
        Returns:
            list: List of test suites
        """
        if not test_labels:
            # Discover all tests
            test_suites = self.build_suite()
        else:
            # Build suite from specific labels
            test_suites = self.build_suite(test_labels)
        
        return list(test_suites)
    
    def _setup_databases(self):
        """
        Setup test databases with template caching for optimal performance.
        
        Returns:
            dict: Mapping of worker_id to (database_name, connection)
            
        Raises:
            DatabaseTemplateException: If setup fails
        """
        try:
            logger.info(f"[CONCURRENT] Setting up {self.worker_count} test databases with template caching")
            
            # Check if template exists
            if self._template_exists():
                logger.info("[CONCURRENT] Using cached database template")
                worker_connections = self._clone_from_template()
            else:
                logger.info("[CONCURRENT] Creating new database template")
                worker_connections = self._create_new_template()
            
            logger.info(f"[CONCURRENT] Successfully setup {len(worker_connections)} databases")
            return worker_connections
            
        except Exception as e:
            raise DatabaseTemplateException(
                f"Failed to setup test databases: {e}"
            ) from e
    
    def _template_exists(self) -> bool:
        """Check if template database exists."""
        try:
            with connection.cursor() as cursor:
                db_name = connection.settings_dict['NAME']
                # Use project-specific prefix for security
                project_name = getattr(settings, 'PROJECT_NAME', 'django')
                template_name = f"{project_name}_{db_name}_template"
                
                if connection.vendor == 'postgresql':
                    cursor.execute(
                        "SELECT 1 FROM pg_database WHERE datname = %s",
                        [template_name]
                    )
                elif connection.vendor == 'mysql':
                    cursor.execute(
                        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                        [template_name]
                    )
                else:
                    return False
                
                return cursor.fetchone() is not None
                
        except Exception as e:
            logger.warning(f"Failed to check template existence: {e}")
            return False
    
    def _clone_from_template(self, worker_count: Optional[int] = None) -> Dict[int, Tuple[str, BaseDatabaseWrapper]]:
        """Clone databases from existing template."""
        if worker_count is None:
            worker_count = self.worker_count
        return setup_test_databases_with_connections(worker_count)
    
    def _create_new_template(self) -> Dict[int, Tuple[str, BaseDatabaseWrapper]]:
        """Create new template and clone databases."""
        # Create template first
        cloner = get_database_cloner(connection)
        # Use project-specific prefix for security
        project_name = getattr(settings, 'PROJECT_NAME', 'django')
        template_db_name = f"{project_name}_{connection.settings_dict['NAME']}_template"
        
        try:
            # Add template cleanup if exists
            if self._template_exists():
                logger.info(f"Cleaning up existing template: {template_db_name}")
                self._cleanup_template()
            
            # Create template database
            with connection.cursor() as cursor:
                if connection.vendor == 'postgresql':
                    cursor.execute(f"CREATE DATABASE {template_db_name}")
                elif connection.vendor == 'mysql':
                    cursor.execute(f"CREATE DATABASE {template_db_name}")
            
            # Cache template
            with self._template_cache_lock:
                self._template_cache[connection.settings_dict['NAME']] = template_db_name
            
            # Clone from template
            return setup_test_databases_with_connections(self.worker_count)
            
        except Exception as e:
            logger.error(f"Failed to create template: {e}")
            # Fallback to regular setup
            return setup_test_databases_with_connections(self.worker_count)
    
    def _cleanup_template(self):
        """Clean up existing template database."""
        try:
            # Use project-specific prefix for security
            project_name = getattr(settings, 'PROJECT_NAME', 'django')
            template_db_name = f"{project_name}_{connection.settings_dict['NAME']}_template"
            
            # Use database vendor abstraction for connection termination
            from .db import terminate_connections
            terminate_connections(template_db_name, connection.vendor)
            
            with connection.cursor() as cursor:
                if connection.vendor == 'postgresql':
                    cursor.execute(f"DROP DATABASE IF EXISTS {template_db_name}")
                elif connection.vendor == 'mysql':
                    cursor.execute(f"DROP DATABASE IF EXISTS {template_db_name}")
            
            logger.info(f"Successfully cleaned up template: {template_db_name}")
            
        except Exception as e:
            logger.warning(f"Failed to cleanup template: {e}")
            # Continue with creation even if cleanup fails
    
    def _calculate_optimal_workers(self, test_suites: List) -> int:
        """
        Calculate optimal number of workers based on system resources and test characteristics.
        
        This method uses dynamic scaling to determine the optimal number of workers
        based on CPU cores, available memory, and test suite characteristics.
        
        Args:
            test_suites: List of test suites
            
        Returns:
            int: Optimal number of workers
        """
        try:
            import multiprocessing
            
            # Get available CPU cores
            cpu_count = multiprocessing.cpu_count()
            
            # Calculate total test count
            total_tests = sum(len(suite) for suite in test_suites)
            
            # Base calculation: 1 worker per 10 tests, but at least min_workers
            base_workers = max(self.min_workers, total_tests // 10)
            
            # Consider CPU cores: don't exceed available cores
            cpu_based_workers = min(cpu_count, base_workers)
            
            # Calculate memory-based scaling
            memory_workers = self._calculate_memory_based_workers()
            
            # Use the minimum of all calculations for safety
            optimal_workers = min(cpu_based_workers, memory_workers)
            
            # Apply min/max constraints
            optimal_workers = max(self.min_workers, min(self.max_workers, optimal_workers))
            
            logger.info(f"[CONCURRENT] Dynamic scaling: {total_tests} tests, {cpu_count} cores, "
                       f"memory: {memory_workers}, {optimal_workers} workers")
            
            return optimal_workers
            
        except Exception as e:
            logger.warning(f"Failed to calculate optimal workers: {e}, using default")
            return get_safe_worker_count()
    
    def _calculate_memory_based_workers(self) -> int:
        """
        Calculate optimal workers based on available memory.
        
        Returns:
            int: Memory-safe number of workers
        """
        try:
            import psutil
            
            # Get available memory in GB
            memory = psutil.virtual_memory()
            available_gb = memory.available / (1024 ** 3)
            
            # Estimate memory per worker (conservative estimate)
            memory_per_worker_gb = 0.5  # 500MB per worker
            
            # Calculate memory-safe workers
            memory_safe_workers = int(available_gb / memory_per_worker_gb)
            
            # Set minimum threshold (2GB available)
            if available_gb < 2.0:
                logger.warning(f"[CONCURRENT] Low memory available: {available_gb:.1f}GB, limiting workers")
                return min(2, self.max_workers)
            
            logger.debug(f"[CONCURRENT] Memory-based scaling: {available_gb:.1f}GB available, "
                        f"{memory_safe_workers} workers safe")
            
            return memory_safe_workers
            
        except ImportError:
            logger.warning("[CONCURRENT] psutil not available, skipping memory-based scaling")
            return self.max_workers
        except Exception as e:
            logger.warning(f"[CONCURRENT] Memory-based scaling failed: {e}")
            return self.max_workers
    
    def _teardown_databases(self, worker_connections):
        """
        Teardown test databases and connections.
        
        Args:
            worker_connections (dict): Mapping of worker_id to (database_name, connection)
        """
        try:
            logger.info(f"[CONCURRENT] Teardown {len(worker_connections)} test databases and connections")
            teardown_test_databases_with_connections(worker_connections)
            clear_connection_pool()
            logger.info("[CONCURRENT] Successfully teardown databases and connections")
            
        except Exception as e:
            logger.warning(f"[CONCURRENT] Failed to teardown databases: {e}")
            # Try to clear connection pool even if teardown fails
            try:
                clear_connection_pool()
            except Exception as pool_error:
                logger.warning(f"[CONCURRENT] Failed to clear connection pool: {pool_error}")
    
    def _run_concurrent_tests(self, test_suites, worker_connections):
        """
        Run tests concurrently with enhanced features.
        
        Args:
            test_suites (list): List of test suites
            worker_connections (dict): Mapping of worker_id to (database_name, connection)
            
        Returns:
            int: Number of failures
        """
        failures = 0
        
        with ThreadPoolExecutor(max_workers=self.worker_count) as executor:
            # Submit test suites to workers
            futures = {}
            for i, suite in enumerate(test_suites):
                if i in worker_connections:
                    database_name, worker_connection = worker_connections[i]
                    future = executor.submit(
                        self._run_worker_tests_with_retry,
                        suite,
                        database_name,
                        worker_connection,
                        i
                    )
                    futures[future] = i
            
            # Collect results
            for future in as_completed(futures, timeout=self.timeout):
                worker_id = futures[future]
                try:
                    result = future.result()
                    with self._lock:
                        self.worker_results[worker_id] = result
                        failures += result.get('failures', 0)
                        
                        # Record worker metrics
                        if 'metrics' in result:
                            self.worker_metrics.append(result['metrics'])
                            self.prometheus_metrics.record_worker_metrics(result['metrics'])
                        
                        # Record test metrics
                        if 'test_metrics' in result:
                            for test_metric in result['test_metrics']:
                                self.test_metrics.append(test_metric)
                                self.prometheus_metrics.record_test_metrics(test_metric)
                                
                                # Update adaptive chunker
                                self.adaptive_chunker.update_timing(
                                    test_metric.test_id, 
                                    test_metric.duration
                                )
                                
                                # Add to JUnit reporter
                                if self.junit_reporter:
                                    self.junit_reporter.add_test_result(
                                        test_metric.test_id,
                                        test_metric.status,
                                        test_metric.duration,
                                        test_metric.error_message,
                                        test_metric.worker_id
                                    )
                        
                except TimeoutError:
                    logger.error(f"[CONCURRENT] Worker {worker_id} timed out")
                    self._handle_worker_timeout(worker_id)
                    failures += 1
                    
                except Exception as e:
                    logger.error(f"[CONCURRENT] Worker {worker_id} failed: {e}")
                    self._handle_worker_error(worker_id, str(e))
                    failures += 1
        
        # Save updated timings
        self.adaptive_chunker._save_timings()
        
        return failures
    
    def _run_worker_tests_with_retry(self, test_suite, database_name, worker_connection, worker_id):
        """
        Run tests for a worker with retry mechanism.
        
        Args:
            test_suite: Test suite to run
            database_name (str): Database name for worker
            worker_connection: Thread-safe database connection for worker
            worker_id (int): Worker ID
            
        Returns:
            dict: Worker results
        """
        retry_count = 0
        last_error = None
        
        while retry_count <= self.max_retries:
            try:
                return self._run_worker_tests(test_suite, database_name, worker_connection, worker_id)
                
            except WorkerRetryException as e:
                retry_count += 1
                last_error = e
                
                if retry_count <= self.max_retries:
                    logger.warning(f"[CONCURRENT] Worker {worker_id} retry {retry_count}/{self.max_retries}: {e}")
                    time.sleep(self.retry_delay * retry_count)  # Exponential backoff
                else:
                    logger.error(f"[CONCURRENT] Worker {worker_id} failed after {self.max_retries} retries")
                    break
                    
            except Exception as e:
                # Non-retryable error
                logger.error(f"[CONCURRENT] Worker {worker_id} non-retryable error: {e}")
                break
        
        # Return failure result
        return {
            'failures': 1,
            'error': str(last_error) if last_error else 'Unknown error',
            'duration': 0,
            'db_operations': 0,
            'db_errors': 1,
            'worker_id': worker_id,
            'metrics': WorkerMetrics(
                worker_id=worker_id,
                start_time=time.time(),
                end_time=time.time(),
                tests_run=0,
                tests_failed=1,
                tests_skipped=0,
                total_queries=0,
                total_query_time=0,
                total_writes=0,
                total_reads=0,
                memory_peak=0,
                connection_errors=retry_count,
                retry_count=retry_count,
                database_name=database_name
            )
        }
    
    def _run_worker_tests(self, test_suite, database_name, worker_connection, worker_id):
        """
        Run tests for a specific worker with enhanced metrics.
        
        Args:
            test_suite: Test suite to run
            database_name (str): Database name for worker
            worker_connection: Thread-safe database connection for worker
            worker_id (int): Worker ID
            
        Returns:
            dict: Worker results
        """
        start_time = time.time()
        test_metrics = []
        total_queries = 0
        total_query_time = 0
        total_writes = 0
        total_reads = 0
        connection_errors = 0
        
        try:
            # Use connection manager for health checks and recycling
            with self.connection_manager.use_connection(worker_id, database_name, worker_connection):
                # Run each test with detailed metrics
                for test in test_suite:
                    test_start = time.time()
                    
                    try:
                        # Capture query metrics
                        with worker_connection.capture() as cap:
                            result = self._run_single_test(test, worker_connection)
                        
                        test_end = time.time()
                        test_duration = test_end - test_start
                        
                        # Calculate metrics
                        queries = len(cap.queries)
                        query_time = sum(float(q.get('time', 0)) for q in cap.queries)
                        writes = sum(1 for q in cap.queries if q.get('sql', '').strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')))
                        reads = queries - writes
                        
                        # Update totals
                        total_queries += queries
                        total_query_time += query_time
                        total_writes += writes
                        total_reads += reads
                        
                        # Create test metrics
                        test_id = f"{test.__class__.__module__}.{test.__class__.__name__}.{test._testMethodName}"
                        test_metric = TestMetrics(
                            test_id=test_id,
                            duration=test_duration,
                            queries=queries,
                            query_time=query_time,
                            writes=writes,
                            reads=reads,
                            memory_usage=self._get_memory_usage(),
                            status='success' if result == 0 else 'failure',
                            worker_id=worker_id
                        )
                        test_metrics.append(test_metric)
                        
                    except Exception as e:
                        connection_errors += 1
                        test_metric = TestMetrics(
                            test_id=f"{test.__class__.__module__}.{test.__class__.__name__}.{test._testMethodName}",
                            duration=time.time() - test_start,
                            queries=0,
                            query_time=0,
                            writes=0,
                            reads=0,
                            memory_usage=self._get_memory_usage(),
                            status='error',
                            error_message=str(e),
                            worker_id=worker_id
                        )
                        test_metrics.append(test_metric)
            
            end_time = time.time()
            worker_duration = end_time - start_time
            
            # Resource monitoring - warn if approaching timeout
            if worker_duration > self.timeout * 0.8:
                logger.warning(f"Worker {worker_id} approaching timeout: {worker_duration:.2f}s / {self.timeout}s")
            
                    # Create worker metrics with per-test timing
        worker_metrics = WorkerMetrics(
            worker_id=worker_id,
            start_time=start_time,
            end_time=end_time,
            tests_run=len(test_suite),
            tests_failed=sum(1 for m in test_metrics if m.status == 'failure'),
            tests_skipped=sum(1 for m in test_metrics if m.status == 'skip'),
            total_queries=total_queries,
            total_query_time=total_query_time,
            total_writes=total_writes,
            total_reads=total_reads,
            memory_peak=max(m.memory_usage for m in test_metrics) if test_metrics else 0,
            connection_errors=connection_errors,
            retry_count=0,
            database_name=database_name
        )
        
        # Log structured information for each worker's database
        logger.info(
            f"[CONCURRENT] Worker {worker_id} completed: "
            f"database={database_name}, "
            f"tests={len(test_suite)}, "
            f"duration={worker_duration:.2f}s, "
            f"queries={total_queries}, "
            f"memory_peak={worker_metrics.memory_peak:.1f}MB"
        )
            
            return {
                'failures': sum(1 for m in test_metrics if m.status == 'failure'),
                'duration': end_time - start_time,
                'db_operations': total_queries,
                'db_errors': connection_errors,
                'worker_id': worker_id,
                'test_metrics': test_metrics,
                'metrics': worker_metrics
            }
            
        except Exception as e:
            logger.error(f"[CONCURRENT] Worker {worker_id} error: {e}")
            
            return {
                'failures': 1,
                'error': str(e),
                'duration': time.time() - start_time,
                'db_operations': total_queries,
                'db_errors': connection_errors + 1,
                'worker_id': worker_id,
                'test_metrics': test_metrics,
                'metrics': WorkerMetrics(
                    worker_id=worker_id,
                    start_time=start_time,
                    end_time=time.time(),
                    tests_run=len(test_metrics),
                    tests_failed=len(test_metrics),
                    tests_skipped=0,
                    total_queries=total_queries,
                    total_query_time=total_query_time,
                    total_writes=total_writes,
                    total_reads=total_reads,
                    memory_peak=0,
                    connection_errors=connection_errors + 1,
                    retry_count=0,
                    database_name=database_name
                )
            }
    
    def _run_single_test(self, test, worker_connection):
        """Run a single test with proper connection handling and timeout."""
        # Store original connection
        original_connection = connections['default']
        
        try:
            # Replace default connection with worker connection
            connections['default'] = worker_connection
            
            # Track per-test-case timing
            test_start_time = time.time()
            
            # Run the test with test-level timeout handling
            with time_limit(self.test_timeout):
                result = self.run_suite([test])
                
                # Enhanced test skipping detection
                if hasattr(result, 'skipped'):
                    return 'skip'
                
                # Check Django's test skipping mechanism
                if result == 0 and hasattr(test, 'skipped') and test.skipped:
                    return 'skip'
                
                # Record per-test timing
                test_duration = time.time() - test_start_time
                test_id = f"{test.__class__.__module__}.{test.__class__.__name__}.{test._testMethodName}"
                
                # Update adaptive chunker with timing data
                if hasattr(self, 'adaptive_chunker'):
                    self.adaptive_chunker.update_timing(test_id, test_duration)
                
                return result
            
        except TestTimeoutError as e:
            logger.error(f"Test timed out: {e}")
            raise WorkerRetryException(f"Test execution timed out: {e}")
        finally:
            # Restore original connection
            connections['default'] = original_connection
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024  # Convert to MB
        except ImportError:
            logger.warning("psutil not installed, memory metrics disabled")
            return 0.0
        except Exception as e:
            logger.warning(f"Failed to get memory usage: {e}")
            return 0.0
    
    def _handle_worker_timeout(self, worker_id: int):
        """Handle worker timeout."""
        logger.error(f"[CONCURRENT] Worker {worker_id} timed out after {self.timeout}s")
        
        with self._lock:
            self.worker_results[worker_id] = {
                'failures': 1,
                'error': 'Worker timeout',
                'duration': self.timeout,
                'db_operations': 0,
                'db_errors': 1,
                'worker_id': worker_id,
                'metrics': WorkerMetrics(
                    worker_id=worker_id,
                    start_time=time.time() - self.timeout,
                    end_time=time.time(),
                    tests_run=0,
                    tests_failed=1,
                    tests_skipped=0,
                    total_queries=0,
                    total_query_time=0,
                    total_writes=0,
                    total_reads=0,
                    memory_peak=0,
                    connection_errors=1,
                    retry_count=0,
                    database_name='unknown'
                )
            }
    
    def _handle_worker_error(self, worker_id: int, error: str):
        """Handle worker error."""
        with self._lock:
            self.worker_results[worker_id] = {
                'failures': 1,
                'error': error,
                'duration': 0,
                'db_operations': 0,
                'db_errors': 1,
                'worker_id': worker_id,
                'metrics': WorkerMetrics(
                    worker_id=worker_id,
                    start_time=time.time(),
                    end_time=time.time(),
                    tests_run=0,
                    tests_failed=1,
                    tests_skipped=0,
                    total_queries=0,
                    total_query_time=0,
                    total_writes=0,
                    total_reads=0,
                    memory_peak=0,
                    connection_errors=1,
                    retry_count=0,
                    database_name='unknown'
                )
            }
    
    def _generate_benchmark_report(self):
        """Generate enhanced benchmark report."""
        if not self.start_time:
            return
        
        self.end_time = time.time()
        total_duration = self.end_time - self.start_time
        
        # Calculate statistics
        total_tests = sum(len(suite) for suite in self.split_suites([]))
        total_workers = len(self.worker_results)
        
        if total_workers == 0:
            return
        
        # Worker statistics
        worker_durations = [result.get('duration', 0) for result in self.worker_results.values()]
        avg_worker_duration = sum(worker_durations) / total_workers
        max_worker_duration = max(worker_durations)
        min_worker_duration = min(worker_durations)
        
        # Database statistics
        total_db_operations = sum(result.get('db_operations', 0) for result in self.worker_results.values())
        total_db_errors = sum(result.get('db_errors', 0) for result in self.worker_results.values())
        
        # Test metrics statistics
        if self.test_metrics:
            test_durations = [m.duration for m in self.test_metrics]
            avg_test_duration = sum(test_durations) / len(test_durations)
            p95_test_duration = sorted(test_durations)[int(len(test_durations) * 0.95)]
            
            total_queries = sum(m.queries for m in self.test_metrics)
            total_writes = sum(m.writes for m in self.test_metrics)
            total_reads = sum(m.reads for m in self.test_metrics)
        else:
            avg_test_duration = p95_test_duration = total_queries = total_writes = total_reads = 0
        
        # Performance metrics
        worker_utilization = (avg_worker_duration / total_duration) * 100 if total_duration > 0 else 0
        estimated_sequential = avg_worker_duration * total_workers
        speedup = ((estimated_sequential - total_duration) / estimated_sequential) * 100 if estimated_sequential > 0 else 0
        
        # Log enhanced benchmark report
        logger.info(f"\n{'='*60}")
        logger.info(f"[CONCURRENT] Production-Grade Benchmark Report")
        logger.info(f"{'='*60}")
        logger.info(f"Test Execution:")
        logger.info(f"  Total tests: {total_tests}")
        logger.info(f"  Workers used: {total_workers}")
        logger.info(f"  Total duration: {total_duration:.2f}s")
        logger.info(f"  Worker utilization: {worker_utilization:.1f}%")
        logger.info(f"  Estimated speedup: {speedup:.1f}% faster than sequential")
        
        logger.info(f"\nWorker Performance:")
        logger.info(f"  Average worker duration: {avg_worker_duration:.2f}s")
        logger.info(f"  Min worker duration: {min_worker_duration:.2f}s")
        logger.info(f"  Max worker duration: {max_worker_duration:.2f}s")
        logger.info(f"  Duration variance: {max_worker_duration - min_worker_duration:.2f}s")
        
        logger.info(f"\nDatabase Operations:")
        logger.info(f"  Total operations: {total_db_operations}")
        logger.info(f"  Total errors: {total_db_errors}")
        logger.info(f"  Error rate: {(total_db_errors/total_db_operations*100):.2f}%" if total_db_operations > 0 else "  Error rate: 0.00%")
        
        if self.test_metrics:
            logger.info(f"\nTest Metrics:")
            logger.info(f"  Average test duration: {avg_test_duration:.3f}s")
            logger.info(f"  95th percentile duration: {p95_test_duration:.3f}s")
            logger.info(f"  Total queries: {total_queries}")
            logger.info(f"  Total writes: {total_writes}")
            logger.info(f"  Total reads: {total_reads}")
            logger.info(f"  Write ratio: {(total_writes/total_queries*100):.1f}%" if total_queries > 0 else "  Write ratio: 0.0%")
        
        # Connection pool statistics
        pool_stats = get_connection_pool_stats()
        if pool_stats:
            logger.info(f"\nConnection Pool:")
            logger.info(f"  Active connections: {pool_stats.get('active', 0)}")
            logger.info(f"  Pooled connections: {pool_stats.get('pooled', 0)}")
            logger.info(f"  Connection hits: {pool_stats.get('hits', 0)}")
            logger.info(f"  Connection misses: {pool_stats.get('misses', 0)}")
        
        logger.info(f"{'='*60}")
    
    def _log_prometheus_metrics(self):
        """Log Prometheus metrics."""
        metrics_summary = self.prometheus_metrics.get_metrics_summary()
        
        logger.info("[CONCURRENT] Prometheus Metrics Summary:")
        for key, value in metrics_summary.items():
            if isinstance(value, float):
                logger.info(f"  {key}: {value:.3f}")
            else:
                logger.info(f"  {key}: {value}")
    
    def _fallback_to_sequential(self, test_labels, **kwargs):
        """
        Fallback to sequential testing.
        
        Args:
            test_labels: Test labels to run
            **kwargs: Additional arguments
            
        Returns:
            int: Number of failures
        """
        logger.info("[CONCURRENT] Running tests sequentially")
        return super().run_tests(test_labels, **kwargs)
    
    def run_suite(self, suite):
        """
        Run a test suite.
        
        Args:
            suite: Test suite to run
            
        Returns:
            int: Number of failures
        """
        # Use Django's default test runner for individual suites
        from django.test.runner import DiscoverRunner
        runner = DiscoverRunner()
        return runner.run_suite(suite)
    
    def setup_databases(self, **kwargs):
        """
        Setup databases for testing.
        
        Args:
            **kwargs: Additional arguments
            
        Returns:
            dict: Database configuration
        """
        # Use parent implementation for database setup
        return super().setup_databases(**kwargs)
    
    def teardown_databases(self, old_config, **kwargs):
        """
        Teardown databases after testing.
        
        Args:
            old_config: Previous database configuration
            **kwargs: Additional arguments
        """
        # Use parent implementation for database teardown
        super().teardown_databases(old_config, **kwargs) 