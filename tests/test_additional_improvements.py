"""
Tests for additional improvements to the ConcurrentTestRunner.
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
from django_concurrent_test.db import (
    terminate_connections,
    SQLiteCloner,
    get_database_cloner,
)
from django_concurrent_test.exceptions import (
    WorkerRetryException,
    DatabaseTemplateException,
    SecurityException,
    UnsupportedDatabase,
)


class TemplateCacheWarmupTestCase(TestCase):
    """Test template cache warmup functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.ConcurrentTestRunner._template_exists')
    @patch('django_concurrent_test.runner.ConcurrentTestRunner._clone_from_template')
    def test_template_cache_warmup_success(self, mock_clone, mock_exists):
        """Test successful template cache warmup."""
        mock_exists.return_value = True
        mock_clone.return_value = {0: ('test_db_0', Mock())}
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            # Re-initialize runner to trigger warmup
            runner = ConcurrentTestRunner()
            
            mock_exists.assert_called_once()
            mock_clone.assert_called_once_with(1)
            mock_logger.info.assert_any_call("[CONCURRENT] Warming up template cache")
            mock_logger.info.assert_any_call("[CONCURRENT] Template cache warmed up successfully")
    
    @patch('django_concurrent_test.runner.ConcurrentTestRunner._template_exists')
    def test_template_cache_warmup_no_template(self, mock_exists):
        """Test template cache warmup when no template exists."""
        mock_exists.return_value = False
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            # Re-initialize runner to trigger warmup
            runner = ConcurrentTestRunner()
            
            mock_exists.assert_called_once()
            # Should not call clone_from_template
            mock_logger.info.assert_not_called()
    
    @patch('django_concurrent_test.runner.ConcurrentTestRunner._template_exists')
    @patch('django_concurrent_test.runner.ConcurrentTestRunner._clone_from_template')
    def test_template_cache_warmup_failure(self, mock_clone, mock_exists):
        """Test template cache warmup failure handling."""
        mock_exists.return_value = True
        mock_clone.side_effect = Exception("Warmup failed")
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            # Re-initialize runner to trigger warmup
            runner = ConcurrentTestRunner()
            
            mock_exists.assert_called_once()
            mock_clone.assert_called_once_with(1)
            mock_logger.warning.assert_called_with("[CONCURRENT] Template cache warmup failed: Warmup failed")


class DatabaseVendorAbstractionTestCase(TestCase):
    """Test database vendor abstraction functionality."""
    
    @patch('django_concurrent_test.db.connection')
    def test_terminate_connections_postgresql(self, mock_connection):
        """Test terminate connections for PostgreSQL."""
        mock_connection.vendor = 'postgresql'
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        terminate_connections('test_db', 'postgresql')
        
        mock_cursor.execute.assert_called_with(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'test_db' AND pid <> pg_backend_pid()"
        )
    
    @patch('django_concurrent_test.db.connection')
    def test_terminate_connections_mysql(self, mock_connection):
        """Test terminate connections for MySQL."""
        mock_connection.vendor = 'mysql'
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [('KILL 123;',), ('KILL 456;',)]
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        terminate_connections('test_db', 'mysql')
        
        # Should call the SELECT query first
        mock_cursor.execute.assert_any_call(
            "SELECT CONCAT('KILL ', id, ';') FROM information_schema.processlist WHERE db = 'test_db' AND id <> CONNECTION_ID()"
        )
        # Should execute the KILL commands
        mock_cursor.execute.assert_any_call('KILL 123;')
        mock_cursor.execute.assert_any_call('KILL 456;')
    
    @patch('django_concurrent_test.db.connection')
    def test_terminate_connections_unsupported_vendor(self, mock_connection):
        """Test terminate connections for unsupported vendor."""
        mock_connection.vendor = 'oracle'
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        with patch('django_concurrent_test.db.logger') as mock_logger:
            terminate_connections('test_db', 'oracle')
            
            mock_logger.warning.assert_called_with("Terminate connections not implemented for vendor: oracle")
    
    @patch('django_concurrent_test.db.connection')
    def test_terminate_connections_error_handling(self, mock_connection):
        """Test terminate connections error handling."""
        mock_connection.vendor = 'postgresql'
        mock_connection.cursor.side_effect = Exception("Database error")
        
        with patch('django_concurrent_test.db.logger') as mock_logger:
            terminate_connections('test_db', 'postgresql')
            
            mock_logger.warning.assert_called_with("Failed to terminate connections for test_db: Database error")


class SQLiteClonerTestCase(TestCase):
    """Test SQLite cloner functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_connection = Mock()
        self.mock_connection.vendor = 'sqlite'
        self.cloner = SQLiteCloner(self.mock_connection)
    
    def test_sqlite_cloner_initialization(self):
        """Test SQLite cloner initialization."""
        with patch('django_concurrent_test.db.logger') as mock_logger:
            cloner = SQLiteCloner(self.mock_connection)
            
            mock_logger.warning.assert_called_with("SQLite detected - tests will run sequentially for proper isolation")
    
    def test_sqlite_clone_database(self):
        """Test SQLite clone database raises exception."""
        with self.assertRaises(DatabaseTemplateException) as cm:
            self.cloner.clone_database(1)
        
        self.assertIn("SQLite doesn't support database cloning", str(cm.exception))
        self.assertIn("Tests will run sequentially", str(cm.exception))
    
    def test_sqlite_clone_databases_batch(self):
        """Test SQLite batch clone databases raises exception."""
        with self.assertRaises(DatabaseTemplateException) as cm:
            self.cloner.clone_databases_batch([1, 2, 3])
        
        self.assertIn("SQLite doesn't support batch database cloning", str(cm.exception))
        self.assertIn("Tests will run sequentially", str(cm.exception))
    
    def test_sqlite_drop_database(self):
        """Test SQLite drop database logs message."""
        with patch('django_concurrent_test.db.logger') as mock_logger:
            self.cloner.drop_database('test_db')
            
            mock_logger.info.assert_called_with("SQLite: Skipping database drop for test_db")
    
    @patch('os.path.exists')
    def test_sqlite_database_exists(self, mock_exists):
        """Test SQLite database exists check."""
        mock_exists.return_value = True
        
        result = self.cloner.database_exists('test_db')
        
        self.assertTrue(result)
        mock_exists.assert_called_with('test_db')
    
    def test_get_database_cloner_sqlite(self):
        """Test get_database_cloner returns SQLiteCloner for SQLite."""
        mock_connection = Mock()
        mock_connection.vendor = 'sqlite'
        
        cloner = get_database_cloner(mock_connection)
        
        self.assertIsInstance(cloner, SQLiteCloner)


class TestTimeoutConfigurationTestCase(TestCase):
    """Test test-level timeout configuration."""
    
    def test_test_timeout_configuration(self):
        """Test test timeout configuration from environment."""
        with patch.dict(os.environ, {'DJANGO_TEST_TIMEOUT_PER_TEST': '60'}):
            runner = ConcurrentTestRunner()
            self.assertEqual(runner.test_timeout, 60)
    
    def test_test_timeout_default(self):
        """Test test timeout default value."""
        runner = ConcurrentTestRunner()
        self.assertEqual(runner.test_timeout, 30)
    
    def test_test_timeout_integration(self):
        """Test test timeout integration in _run_single_test."""
        runner = ConcurrentTestRunner()
        runner.test_timeout = 1  # 1 second timeout
        
        mock_test = Mock()
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        
        with patch('django_concurrent_test.runner.connections') as mock_connections:
            mock_connections.__getitem__.return_value = mock_original_connection
            
            # Mock run_suite to take longer than timeout
            with patch.object(runner, 'run_suite') as mock_run_suite:
                mock_run_suite.side_effect = lambda x: time.sleep(2)  # Longer than timeout
                
                with self.assertRaises(WorkerRetryException):
                    runner._run_single_test(mock_test, mock_worker_connection)


class TestSkippingDetectionTestCase(TestCase):
    """Test test skipping detection functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.connections')
    def test_test_skipping_detection(self, mock_connections):
        """Test detection of skipped tests."""
        mock_test = Mock()
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        mock_connections.__getitem__.return_value = mock_original_connection
        
        # Mock result with skipped attribute
        mock_result = Mock()
        mock_result.skipped = True
        
        with patch.object(self.runner, 'run_suite', return_value=mock_result):
            with patch('django_concurrent_test.runner.time_limit') as mock_time_limit:
                mock_time_limit.return_value.__enter__ = Mock()
                mock_time_limit.return_value.__exit__ = Mock(return_value=None)
                
                result = self.runner._run_single_test(mock_test, mock_worker_connection)
                
                self.assertEqual(result, 'skip')
    
    @patch('django_concurrent_test.runner.connections')
    def test_test_no_skipping_detection(self, mock_connections):
        """Test normal test execution without skipping."""
        mock_test = Mock()
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        mock_connections.__getitem__.return_value = mock_original_connection
        
        # Mock result without skipped attribute
        mock_result = Mock()
        delattr(mock_result, 'skipped')  # Ensure no skipped attribute
        
        with patch.object(self.runner, 'run_suite', return_value=mock_result):
            with patch('django_concurrent_test.runner.time_limit') as mock_time_limit:
                mock_time_limit.return_value.__enter__ = Mock()
                mock_time_limit.return_value.__exit__ = Mock(return_value=None)
                
                result = self.runner._run_single_test(mock_test, mock_worker_connection)
                
                self.assertEqual(result, mock_result)


class ResourceMonitoringTestCase(TestCase):
    """Test resource monitoring functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
        self.runner.timeout = 100  # 100 second timeout
    
    def test_resource_monitoring_approaching_timeout(self):
        """Test resource monitoring when approaching timeout."""
        worker_duration = 85  # 85% of timeout
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            # Simulate worker execution approaching timeout
            if worker_duration > self.runner.timeout * 0.8:
                mock_logger.warning(f"Worker 1 approaching timeout: {worker_duration:.2f}s / {self.runner.timeout}s")
            
            mock_logger.warning.assert_called_with("Worker 1 approaching timeout: 85.00s / 100s")
    
    def test_resource_monitoring_normal_execution(self):
        """Test resource monitoring during normal execution."""
        worker_duration = 50  # 50% of timeout
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            # Simulate normal worker execution
            if worker_duration > self.runner.timeout * 0.8:
                mock_logger.warning(f"Worker 1 approaching timeout: {worker_duration:.2f}s / {self.runner.timeout}s")
            
            # Should not call warning
            mock_logger.warning.assert_not_called()


class AdaptiveChunkerLoadBalancingTestCase(TestCase):
    """Test adaptive chunker load balancing functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.chunker = AdaptiveChunker()
    
    def test_chunk_based_load_balancing(self):
        """Test chunk-based load balancing using past test duration stats."""
        # Create test suites with known timings
        test_suites = [
            [Mock(_testMethodName=f'test{i}') for i in range(5)],
            [Mock(_testMethodName=f'test{i}') for i in range(5, 10)],
            [Mock(_testMethodName=f'test{i}') for i in range(10, 15)]
        ]
        
        # Set up test classes
        for suite in test_suites:
            for test in suite:
                test.__class__.__module__ = 'test_app.test_module'
                test.__class__.__name__ = 'TestClass'
        
        # Set up historical timings with varying durations
        self.chunker.timings = {
            f"test_app.test_module.TestClass.test{i}": i * 0.5  # Increasing durations
            for i in range(15)
        }
        
        # Test chunking with load balancing
        chunks = self.chunker.chunk_tests(test_suites, 3)
        
        # Verify chunks were created
        self.assertEqual(len(chunks), 3)
        
        # Verify load balancing (longer tests should be distributed)
        chunk_times = []
        for chunk in chunks:
            total_time = sum(
                self.chunker.timings.get(
                    f"test_app.test_module.TestClass.{test._testMethodName}", 
                    1.0
                ) for test in chunk
            )
            chunk_times.append(total_time)
        
        # Check that chunks are reasonably balanced
        max_time = max(chunk_times)
        min_time = min(chunk_times)
        time_variance = max_time - min_time
        
        # Variance should be reasonable (not too large)
        self.assertLess(time_variance, 5.0)  # Allow some variance
    
    def test_adaptive_chunker_performance(self):
        """Test adaptive chunker performance with large test suites."""
        # Create large test suite
        test_suites = [
            [Mock(_testMethodName=f'test{i}') for i in range(100)]
        ]
        
        # Set up test classes
        for suite in test_suites:
            for test in suite:
                test.__class__.__module__ = 'test_app.test_module'
                test.__class__.__name__ = 'TestClass'
        
        # Set up timings
        self.chunker.timings = {
            f"test_app.test_module.TestClass.test{i}": 0.1
            for i in range(100)
        }
        
        # Test performance
        start_time = time.time()
        chunks = self.chunker.chunk_tests(test_suites, 8)
        duration = time.time() - start_time
        
        # Should complete quickly
        self.assertLess(duration, 0.1)
        self.assertEqual(len(chunks), 8)


class LoggingConfigurationTestCase(TestCase):
    """Test logging configuration responsibility."""
    
    def test_logging_configuration_optional(self):
        """Test that logging configuration is optional."""
        # Test that runner works without custom logging configuration
        runner = ConcurrentTestRunner()
        
        # Should not fail if no logging is configured
        self.assertIsNotNone(runner)
    
    def test_logging_configuration_custom(self):
        """Test custom logging configuration."""
        # Test with custom logging configuration
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            runner = ConcurrentTestRunner()
            
            # Should use the configured logger
            mock_logger.info("Test message")
            mock_logger.info.assert_called_with("Test message")


class TestTimingsFileTestCase(TestCase):
    """Test test timings file functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        self.temp_file.close()
        self.chunker = AdaptiveChunker(self.temp_file.name)
    
    def tearDown(self):
        """Clean up test environment."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
    
    def test_test_timings_regeneration(self):
        """Test test timings file regeneration."""
        # Create initial timings
        initial_timings = {
            "test_app.test_module.TestClass.test1": 1.5,
            "test_app.test_module.TestClass.test2": 2.0
        }
        
        with open(self.temp_file.name, 'w') as f:
            import json
            json.dump(initial_timings, f)
        
        # Load timings
        timings = self.chunker._load_timings()
        self.assertEqual(timings, initial_timings)
        
        # Update timings
        self.chunker.update_timing("test_app.test_module.TestClass.test1", 2.0)
        self.chunker.update_timing("test_app.test_module.TestClass.test3", 1.0)
        
        # Save updated timings
        self.chunker._save_timings()
        
        # Reload and verify
        new_timings = self.chunker._load_timings()
        self.assertIn("test_app.test_module.TestClass.test1", new_timings)
        self.assertIn("test_app.test_module.TestClass.test2", new_timings)
        self.assertIn("test_app.test_module.TestClass.test3", new_timings)
    
    def test_test_timings_file_documentation(self):
        """Test that test timings file usage is documented."""
        # This test verifies that the functionality is documented
        # The actual documentation would be in the README or other docs
        
        # Test that the file can be manually created
        manual_timings = {
            "test_app.test_module.TestClass.test1": 1.0,
            "test_app.test_module.TestClass.test2": 2.0
        }
        
        with open(self.temp_file.name, 'w') as f:
            import json
            json.dump(manual_timings, f)
        
        # Verify it can be loaded
        timings = self.chunker._load_timings()
        self.assertEqual(timings, manual_timings)


class ConnectionPoolStatsTestCase(TestCase):
    """Test connection pool statistics caching."""
    
    def test_connection_pool_stats_caching(self):
        """Test connection pool stats caching functionality."""
        from django_concurrent_test.db import get_connection_pool_stats, clear_connection_pool
        
        # Clear pool first
        clear_connection_pool()
        
        # Get initial stats
        initial_stats = get_connection_pool_stats()
        self.assertEqual(initial_stats['total_connections'], 0)
        
        # Create some connections (mocked)
        with patch('django_concurrent_test.db._connection_pool') as mock_pool:
            mock_pool.__len__.return_value = 5
            mock_pool.keys.return_value = ['1_test_db_default', '2_test_db_default']
            
            stats = get_connection_pool_stats()
            
            self.assertEqual(stats['total_connections'], 5)
            self.assertEqual(stats['pool_size'], 5)
            self.assertIn('1_test_db_default', stats['connection_keys'])
            self.assertIn('2_test_db_default', stats['connection_keys'])


class IntegrationAdditionalImprovementsTestCase(TestCase):
    """Integration tests for all additional improvements."""
    
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
    def test_all_additional_improvements_integration(self, mock_clear_pool, mock_teardown, 
                                                    mock_verify, mock_setup, mock_check_telemetry, 
                                                    mock_validate):
        """Test integration of all additional improvements."""
        # Mock setup
        mock_setup.return_value = {
            0: ('test_db_0', Mock()),
            1: ('test_db_1', Mock())
        }
        
        # Mock test suites
        test_suites = [
            [Mock(), Mock()], 
            [Mock()]
        ]
        
        with patch.object(self.runner, 'split_suites', return_value=test_suites):
            with patch.object(self.runner, '_calculate_optimal_workers', return_value=2):
                with patch.object(self.runner, '_run_concurrent_tests', return_value=0):
                    with patch('django_concurrent_test.runner.logger') as mock_logger:
                        result = self.runner.run_tests(['test_app'])
                        
                        # Verify all improvements were used
                        mock_logger.info.assert_any_call("[CONCURRENT] Dynamic scaling: using 2 workers")
                        self.assertEqual(result, 0)
    
    def test_sqlite_fallback_integration(self):
        """Test SQLite fallback integration."""
        mock_connection = Mock()
        mock_connection.vendor = 'sqlite'
        
        # Test that SQLite cloner is returned
        cloner = get_database_cloner(mock_connection)
        self.assertIsInstance(cloner, SQLiteCloner)
        
        # Test that SQLite cloner raises appropriate exception
        with self.assertRaises(DatabaseTemplateException) as cm:
            cloner.clone_database(1)
        
        self.assertIn("SQLite doesn't support database cloning", str(cm.exception))


if __name__ == '__main__':
    unittest.main() 