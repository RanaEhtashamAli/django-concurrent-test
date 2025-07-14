"""
Tests for the enhanced ConcurrentTestRunner with production-grade features.
"""

import os
import sys
import time
import json
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock, call
from io import StringIO
from contextlib import contextmanager

from django.test import TestCase, override_settings
from django.db import connections, connection
from django.conf import settings

from django_concurrent_test.runner import (
    ConcurrentTestRunner,
    TestMetrics,
    WorkerMetrics,
    AdaptiveChunker,
    ConnectionManager,
    JUnitReporter,
    PrometheusMetrics,
)
from django_concurrent_test.exceptions import (
    WorkerRetryException,
    DatabaseTemplateException,
    SecurityException,
)


class TestMetricsTestCase(TestCase):
    """Test TestMetrics dataclass."""
    
    def test_test_metrics_creation(self):
        """Test creating TestMetrics instance."""
        metrics = TestMetrics(
            test_id="test_app.test_module.TestClass.test_method",
            duration=1.5,
            queries=10,
            query_time=0.5,
            writes=3,
            reads=7,
            memory_usage=50.0,
            status="success",
            worker_id=1
        )
        
        self.assertEqual(metrics.test_id, "test_app.test_module.TestClass.test_method")
        self.assertEqual(metrics.duration, 1.5)
        self.assertEqual(metrics.queries, 10)
        self.assertEqual(metrics.query_time, 0.5)
        self.assertEqual(metrics.writes, 3)
        self.assertEqual(metrics.reads, 7)
        self.assertEqual(metrics.memory_usage, 50.0)
        self.assertEqual(metrics.status, "success")
        self.assertEqual(metrics.worker_id, 1)
    
    def test_test_metrics_with_error(self):
        """Test TestMetrics with error message."""
        metrics = TestMetrics(
            test_id="test_app.test_module.TestClass.test_method",
            duration=0.1,
            queries=0,
            query_time=0.0,
            writes=0,
            reads=0,
            memory_usage=10.0,
            status="error",
            error_message="Database connection failed",
            worker_id=2
        )
        
        self.assertEqual(metrics.status, "error")
        self.assertEqual(metrics.error_message, "Database connection failed")


class WorkerMetricsTestCase(TestCase):
    """Test WorkerMetrics dataclass."""
    
    def test_worker_metrics_creation(self):
        """Test creating WorkerMetrics instance."""
        start_time = time.time()
        end_time = start_time + 10.0
        
        metrics = WorkerMetrics(
            worker_id=1,
            start_time=start_time,
            end_time=end_time,
            tests_run=5,
            tests_failed=1,
            tests_skipped=0,
            total_queries=50,
            total_query_time=2.5,
            total_writes=15,
            total_reads=35,
            memory_peak=100.0,
            connection_errors=0,
            retry_count=0,
            database_name="test_db_1"
        )
        
        self.assertEqual(metrics.worker_id, 1)
        self.assertEqual(metrics.tests_run, 5)
        self.assertEqual(metrics.tests_failed, 1)
        self.assertEqual(metrics.total_queries, 50)
        self.assertEqual(metrics.total_writes, 15)
        self.assertEqual(metrics.total_reads, 35)
        self.assertEqual(metrics.memory_peak, 100.0)
        self.assertEqual(metrics.database_name, "test_db_1")


class AdaptiveChunkerTestCase(TestCase):
    """Test AdaptiveChunker class."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        self.temp_file.close()
        self.chunker = AdaptiveChunker(self.temp_file.name)
    
    def tearDown(self):
        """Clean up test environment."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
    
    def test_load_timings_empty_file(self):
        """Test loading timings from empty file."""
        timings = self.chunker._load_timings()
        self.assertEqual(timings, {})
    
    def test_load_timings_with_data(self):
        """Test loading timings from file with data."""
        test_data = {
            "test_app.test_module.TestClass.test_method": 1.5,
            "test_app.test_module.TestClass.test_method2": 2.0
        }
        
        with open(self.temp_file.name, 'w') as f:
            json.dump(test_data, f)
        
        timings = self.chunker._load_timings()
        self.assertEqual(timings, test_data)
    
    def test_save_timings(self):
        """Test saving timings to file."""
        test_data = {
            "test_app.test_module.TestClass.test_method": 1.5
        }
        
        self.chunker.timings = test_data
        self.chunker._save_timings()
        
        with open(self.temp_file.name, 'r') as f:
            saved_data = json.load(f)
        
        self.assertEqual(saved_data, test_data)
    
    def test_update_timing_new_test(self):
        """Test updating timing for new test."""
        test_id = "test_app.test_module.TestClass.test_method"
        duration = 1.5
        
        self.chunker.update_timing(test_id, duration)
        
        self.assertEqual(self.chunker.timings[test_id], duration)
    
    def test_update_timing_existing_test(self):
        """Test updating timing for existing test."""
        test_id = "test_app.test_module.TestClass.test_method"
        initial_duration = 1.0
        new_duration = 2.0
        
        # Set initial timing
        self.chunker.timings[test_id] = initial_duration
        
        # Update timing
        self.chunker.update_timing(test_id, new_duration)
        
        # Should use exponential moving average: 0.7 * 1.0 + 0.3 * 2.0 = 1.3
        expected = 0.7 * initial_duration + 0.3 * new_duration
        self.assertEqual(self.chunker.timings[test_id], expected)
    
    def test_chunk_tests_empty_suites(self):
        """Test chunking empty test suites."""
        chunks = self.chunker.chunk_tests([], 4)
        self.assertEqual(chunks, [])
    
    def test_chunk_tests_single_worker(self):
        """Test chunking tests for single worker."""
        # Create mock test suites
        test_suites = [
            [Mock(_testMethodName='test1'), Mock(_testMethodName='test2')],
            [Mock(_testMethodName='test3')]
        ]
        
        # Set up test classes
        for suite in test_suites:
            for test in suite:
                test.__class__.__module__ = 'test_app.test_module'
                test.__class__.__name__ = 'TestClass'
        
        chunks = self.chunker.chunk_tests(test_suites, 1)
        
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 3)  # All tests in one chunk
    
    def test_chunk_tests_multiple_workers(self):
        """Test chunking tests for multiple workers."""
        # Create mock test suites with known timings
        test_suites = [
            [Mock(_testMethodName='test1'), Mock(_testMethodName='test2')],
            [Mock(_testMethodName='test3'), Mock(_testMethodName='test4')]
        ]
        
        # Set up test classes
        for suite in test_suites:
            for test in suite:
                test.__class__.__module__ = 'test_app.test_module'
                test.__class__.__name__ = 'TestClass'
        
        # Set up timings
        self.chunker.timings = {
            "test_app.test_module.TestClass.test1": 3.0,  # Longest
            "test_app.test_module.TestClass.test2": 1.0,
            "test_app.test_module.TestClass.test3": 2.0,
            "test_app.test_module.TestClass.test4": 1.0,
        }
        
        chunks = self.chunker.chunk_tests(test_suites, 2)
        
        self.assertEqual(len(chunks), 2)
        # Should distribute tests based on timing
        total_tests = sum(len(chunk) for chunk in chunks)
        self.assertEqual(total_tests, 4)


class ConnectionManagerTestCase(TestCase):
    """Test ConnectionManager class."""
    
    def setUp(self):
        """Set up test environment."""
        self.manager = ConnectionManager()
        self.mock_connection = Mock()
        self.mock_connection.settings_dict = {'NAME': 'test_db'}
    
    def test_use_connection_context_manager(self):
        """Test connection context manager."""
        original_connection = connections['default']
        
        with self.manager.use_connection(1, 'test_db', self.mock_connection):
            # Connection should be switched
            self.assertEqual(connections['default'], self.mock_connection)
        
        # Connection should be restored
        self.assertEqual(connections['default'], original_connection)
    
    def test_health_check_triggered(self):
        """Test that health check is triggered when needed."""
        # Mock cursor
        mock_cursor = Mock()
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Set last check to be old
        self.manager._connection_stats[1]['last_check'] = time.time() - 120
        
        with self.manager.use_connection(1, 'test_db', self.mock_connection):
            pass
        
        # Health check should have been called
        mock_cursor.execute.assert_called_with("SELECT 1")
    
    def test_connection_recycling(self):
        """Test connection recycling after max operations."""
        # Set operation count to trigger recycling
        self.manager._connection_stats[1]['operations'] = 999
        
        with self.manager.use_connection(1, 'test_db', self.mock_connection):
            pass
        
        # Connection should be recycled
        self.mock_connection.close.assert_called_once()
        self.mock_connection.ensure_connection.assert_called_once()
        
        # Operation count should be reset
        self.assertEqual(self.manager._connection_stats[1]['operations'], 0)
    
    def test_health_check_failure(self):
        """Test health check failure raises WorkerRetryException."""
        # Mock cursor to raise exception
        self.mock_connection.cursor.side_effect = Exception("Connection failed")
        
        # Set last check to be old
        self.manager._connection_stats[1]['last_check'] = time.time() - 120
        
        with self.assertRaises(WorkerRetryException):
            with self.manager.use_connection(1, 'test_db', self.mock_connection):
                pass


class JUnitReporterTestCase(TestCase):
    """Test JUnitReporter class."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xml')
        self.temp_file.close()
        self.reporter = JUnitReporter(self.temp_file.name)
    
    def tearDown(self):
        """Clean up test environment."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
    
    def test_add_test_result(self):
        """Test adding test result."""
        self.reporter.add_test_result(
            "test_app.test_module.TestClass.test_method",
            "success",
            1.5,
            worker_id=1
        )
        
        self.assertEqual(len(self.reporter.test_results), 1)
        result = self.reporter.test_results[0]
        self.assertEqual(result['test_id'], "test_app.test_module.TestClass.test_method")
        self.assertEqual(result['status'], "success")
        self.assertEqual(result['duration'], 1.5)
        self.assertEqual(result['worker_id'], 1)
    
    def test_generate_report_success(self):
        """Test generating JUnit report with success."""
        # Add test results
        self.reporter.add_test_result(
            "test_app.test_module.TestClass.test_method1",
            "success",
            1.0
        )
        self.reporter.add_test_result(
            "test_app.test_module.TestClass.test_method2",
            "failure",
            2.0,
            error_message="Assertion failed"
        )
        self.reporter.add_test_result(
            "test_app.test_module.TestClass2.test_method3",
            "success",
            0.5
        )
        
        # Generate report
        self.reporter.generate_report()
        
        # Check file exists and has content
        self.assertTrue(os.path.exists(self.temp_file.name))
        
        with open(self.temp_file.name, 'rb') as f:
            content = f.read()
        
        # Should contain XML content
        self.assertIn(b'<testsuites', content)
        self.assertIn(b'<testsuite', content)
        self.assertIn(b'<testcase', content)
        self.assertIn(b'Assertion failed', content)
    
    def test_generate_report_no_output_file(self):
        """Test generating report with no output file."""
        reporter = JUnitReporter(None)
        reporter.add_test_result("test", "success", 1.0)
        
        # Should not raise exception
        reporter.generate_report()


class PrometheusMetricsTestCase(TestCase):
    """Test PrometheusMetrics class."""
    
    def setUp(self):
        """Set up test environment."""
        self.metrics = PrometheusMetrics()
    
    def test_record_test_metrics(self):
        """Test recording test metrics."""
        test_metric = TestMetrics(
            test_id="test",
            duration=1.5,
            queries=10,
            query_time=0.5,
            writes=3,
            reads=7,
            memory_usage=50.0,
            status="success"
        )
        
        self.metrics.record_test_metrics(test_metric)
        
        self.assertEqual(len(self.metrics.metrics['test_duration_seconds']), 1)
        self.assertEqual(len(self.metrics.metrics['test_queries_total']), 1)
        self.assertEqual(len(self.metrics.metrics['test_writes_total']), 1)
    
    def test_record_worker_metrics(self):
        """Test recording worker metrics."""
        worker_metric = WorkerMetrics(
            worker_id=1,
            start_time=time.time(),
            end_time=time.time() + 10.0,
            tests_run=5,
            tests_failed=1,
            tests_skipped=0,
            total_queries=50,
            total_query_time=2.5,
            total_writes=15,
            total_reads=35,
            memory_peak=100.0,
            connection_errors=0,
            retry_count=0,
            database_name="test_db"
        )
        
        self.metrics.record_worker_metrics(worker_metric)
        
        self.assertEqual(len(self.metrics.metrics['worker_duration_seconds']), 1)
        self.assertEqual(len(self.metrics.metrics['database_operations_total']), 1)
        self.assertEqual(self.metrics.metrics['connection_errors_total'], 0)
        self.assertEqual(self.metrics.metrics['worker_retries_total'], 0)
    
    def test_get_metrics_summary(self):
        """Test getting metrics summary."""
        # Add some test data
        self.metrics.metrics['test_duration_seconds'] = [1.0, 2.0, 3.0]
        self.metrics.metrics['test_queries_total'] = [10, 20, 30]
        self.metrics.metrics['test_writes_total'] = [5, 10, 15]
        self.metrics.metrics['worker_duration_seconds'] = [10.0, 15.0]
        self.metrics.metrics['database_operations_total'] = [100, 150]
        self.metrics.metrics['connection_errors_total'] = 2
        self.metrics.metrics['worker_retries_total'] = 1
        
        summary = self.metrics.get_metrics_summary()
        
        self.assertEqual(summary['test_duration_avg'], 2.0)
        self.assertEqual(summary['total_queries'], 60)
        self.assertEqual(summary['total_writes'], 30)
        self.assertEqual(summary['connection_errors'], 2)
        self.assertEqual(summary['worker_retries'], 1)


class EnhancedConcurrentTestRunnerTestCase(TestCase):
    """Test the enhanced ConcurrentTestRunner."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.validate_environment')
    @patch('django_concurrent_test.runner.check_telemetry_disabled')
    @patch('django_concurrent_test.runner.setup_test_databases_with_connections')
    @patch('django_concurrent_test.runner.verify_database_isolation')
    @patch('django_concurrent_test.runner.teardown_test_databases_with_connections')
    @patch('django_concurrent_test.runner.clear_connection_pool')
    def test_run_tests_success(self, mock_clear_pool, mock_teardown, mock_verify, 
                              mock_setup, mock_check_telemetry, mock_validate):
        """Test successful test run."""
        # Mock setup
        mock_setup.return_value = {
            0: ('test_db_0', Mock()),
            1: ('test_db_1', Mock())
        }
        
        # Mock test suites
        test_suites = [[Mock(), Mock()], [Mock()]]
        
        with patch.object(self.runner, 'split_suites', return_value=test_suites):
            with patch.object(self.runner, '_run_concurrent_tests', return_value=0):
                result = self.runner.run_tests(['test_app'])
        
        self.assertEqual(result, 0)
        mock_validate.assert_called_once()
        mock_check_telemetry.assert_called_once()
        mock_setup.assert_called_once()
        mock_verify.assert_called_once()
        mock_teardown.assert_called_once()
        mock_clear_pool.assert_called_once()
    
    @patch('django_concurrent_test.runner.validate_environment')
    @patch('django_concurrent_test.runner.check_telemetry_disabled')
    def test_run_tests_security_exception_fallback(self, mock_check_telemetry, mock_validate):
        """Test fallback to sequential on security exception."""
        mock_validate.side_effect = SecurityException("Security check failed")
        
        with patch.object(self.runner, '_fallback_to_sequential', return_value=1) as mock_fallback:
            result = self.runner.run_tests(['test_app'])
        
        self.assertEqual(result, 1)
        mock_fallback.assert_called_once()
    
    def test_template_exists_postgresql(self):
        """Test template existence check for PostgreSQL."""
        with patch.object(connection, 'vendor', 'postgresql'):
            with patch.object(connection, 'cursor') as mock_cursor:
                mock_cursor.return_value.__enter__.return_value.fetchone.return_value = [1]
                
                result = self.runner._template_exists()
                
                self.assertTrue(result)
                mock_cursor.return_value.__enter__.return_value.execute.assert_called_once()
    
    def test_template_exists_mysql(self):
        """Test template existence check for MySQL."""
        with patch.object(connection, 'vendor', 'mysql'):
            with patch.object(connection, 'cursor') as mock_cursor:
                mock_cursor.return_value.__enter__.return_value.fetchone.return_value = [1]
                
                result = self.runner._template_exists()
                
                self.assertTrue(result)
                mock_cursor.return_value.__enter__.return_value.execute.assert_called_once()
    
    def test_template_exists_not_found(self):
        """Test template existence check when template doesn't exist."""
        with patch.object(connection, 'vendor', 'postgresql'):
            with patch.object(connection, 'cursor') as mock_cursor:
                mock_cursor.return_value.__enter__.return_value.fetchone.return_value = None
                
                result = self.runner._template_exists()
                
                self.assertFalse(result)
    
    @patch('django_concurrent_test.runner.setup_test_databases_with_connections')
    def test_clone_from_template(self, mock_setup):
        """Test cloning from existing template."""
        mock_setup.return_value = {0: ('test_db_0', Mock())}
        
        result = self.runner._clone_from_template()
        
        self.assertEqual(result, {0: ('test_db_0', Mock())})
        mock_setup.assert_called_once_with(self.runner.worker_count)
    
    @patch('django_concurrent_test.runner.setup_test_databases_with_connections')
    def test_create_new_template(self, mock_setup):
        """Test creating new template."""
        mock_setup.return_value = {0: ('test_db_0', Mock())}
        
        with patch.object(connection, 'cursor') as mock_cursor:
            with patch.object(connection, 'vendor', 'postgresql'):
                result = self.runner._create_new_template()
        
        self.assertEqual(result, {0: ('test_db_0', Mock())})
        mock_setup.assert_called_once_with(self.runner.worker_count)
    
    def test_get_memory_usage_with_psutil(self):
        """Test memory usage with psutil available."""
        with patch('django_concurrent_test.runner.psutil') as mock_psutil:
            mock_process = Mock()
            mock_process.memory_info.return_value.rss = 100 * 1024 * 1024  # 100MB
            mock_psutil.Process.return_value = mock_process
            
            usage = self.runner._get_memory_usage()
            
            self.assertEqual(usage, 100.0)
    
    def test_get_memory_usage_without_psutil(self):
        """Test memory usage without psutil."""
        with patch('django_concurrent_test.runner.psutil', None):
            usage = self.runner._get_memory_usage()
            
            self.assertEqual(usage, 0.0)
    
    def test_handle_worker_timeout(self):
        """Test handling worker timeout."""
        worker_id = 1
        
        self.runner._handle_worker_timeout(worker_id)
        
        self.assertIn(worker_id, self.runner.worker_results)
        result = self.runner.worker_results[worker_id]
        self.assertEqual(result['failures'], 1)
        self.assertEqual(result['error'], 'Worker timeout')
        self.assertEqual(result['duration'], self.runner.timeout)
    
    def test_handle_worker_error(self):
        """Test handling worker error."""
        worker_id = 1
        error = "Database connection failed"
        
        self.runner._handle_worker_error(worker_id, error)
        
        self.assertIn(worker_id, self.runner.worker_results)
        result = self.runner.worker_results[worker_id]
        self.assertEqual(result['failures'], 1)
        self.assertEqual(result['error'], error)
    
    @patch('django_concurrent_test.runner.get_connection_pool_stats')
    def test_generate_benchmark_report(self, mock_pool_stats):
        """Test generating benchmark report."""
        mock_pool_stats.return_value = {
            'active': 5,
            'pooled': 10,
            'hits': 100,
            'misses': 5
        }
        
        # Set up test data
        self.runner.start_time = time.time() - 10.0
        self.runner.end_time = time.time()
        self.runner.worker_results = {
            0: {'duration': 5.0, 'db_operations': 50, 'db_errors': 0},
            1: {'duration': 8.0, 'db_operations': 75, 'db_errors': 1}
        }
        self.runner.test_metrics = [
            TestMetrics(
                test_id="test1",
                duration=1.0,
                queries=10,
                query_time=0.5,
                writes=3,
                reads=7,
                memory_usage=50.0,
                status="success"
            )
        ]
        
        # Capture output
        with patch('builtins.print') as mock_print:
            self.runner._generate_benchmark_report()
        
        # Should have called print multiple times for the report
        self.assertGreater(mock_print.call_count, 10)
    
    def test_log_prometheus_metrics(self):
        """Test logging Prometheus metrics."""
        # Add some test data
        self.runner.prometheus_metrics.metrics['test_duration_seconds'] = [1.0, 2.0]
        self.runner.prometheus_metrics.metrics['test_queries_total'] = [10, 20]
        self.runner.prometheus_metrics.metrics['test_writes_total'] = [5, 10]
        self.runner.prometheus_metrics.metrics['worker_duration_seconds'] = [10.0]
        self.runner.prometheus_metrics.metrics['database_operations_total'] = [100]
        self.runner.prometheus_metrics.metrics['connection_errors_total'] = 1
        self.runner.prometheus_metrics.metrics['worker_retries_total'] = 0
        
        with patch('django_concurrent_test.runner.logger.info') as mock_log:
            self.runner._log_prometheus_metrics()
        
        # Should have logged metrics
        self.assertGreater(mock_log.call_count, 0)


class IntegrationTestCase(TestCase):
    """Integration tests for the enhanced runner."""
    
    @patch('django_concurrent_test.runner.validate_environment')
    @patch('django_concurrent_test.runner.check_telemetry_disabled')
    @patch('django_concurrent_test.runner.setup_test_databases_with_connections')
    @patch('django_concurrent_test.runner.verify_database_isolation')
    @patch('django_concurrent_test.runner.teardown_test_databases_with_connections')
    @patch('django_concurrent_test.runner.clear_connection_pool')
    def test_full_integration_with_metrics(self, mock_clear_pool, mock_teardown, 
                                          mock_verify, mock_setup, mock_check_telemetry, 
                                          mock_validate):
        """Test full integration with metrics collection."""
        # Mock database setup
        mock_setup.return_value = {
            0: ('test_db_0', Mock()),
            1: ('test_db_1', Mock())
        }
        
        # Create runner with JUnit output
        runner = ConcurrentTestRunner(junitxml='test-results.xml')
        
        # Mock test suites
        test_suites = [[Mock(), Mock()], [Mock()]]
        
        with patch.object(runner, 'split_suites', return_value=test_suites):
            with patch.object(runner, '_run_concurrent_tests', return_value=0):
                result = runner.run_tests(['test_app'])
        
        self.assertEqual(result, 0)
        
        # Verify all components were used
        mock_validate.assert_called_once()
        mock_check_telemetry.assert_called_once()
        mock_setup.assert_called_once()
        mock_verify.assert_called_once()
        mock_teardown.assert_called_once()
        mock_clear_pool.assert_called_once()
        
        # Verify JUnit reporter was created
        self.assertIsNotNone(runner.junit_reporter)
    
    def test_adaptive_chunking_integration(self):
        """Test adaptive chunking integration."""
        # Create timing file with historical data
        timing_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        timing_data = {
            "test_app.test_module.TestClass.test1": 3.0,
            "test_app.test_module.TestClass.test2": 1.0,
            "test_app.test_module.TestClass.test3": 2.0
        }
        json.dump(timing_data, timing_file)
        timing_file.close()
        
        try:
            # Create chunker
            chunker = AdaptiveChunker(timing_file.name)
            
            # Create mock test suites
            test_suites = [
                [Mock(_testMethodName='test1'), Mock(_testMethodName='test2')],
                [Mock(_testMethodName='test3')]
            ]
            
            # Set up test classes
            for suite in test_suites:
                for test in suite:
                    test.__class__.__module__ = 'test_app.test_module'
                    test.__class__.__name__ = 'TestClass'
            
            # Chunk tests
            chunks = chunker.chunk_tests(test_suites, 2)
            
            # Verify chunks were created
            self.assertEqual(len(chunks), 2)
            total_tests = sum(len(chunk) for chunk in chunks)
            self.assertEqual(total_tests, 3)
            
            # Update timing
            chunker.update_timing("test_app.test_module.TestClass.test1", 2.5)
            
            # Verify timing was updated
            self.assertIn("test_app.test_module.TestClass.test1", chunker.timings)
            
        finally:
            os.unlink(timing_file.name)


if __name__ == '__main__':
    unittest.main() 