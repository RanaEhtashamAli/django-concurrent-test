"""
Tests for potential improvements to the ConcurrentTestRunner.
"""

import os
import sys
import time
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
)
from django_concurrent_test.db import (
    generate_template_fingerprint,
    terminate_connections,
    PostgreSQLCloner,
    MySQLCloner,
    SQLiteCloner,
)
from django_concurrent_test.exceptions import (
    WorkerRetryException,
    DatabaseTemplateException,
    SecurityException,
    UnsupportedDatabase,
)


class EnhancedTestSkippingTestCase(TestCase):
    """Test enhanced test skipping detection."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.connections')
    def test_enhanced_test_skipping_detection(self, mock_connections):
        """Test enhanced test skipping detection with Django's mechanism."""
        mock_test = Mock()
        mock_test.skipped = True  # Django's test skipping mechanism
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        mock_connections.__getitem__.return_value = mock_original_connection
        
        # Mock result with successful execution but skipped test
        mock_result = 0  # Django returns 0 for successful but skipped tests
        
        with patch.object(self.runner, 'run_suite', return_value=mock_result):
            with patch('django_concurrent_test.runner.time_limit') as mock_time_limit:
                mock_time_limit.return_value.__enter__ = Mock()
                mock_time_limit.return_value.__exit__ = Mock(return_value=None)
                
                result = self.runner._run_single_test(mock_test, mock_worker_connection)
                
                self.assertEqual(result, 'skip')
    
    @patch('django_concurrent_test.runner.connections')
    def test_enhanced_test_skipping_no_skip(self, mock_connections):
        """Test enhanced test skipping when test is not skipped."""
        mock_test = Mock()
        mock_test.skipped = False  # Test is not skipped
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        mock_connections.__getitem__.return_value = mock_original_connection
        
        # Mock result with successful execution
        mock_result = 0  # Django returns 0 for successful tests
        
        with patch.object(self.runner, 'run_suite', return_value=mock_result):
            with patch('django_concurrent_test.runner.time_limit') as mock_time_limit:
                mock_time_limit.return_value.__enter__ = Mock()
                mock_time_limit.return_value.__exit__ = Mock(return_value=None)
                
                result = self.runner._run_single_test(mock_test, mock_worker_connection)
                
                # Should return the result, not 'skip'
                self.assertEqual(result, mock_result)
    
    @patch('django_concurrent_test.runner.connections')
    def test_enhanced_test_skipping_failed_test(self, mock_connections):
        """Test enhanced test skipping with failed test."""
        mock_test = Mock()
        mock_test.skipped = True  # Test is marked as skipped
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        mock_connections.__getitem__.return_value = mock_original_connection
        
        # Mock result with failed execution
        mock_result = 1  # Django returns non-zero for failed tests
        
        with patch.object(self.runner, 'run_suite', return_value=mock_result):
            with patch('django_concurrent_test.runner.time_limit') as mock_time_limit:
                mock_time_limit.return_value.__enter__ = Mock()
                mock_time_limit.return_value.__exit__ = Mock(return_value=None)
                
                result = self.runner._run_single_test(mock_test, mock_worker_connection)
                
                # Should return the failure result, not 'skip'
                self.assertEqual(result, mock_result)


class TemplateVersioningTestCase(TestCase):
    """Test template versioning functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_connection = Mock()
        self.mock_connection.vendor = 'postgresql'
        self.mock_connection.settings_dict = {
            'NAME': 'test_db',
            'USER': 'test_user',
            'HOST': 'localhost',
            'PORT': '5432',
            'ENGINE': 'django.db.backends.postgresql'
        }
        self.cloner = PostgreSQLCloner(self.mock_connection)
    
    @patch('django_concurrent_test.db.generate_template_fingerprint')
    def test_template_versioning_cache_hit(self, mock_fingerprint):
        """Test template versioning with cache hit."""
        mock_fingerprint.return_value = 'abc12345'
        
        # Mock cache with matching fingerprint
        with patch('django_concurrent_test.db._template_cache', {'test_db_test_user': 'test_db_template'}):
            with patch('django_concurrent_test.db._template_fingerprints', {'test_db_test_user': 'abc12345'}):
                with patch('django_concurrent_test.db._template_cache_lock'):
                    with patch('django_concurrent_test.db.logger') as mock_logger:
                        self.cloner._ensure_template_database()
                        
                        mock_logger.debug.assert_called_with(
                            "Using cached template test_db_template (fingerprint: abc12345)"
                        )
    
    @patch('django_concurrent_test.db.generate_template_fingerprint')
    def test_template_versioning_cache_miss(self, mock_fingerprint):
        """Test template versioning with cache miss due to fingerprint mismatch."""
        mock_fingerprint.return_value = 'def67890'  # Different fingerprint
        
        # Mock cache with different fingerprint
        with patch('django_concurrent_test.db._template_cache', {'test_db_test_user': 'test_db_template'}):
            with patch('django_concurrent_test.db._template_fingerprints', {'test_db_test_user': 'abc12345'}):
                with patch('django_concurrent_test.db._template_cache_lock'):
                    with patch.object(self.cloner, '_refresh_template') as mock_refresh:
                        with patch('django_concurrent_test.db.logger') as mock_logger:
                            self.cloner._ensure_template_database()
                            
                            mock_logger.info.assert_called_with(
                                "Template version mismatch, refreshing template (old: abc12345, new: def67890)"
                            )
                            mock_refresh.assert_called_with('test_db_test_user')
    
    def test_template_refresh_method(self):
        """Test template refresh method."""
        self.cloner._template_db_name = 'test_db_template'
        
        with patch.object(self.cloner, 'database_exists', return_value=True):
            with patch.object(self.cloner, 'drop_database') as mock_drop:
                with patch('django_concurrent_test.db._template_cache', {'test_key': 'test_value'}):
                    with patch('django_concurrent_test.db._template_fingerprints', {'test_key': 'test_fingerprint'}):
                        with patch('django_concurrent_test.db.logger') as mock_logger:
                            self.cloner._refresh_template('test_key')
                            
                            mock_drop.assert_called_with('test_db_template')
                            mock_logger.info.assert_called_with("Dropped outdated template: test_db_template")
                            mock_logger.info.assert_called_with("Template cache cleared, will recreate on next use")
    
    def test_generate_template_fingerprint(self):
        """Test template fingerprint generation."""
        mock_connection = Mock()
        mock_connection.settings_dict = {
            'NAME': 'test_db',
            'USER': 'test_user',
            'HOST': 'localhost',
            'PORT': '5432',
            'ENGINE': 'django.db.backends.postgresql'
        }
        mock_connection.vendor = 'postgresql'
        
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ('table1', 'column1', 'integer'),
            ('table1', 'column2', 'text'),
            ('table2', 'column1', 'varchar')
        ]
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        fingerprint = generate_template_fingerprint(mock_connection)
        
        # Should return a valid MD5 hash
        self.assertIsInstance(fingerprint, str)
        self.assertEqual(len(fingerprint), 32)  # MD5 hash length
        self.assertTrue(all(c in '0123456789abcdef' for c in fingerprint))


class DatabaseBackendExpansionTestCase(TestCase):
    """Test database backend expansion functionality."""
    
    @patch('django_concurrent_test.db.connection')
    def test_terminate_connections_sqlite3(self, mock_connection):
        """Test terminate connections for SQLite3."""
        mock_connection.vendor = 'sqlite3'
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        with patch('django_concurrent_test.db.logger') as mock_logger:
            terminate_connections('test_db', 'sqlite3')
            
            mock_logger.debug.assert_called_with("SQLite3: Skipping connection termination for test_db")
            # Should not execute any SQL commands
            mock_cursor.execute.assert_not_called()
    
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


class MemoryBasedScalingTestCase(TestCase):
    """Test memory-based scaling functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.runner = ConcurrentTestRunner()
    
    @patch('django_concurrent_test.runner.psutil')
    def test_memory_based_scaling_sufficient_memory(self, mock_psutil):
        """Test memory-based scaling with sufficient memory."""
        # Mock 8GB available memory
        mock_memory = Mock()
        mock_memory.available = 8 * (1024 ** 3)  # 8GB in bytes
        mock_psutil.virtual_memory.return_value = mock_memory
        
        workers = self.runner._calculate_memory_based_workers()
        
        # Should calculate 8GB / 0.5GB = 16 workers
        self.assertEqual(workers, 16)
    
    @patch('django_concurrent_test.runner.psutil')
    def test_memory_based_scaling_low_memory(self, mock_psutil):
        """Test memory-based scaling with low memory."""
        # Mock 1GB available memory (below 2GB threshold)
        mock_memory = Mock()
        mock_memory.available = 1 * (1024 ** 3)  # 1GB in bytes
        mock_psutil.virtual_memory.return_value = mock_memory
        
        with patch('django_concurrent_test.runner.logger') as mock_logger:
            workers = self.runner._calculate_memory_based_workers()
            
            # Should limit to 2 workers due to low memory
            self.assertEqual(workers, 2)
            mock_logger.warning.assert_called_with(
                "[CONCURRENT] Low memory available: 1.0GB, limiting workers"
            )
    
    @patch('django_concurrent_test.runner.psutil')
    def test_memory_based_scaling_exact_threshold(self, mock_psutil):
        """Test memory-based scaling at exact threshold."""
        # Mock 2GB available memory (exact threshold)
        mock_memory = Mock()
        mock_memory.available = 2 * (1024 ** 3)  # 2GB in bytes
        mock_psutil.virtual_memory.return_value = mock_memory
        
        workers = self.runner._calculate_memory_based_workers()
        
        # Should calculate 2GB / 0.5GB = 4 workers
        self.assertEqual(workers, 4)
    
    def test_memory_based_scaling_psutil_not_available(self):
        """Test memory-based scaling when psutil is not available."""
        with patch('django_concurrent_test.runner.psutil', side_effect=ImportError):
            with patch('django_concurrent_test.runner.logger') as mock_logger:
                workers = self.runner._calculate_memory_based_workers()
                
                # Should return max_workers when psutil is not available
                self.assertEqual(workers, self.runner.max_workers)
                mock_logger.warning.assert_called_with(
                    "[CONCURRENT] psutil not available, skipping memory-based scaling"
                )
    
    def test_memory_based_scaling_exception_handling(self):
        """Test memory-based scaling exception handling."""
        with patch('django_concurrent_test.runner.psutil.virtual_memory', side_effect=Exception("Memory error")):
            with patch('django_concurrent_test.runner.logger') as mock_logger:
                workers = self.runner._calculate_memory_based_workers()
                
                # Should return max_workers on exception
                self.assertEqual(workers, self.runner.max_workers)
                mock_logger.warning.assert_called_with(
                    "[CONCURRENT] Memory-based scaling failed: Memory error"
                )
    
    def test_calculate_optimal_workers_with_memory_scaling(self):
        """Test optimal workers calculation with memory scaling."""
        test_suites = [[Mock(), Mock(), Mock()], [Mock(), Mock()]]  # 5 tests total
        
        with patch.object(self.runner, '_calculate_memory_based_workers', return_value=4):
            with patch('django_concurrent_test.runner.multiprocessing.cpu_count', return_value=8):
                with patch('django_concurrent_test.runner.logger') as mock_logger:
                    workers = self.runner._calculate_optimal_workers(test_suites)
                    
                    # Should use minimum of CPU-based (8) and memory-based (4) workers
                    self.assertEqual(workers, 4)
                    mock_logger.info.assert_called_with(
                        "[CONCURRENT] Dynamic scaling: 5 tests, 8 cores, memory: 4, 4 workers"
                    )


class TimeoutHierarchyTestCase(TestCase):
    """Test timeout hierarchy functionality."""
    
    def test_timeout_hierarchy_default_values(self):
        """Test timeout hierarchy with default values."""
        runner = ConcurrentTestRunner()
        
        # Default timeout hierarchy: test < worker < global
        self.assertEqual(runner.test_timeout, 30)
        self.assertEqual(runner.worker_timeout, runner.timeout)  # Same as main timeout
        self.assertEqual(runner.global_timeout, runner.timeout * 2)  # Double main timeout
    
    @override_settings(DJANGO_TEST_TIMEOUT_PER_TEST=15, DJANGO_TEST_TIMEOUT_PER_WORKER=60, DJANGO_TEST_TIMEOUT_GLOBAL=120)
    def test_timeout_hierarchy_custom_values(self):
        """Test timeout hierarchy with custom values."""
        with patch.dict(os.environ, {
            'DJANGO_TEST_TIMEOUT_PER_TEST': '15',
            'DJANGO_TEST_TIMEOUT_PER_WORKER': '60',
            'DJANGO_TEST_TIMEOUT_GLOBAL': '120'
        }):
            with patch('django_concurrent_test.runner.logger') as mock_logger:
                runner = ConcurrentTestRunner()
                
                self.assertEqual(runner.test_timeout, 15)
                self.assertEqual(runner.worker_timeout, 60)
                self.assertEqual(runner.global_timeout, 120)
                
                # Should not log warnings for proper hierarchy
                mock_logger.warning.assert_not_called()
    
    @override_settings(DJANGO_TEST_TIMEOUT_PER_TEST=60, DJANGO_TEST_TIMEOUT_PER_WORKER=30)
    def test_timeout_hierarchy_invalid_values(self):
        """Test timeout hierarchy with invalid values."""
        with patch.dict(os.environ, {
            'DJANGO_TEST_TIMEOUT_PER_TEST': '60',
            'DJANGO_TEST_TIMEOUT_PER_WORKER': '30'
        }):
            with patch('django_concurrent_test.runner.logger') as mock_logger:
                runner = ConcurrentTestRunner()
                
                self.assertEqual(runner.test_timeout, 60)
                self.assertEqual(runner.worker_timeout, 30)
                
                # Should log warnings for invalid hierarchy
                mock_logger.warning.assert_called_with(
                    "[CONCURRENT] Test timeout (60s) should be less than worker timeout (30s)"
                )
    
    @override_settings(DJANGO_TEST_TIMEOUT_PER_WORKER=120, DJANGO_TEST_TIMEOUT_GLOBAL=60)
    def test_timeout_hierarchy_worker_greater_than_global(self):
        """Test timeout hierarchy when worker timeout is greater than global."""
        with patch.dict(os.environ, {
            'DJANGO_TEST_TIMEOUT_PER_WORKER': '120',
            'DJANGO_TEST_TIMEOUT_GLOBAL': '60'
        }):
            with patch('django_concurrent_test.runner.logger') as mock_logger:
                runner = ConcurrentTestRunner()
                
                self.assertEqual(runner.worker_timeout, 120)
                self.assertEqual(runner.global_timeout, 60)
                
                # Should log warnings for invalid hierarchy
                mock_logger.warning.assert_called_with(
                    "[CONCURRENT] Worker timeout (120s) should be less than global timeout (60s)"
                )
    
    def test_timeout_hierarchy_usage_in_test_execution(self):
        """Test that timeout hierarchy is used in test execution."""
        runner = ConcurrentTestRunner()
        runner.test_timeout = 10
        runner.worker_timeout = 30
        runner.global_timeout = 60
        
        mock_test = Mock()
        mock_worker_connection = Mock()
        mock_original_connection = Mock()
        
        with patch('django_concurrent_test.runner.connections') as mock_connections:
            mock_connections.__getitem__.return_value = mock_original_connection
            
            with patch.object(runner, 'run_suite', return_value=0):
                with patch('django_concurrent_test.runner.time_limit') as mock_time_limit:
                    mock_time_limit.return_value.__enter__ = Mock()
                    mock_time_limit.return_value.__exit__ = Mock(return_value=None)
                    
                    runner._run_single_test(mock_test, mock_worker_connection)
                    
                    # Should use test_timeout for individual tests
                    mock_time_limit.assert_called_with(10)


class IntegrationPotentialImprovementsTestCase(TestCase):
    """Integration tests for all potential improvements."""
    
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
    def test_all_potential_improvements_integration(self, mock_clear_pool, mock_teardown, 
                                                   mock_verify, mock_setup, mock_check_telemetry, 
                                                   mock_validate):
        """Test integration of all potential improvements."""
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
                        mock_logger.info.assert_any_call("[CONCURRENT] Dynamic scaling: 3 tests, 8 cores, memory: 4, 2 workers")
                        self.assertEqual(result, 0)
    
    def test_template_versioning_integration(self):
        """Test template versioning integration."""
        mock_connection = Mock()
        mock_connection.vendor = 'postgresql'
        mock_connection.settings_dict = {
            'NAME': 'test_db',
            'USER': 'test_user',
            'HOST': 'localhost',
            'PORT': '5432',
            'ENGINE': 'django.db.backends.postgresql'
        }
        
        cloner = PostgreSQLCloner(mock_connection)
        
        # Test that fingerprinting is integrated
        with patch('django_concurrent_test.db.generate_template_fingerprint', return_value='test_fingerprint'):
            with patch('django_concurrent_test.db._template_cache', {}):
                with patch('django_concurrent_test.db._template_fingerprints', {}):
                    with patch('django_concurrent_test.db._template_cache_lock'):
                        # Should not raise any exceptions
                        self.assertIsNotNone(cloner)
    
    def test_memory_scaling_integration(self):
        """Test memory scaling integration."""
        test_suites = [[Mock() for _ in range(10)]]  # 10 tests
        
        with patch('django_concurrent_test.runner.psutil.virtual_memory') as mock_memory:
            mock_memory.return_value.available = 4 * (1024 ** 3)  # 4GB
            
            with patch('django_concurrent_test.runner.multiprocessing.cpu_count', return_value=8):
                workers = self.runner._calculate_optimal_workers(test_suites)
                
                # Should use memory-based scaling
                self.assertLessEqual(workers, 8)  # Should not exceed memory limit
    
    def test_timeout_hierarchy_integration(self):
        """Test timeout hierarchy integration."""
        # Test that all timeout levels are properly configured
        self.assertLess(self.runner.test_timeout, self.runner.worker_timeout)
        self.assertLess(self.runner.worker_timeout, self.runner.global_timeout)
        
        # Test that timeouts are used in execution
        self.assertGreater(self.runner.test_timeout, 0)
        self.assertGreater(self.runner.worker_timeout, 0)
        self.assertGreater(self.runner.global_timeout, 0)


if __name__ == '__main__':
    unittest.main() 