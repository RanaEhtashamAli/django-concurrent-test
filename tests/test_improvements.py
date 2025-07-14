"""
Tests for the improvements made to the ConcurrentTestRunner.
"""

import os
import sys
import time
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
    TestTimeoutError,
    time_limit,
    AdaptiveChunker,
    ConnectionManager,
)
from django_concurrent_test.exceptions import (
    WorkerRetryException,
    DatabaseTemplateException,
    SecurityException,
)


class TemplateCleanupTestCase(TestCase):
    """Test template database cleanup functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.connection')
    def test_template_cleanup_postgresql(self, mock_connection):
        """Test template cleanup for PostgreSQL."""
        mock_connection.vendor = 'postgresql'
        mock_connection.settings_dict = {'NAME': 'test_db'}
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock settings
        with patch('django_concurrent_test.runner.settings') as mock_settings:
            mock_settings.PROJECT_NAME = 'myproject'
            
            self.runner._cleanup_template()
            
            # Verify SQL commands were executed
            mock_cursor.execute.assert_any_call(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'myproject_test_db_template' AND pid <> pg_backend_pid()"
            )
            mock_cursor.execute.assert_any_call("DROP DATABASE IF EXISTS myproject_test_db_template")
    
    @patch('django_concurrent_test.runner.connection')
    def test_template_cleanup_mysql(self, mock_connection):
        """Test template cleanup for MySQL."""
        mock_connection.vendor = 'mysql'
        mock_connection.settings_dict = {'NAME': 'test_db'}
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock settings
        with patch('django_concurrent_test.runner.settings') as mock_settings:
            mock_settings.PROJECT_NAME = 'myproject'
            
            self.runner._cleanup_template()
            
            # Verify SQL command was executed
            mock_cursor.execute.assert_called_with("DROP DATABASE IF EXISTS myproject_test_db_template")
    
    @patch('django_concurrent_test.runner.connection')
    def test_template_cleanup_error_handling(self, mock_connection):
        """Test template cleanup error handling."""
        mock_connection.vendor = 'postgresql'
        mock_connection.settings_dict = {'NAME': 'test_db'}
        mock_connection.cursor.side_effect = Exception("Database error")
        
        # Should not raise exception, just log warning
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            self.runner._cleanup_template()
            mock_logger.warning.assert_called()
    
    @patch('django_concurrent_test.runner.connection')
    def test_template_exists_with_project_prefix(self, mock_connection):
        """Test template existence check with project prefix."""
        mock_connection.vendor = 'postgresql'
        mock_connection.settings_dict = {'NAME': 'test_db'}
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = [1]  # Template exists
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock settings
        with patch('django_concurrent_test.runner.settings') as mock_settings:
            mock_settings.PROJECT_NAME = 'myproject'
            
            result = self.runner._template_exists()
            
            self.assertTrue(result)
            mock_cursor.execute.assert_called_with(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                ['myproject_test_db_template']
            )


class MemoryMeasurementTestCase(TestCase):
    """Test memory measurement functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.psutil')
    def test_memory_usage_with_psutil(self, mock_psutil):
        """Test memory usage measurement with psutil available."""
        mock_process = Mock()
        mock_process.memory_info.return_value.rss = 100 * 1024 * 1024  # 100MB
        mock_psutil.Process.return_value = mock_process
        
        usage = self.runner._get_memory_usage()
        
        self.assertEqual(usage, 100.0)
        mock_psutil.Process.assert_called_once()
    
    @patch('django_concurrent_test.runner.psutil', None)
    def test_memory_usage_without_psutil(self, mock_psutil):
        """Test memory usage measurement without psutil."""
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            usage = self.runner._get_memory_usage()
            
            self.assertEqual(usage, 0.0)
            mock_logger.warning.assert_called_with("psutil not installed, memory metrics disabled")
    
    @patch('django_concurrent_test.runner.psutil')
    def test_memory_usage_psutil_error(self, mock_psutil):
        """Test memory usage measurement with psutil error."""
        mock_psutil.Process.side_effect = Exception("psutil error")
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            usage = self.runner._get_memory_usage()
            
            self.assertEqual(usage, 0.0)
            mock_logger.warning.assert_called_with("Failed to get memory usage: psutil error")


class DynamicWorkerScalingTestCase(TestCase):
    """Test dynamic worker scaling functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
        self.runner.dynamic_scaling = True
        self.runner.min_workers = 2
        self.runner.max_workers = 16
    
    @patch('django_concurrent_test.runner.multiprocessing')
    def test_calculate_optimal_workers_small_suite(self, mock_multiprocessing):
        """Test optimal worker calculation for small test suite."""
        mock_multiprocessing.cpu_count.return_value = 8
        
        # Small test suite: 15 tests
        test_suites = [[Mock() for _ in range(5)] for _ in range(3)]
        
        optimal_workers = self.runner._calculate_optimal_workers(test_suites)
        
        # Should use min_workers (2) since 15 tests / 10 = 1.5, but min is 2
        self.assertEqual(optimal_workers, 2)
    
    @patch('django_concurrent_test.runner.multiprocessing')
    def test_calculate_optimal_workers_large_suite(self, mock_multiprocessing):
        """Test optimal worker calculation for large test suite."""
        mock_multiprocessing.cpu_count.return_value = 8
        
        # Large test suite: 100 tests
        test_suites = [[Mock() for _ in range(20)] for _ in range(5)]
        
        optimal_workers = self.runner._calculate_optimal_workers(test_suites)
        
        # Should use 10 workers (100 tests / 10), but limited by CPU cores (8)
        self.assertEqual(optimal_workers, 8)
    
    @patch('django_concurrent_test.runner.multiprocessing')
    def test_calculate_optimal_workers_max_constraint(self, mock_multiprocessing):
        """Test optimal worker calculation with max constraint."""
        mock_multiprocessing.cpu_count.return_value = 32  # Many cores
        
        # Very large test suite: 200 tests
        test_suites = [[Mock() for _ in range(40)] for _ in range(5)]
        
        optimal_workers = self.runner._calculate_optimal_workers(test_suites)
        
        # Should use max_workers (16) since 200 tests / 10 = 20, but max is 16
        self.assertEqual(optimal_workers, 16)
    
    @patch('django_concurrent_test.runner.multiprocessing')
    def test_calculate_optimal_workers_error_handling(self, mock_multiprocessing):
        """Test optimal worker calculation error handling."""
        mock_multiprocessing.cpu_count.side_effect = Exception("CPU count error")
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_safe:
            mock_get_safe.return_value = 4
            
            test_suites = [[Mock() for _ in range(10)]]
            optimal_workers = self.runner._calculate_optimal_workers(test_suites)
            
            self.assertEqual(optimal_workers, 4)
            mock_get_safe.assert_called_once()
    
    @patch('django_concurrent_test.runner.validate_environment')
    @patch('django_concurrent_test.runner.check_telemetry_disabled')
    @patch('django_concurrent_test.runner.setup_test_databases_with_connections')
    @patch('django_concurrent_test.runner.verify_database_isolation')
    @patch('django_concurrent_test.runner.teardown_test_databases_with_connections')
    @patch('django_concurrent_test.runner.clear_connection_pool')
    def test_dynamic_scaling_integration(self, mock_clear_pool, mock_teardown, 
                                        mock_verify, mock_setup, mock_check_telemetry, 
                                        mock_validate):
        """Test dynamic scaling integration in run_tests."""
        # Mock setup
        mock_setup.return_value = {
            0: ('test_db_0', Mock()),
            1: ('test_db_1', Mock())
        }
        
        # Mock test suites
        test_suites = [[Mock(), Mock()], [Mock()]]
        
        with patch.object(self.runner, 'split_suites', return_value=test_suites):
            with patch.object(self.runner, '_calculate_optimal_workers', return_value=2):
                with patch.object(self.runner, '_run_concurrent_tests', return_value=0):
                    with patch('django_concurrent_test.runner.logger') as mock_logger:
                        result = self.runner.run_tests(['test_app'])
                        
                        # Verify dynamic scaling was used
                        mock_logger.info.assert_any_call("[CONCURRENT] Dynamic scaling: using 2 workers")
                        self.assertEqual(result, 0)


class ConnectionRecyclingTestCase(TestCase):
    """Test connection recycling enhancements."""
    
    def setUp(self):
        """Set up test environment."""
        self.manager = ConnectionManager()
        self.mock_connection = Mock()
        self.mock_connection.settings_dict = {'NAME': 'test_db'}
    
    def test_connection_recycling_with_health_check(self):
        """Test connection recycling with health check."""
        # Mock cursor for health check
        mock_cursor = Mock()
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Set operation count to trigger recycling
        self.manager._connection_stats[1]['operations'] = 999
        
        with patch.object(self.manager, '_health_check') as mock_health_check:
            self.manager._recycle_connection(1, self.mock_connection)
            
            # Verify connection was recycled
            self.mock_connection.close.assert_called_once()
            self.mock_connection.ensure_connection.assert_called_once()
            
            # Verify health check was called after recycle
            mock_health_check.assert_called_once_with(self.mock_connection)
            
            # Verify operation count was reset
            self.assertEqual(self.manager._connection_stats[1]['operations'], 0)
    
    def test_connection_recycling_error_handling(self):
        """Test connection recycling error handling."""
        # Mock connection to raise error during recycle
        self.mock_connection.close.side_effect = Exception("Recycle error")
        
        # Set operation count to trigger recycling
        self.manager._connection_stats[1]['operations'] = 999
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            self.manager._recycle_connection(1, self.mock_connection)
            
            # Verify error was logged
            mock_logger.warning.assert_called_with("Failed to recycle connection for worker 1: Recycle error")
            
            # Verify last_check was reset to force health check
            self.assertEqual(self.manager._connection_stats[1]['last_check'], 0)


class TimeoutHandlingTestCase(TestCase):
    """Test timeout handling functionality."""
    
    def test_time_limit_context_manager(self):
        """Test time_limit context manager."""
        # Test successful execution within timeout
        with time_limit(5):
            time.sleep(0.1)  # Should complete successfully
        
        # Test timeout exception
        with self.assertRaises(TestTimeoutError):
            with time_limit(1):
                time.sleep(2)  # Should timeout
    
    @patch('django_concurrent_test.runner.connections')
    def test_run_single_test_with_timeout(self, mock_connections):
        """Test _run_single_test with timeout handling."""
        runner = ConcurrentTestRunner()
        runner.timeout = 1  # 1 second timeout
        
        mock_test = Mock()
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        mock_connections.__getitem__.return_value = mock_original_connection
        
        # Mock run_suite to take longer than timeout
        with patch.object(runner, 'run_suite') as mock_run_suite:
            mock_run_suite.side_effect = lambda x: time.sleep(2)  # Longer than timeout
            
            with self.assertRaises(WorkerRetryException):
                runner._run_single_test(mock_test, mock_worker_connection)
            
            # Verify connection was restored
            mock_connections.__setitem__.assert_called_with('default', mock_original_connection)
    
    def test_test_timeout_error_class(self):
        """Test TestTimeoutError exception class."""
        error = TestTimeoutError("Test timed out")
        self.assertEqual(str(error), "Test timed out")
        self.assertIsInstance(error, Exception)


class SecurityImprovementsTestCase(TestCase):
    """Test security improvements."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.connection')
    def test_template_naming_with_project_prefix(self, mock_connection):
        """Test template naming uses project-specific prefix."""
        mock_connection.vendor = 'postgresql'
        mock_connection.settings_dict = {'NAME': 'test_db'}
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock settings with project name
        with patch('django_concurrent_test.runner.settings') as mock_settings:
            mock_settings.PROJECT_NAME = 'myproject'
            
            # Test template existence check
            self.runner._template_exists()
            
            # Verify project prefix was used
            mock_cursor.execute.assert_called_with(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                ['myproject_test_db_template']
            )
    
    @patch('django_concurrent_test.runner.connection')
    def test_template_naming_default_prefix(self, mock_connection):
        """Test template naming uses default prefix when PROJECT_NAME not set."""
        mock_connection.vendor = 'postgresql'
        mock_connection.settings_dict = {'NAME': 'test_db'}
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock settings without project name
        with patch('django_concurrent_test.runner.settings') as mock_settings:
            delattr(mock_settings, 'PROJECT_NAME')  # Remove PROJECT_NAME
            
            # Test template existence check
            self.runner._template_exists()
            
            # Verify default prefix was used
            mock_cursor.execute.assert_called_with(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                ['django_test_db_template']
            )


class IntegrationImprovementsTestCase(TestCase):
    """Integration tests for all improvements."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner(
            dynamic_scaling=True,
            benchmark=True
        )
    
    @patch('django_concurrent_test.runner.validate_environment')
    @patch('django_concurrent_test.runner.check_telemetry_disabled')
    @patch('django_concurrent_test.runner.setup_test_databases_with_connections')
    @patch('django_concurrent_test.runner.verify_database_isolation')
    @patch('django_concurrent_test.runner.teardown_test_databases_with_connections')
    @patch('django_concurrent_test.runner.clear_connection_pool')
    def test_all_improvements_integration(self, mock_clear_pool, mock_teardown, 
                                         mock_verify, mock_setup, mock_check_telemetry, 
                                         mock_validate):
        """Test integration of all improvements."""
        # Mock setup
        mock_setup.return_value = {
            0: ('test_db_0', Mock()),
            1: ('test_db_1', Mock())
        }
        
        # Mock test suites
        test_suites = [[Mock(), Mock()], [Mock()]]
        
        with patch.object(self.runner, 'split_suites', return_value=test_suites):
            with patch.object(self.runner, '_calculate_optimal_workers', return_value=2):
                with patch.object(self.runner, '_run_concurrent_tests', return_value=0):
                    with patch('django_concurrent_test.runner.logger') as mock_logger:
                        result = self.runner.run_tests(['test_app'])
                        
                        # Verify all improvements were used
                        mock_logger.info.assert_any_call("[CONCURRENT] Dynamic scaling: using 2 workers")
                        self.assertEqual(result, 0)
    
    def test_memory_usage_integration(self):
        """Test memory usage integration with metrics."""
        # Mock psutil
        with patch('django_concurrent_test.runner.psutil') as mock_psutil:
            mock_process = Mock()
            mock_process.memory_info.return_value.rss = 50 * 1024 * 1024  # 50MB
            mock_psutil.Process.return_value = mock_process
            
            # Test memory usage in metrics
            usage = self.runner._get_memory_usage()
            
            self.assertEqual(usage, 50.0)
    
    def test_timeout_integration(self):
        """Test timeout integration."""
        # Test timeout context manager
        start_time = time.time()
        
        with time_limit(1):
            time.sleep(0.5)  # Should complete within timeout
        
        duration = time.time() - start_time
        self.assertLess(duration, 1.1)  # Allow small overhead


class PerformanceTestCase(TestCase):
    """Test performance improvements."""
    
    def test_adaptive_chunking_performance(self):
        """Test adaptive chunking performance."""
        chunker = AdaptiveChunker()
        
        # Create test suites with known timings
        test_suites = [
            [Mock(_testMethodName=f'test{i}') for i in range(10)],
            [Mock(_testMethodName=f'test{i}') for i in range(10, 20)]
        ]
        
        # Set up test classes
        for suite in test_suites:
            for test in suite:
                test.__class__.__module__ = 'test_app.test_module'
                test.__class__.__name__ = 'TestClass'
        
        # Set up timings
        chunker.timings = {
            f"test_app.test_module.TestClass.test{i}": i * 0.1
            for i in range(20)
        }
        
        # Test chunking performance
        start_time = time.time()
        chunks = chunker.chunk_tests(test_suites, 4)
        duration = time.time() - start_time
        
        # Should complete quickly
        self.assertLess(duration, 0.1)
        self.assertEqual(len(chunks), 4)
    
    def test_connection_manager_performance(self):
        """Test connection manager performance."""
        manager = ConnectionManager()
        mock_connection = Mock()
        
        # Test connection switching performance
        start_time = time.time()
        
        for i in range(100):
            with manager.use_connection(i, f'test_db_{i}', mock_connection):
                pass
        
        duration = time.time() - start_time
        
        # Should complete quickly
        self.assertLess(duration, 1.0)


if __name__ == '__main__':
    unittest.main() 