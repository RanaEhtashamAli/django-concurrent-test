"""
Tests for the ConcurrentTestRunner class.
"""

import os
import unittest
from unittest.mock import patch, MagicMock, call
from django.test import TestCase, override_settings
from django.test.runner import DiscoverRunner

from django_concurrent_test.exceptions import (
    SecurityException,
    UnsupportedDatabase,
    DatabaseTemplateException,
)
from django_concurrent_test.runner import ConcurrentTestRunner


class ConcurrentTestRunnerTests(TestCase):
    """Test ConcurrentTestRunner class."""
    
    def setUp(self):
        """Set up test environment."""
        # Clear environment variables
        for key in ['DJANGO_ENABLE_CONCURRENT', 'DJANGO_TEST_WORKERS', 'DJANGO_TEST_TIMEOUT', 'DJANGO_TEST_BENCHMARK']:
            if key in os.environ:
                del os.environ[key]
    
    def test_init_default_values(self):
        """Test ConcurrentTestRunner initialization with default values."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 4
            
            runner = ConcurrentTestRunner()
            
            self.assertEqual(runner.worker_count, 4)
            self.assertEqual(runner.timeout, 300)
            self.assertFalse(runner.benchmark)
            self.assertIsNone(runner.junit_xml)
    
    def test_init_custom_values(self):
        """Test ConcurrentTestRunner initialization with custom values."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 4
            
            runner = ConcurrentTestRunner(
                benchmark=True,
                junitxml='results.xml'
            )
            
            self.assertEqual(runner.worker_count, 4)
            self.assertEqual(runner.timeout, 300)
            self.assertTrue(runner.benchmark)
            self.assertEqual(runner.junit_xml, 'results.xml')
    
    def test_init_environment_override(self):
        """Test ConcurrentTestRunner initialization with environment override."""
        os.environ['DJANGO_TEST_WORKERS'] = '6'
        os.environ['DJANGO_TEST_TIMEOUT'] = '600'
        os.environ['DJANGO_TEST_BENCHMARK'] = 'True'
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 6
            
            runner = ConcurrentTestRunner()
            
            self.assertEqual(runner.worker_count, 6)
            self.assertEqual(runner.timeout, 600)
            self.assertTrue(runner.benchmark)
    
    def test_split_suites_no_labels(self):
        """Test splitting test suites with no labels."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock build_suite to return a list of test cases
            mock_suite = [MagicMock() for _ in range(10)]
            runner.build_suite = MagicMock(return_value=mock_suite)
            
            suites = runner.split_suites([])
            
            self.assertEqual(len(suites), 2)
            self.assertEqual(len(suites[0]), 5)
            self.assertEqual(len(suites[1]), 5)
    
    def test_split_suites_with_labels(self):
        """Test splitting test suites with specific labels."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock build_suite to return a list of test cases
            mock_suite = [MagicMock() for _ in range(6)]
            runner.build_suite = MagicMock(return_value=mock_suite)
            
            suites = runner.split_suites(['app1', 'app2'])
            
            self.assertEqual(len(suites), 2)
            self.assertEqual(len(suites[0]), 3)
            self.assertEqual(len(suites[1]), 3)
            runner.build_suite.assert_called_with(['app1', 'app2'])
    
    def test_split_suites_single_worker(self):
        """Test splitting test suites with single worker."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 1
            
            runner = ConcurrentTestRunner()
            
            # Mock build_suite to return a list of test cases
            mock_suite = [MagicMock() for _ in range(5)]
            runner.build_suite = MagicMock(return_value=mock_suite)
            
            suites = runner.split_suites([])
            
            self.assertEqual(len(suites), 1)
            self.assertEqual(len(suites[0]), 5)
    
    def test_split_suites_empty_suite(self):
        """Test splitting test suites with empty suite."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock build_suite to return empty list
            runner.build_suite = MagicMock(return_value=[])
            
            suites = runner.split_suites([])
            
            self.assertEqual(len(suites), 1)
            self.assertEqual(len(suites[0]), 0)
    
    @patch('django_concurrent_test.runner.setup_test_databases')
    def test_setup_databases_success(self, mock_setup):
        """Test successful database setup."""
        mock_setup.return_value = ['test_worker_0', 'test_worker_1']
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            result = runner._setup_databases()
            
            self.assertEqual(result, ['test_worker_0', 'test_worker_1'])
            mock_setup.assert_called_with(2)
    
    @patch('django_concurrent_test.runner.setup_test_databases')
    def test_setup_databases_failure(self, mock_setup):
        """Test database setup failure."""
        mock_setup.side_effect = Exception("Setup failed")
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            with self.assertRaises(DatabaseTemplateException) as cm:
                runner._setup_databases()
            
            self.assertIn("Failed to setup test databases", str(cm.exception))
    
    @patch('django_concurrent_test.runner.teardown_test_databases')
    def test_teardown_databases_success(self, mock_teardown):
        """Test successful database teardown."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            database_names = ['test_worker_0', 'test_worker_1']
            runner._teardown_databases(database_names)
            
            mock_teardown.assert_called_with(database_names)
    
    @patch('django_concurrent_test.runner.teardown_test_databases')
    def test_teardown_databases_failure(self, mock_teardown):
        """Test database teardown with failure."""
        mock_teardown.side_effect = Exception("Teardown failed")
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Should not raise exception, just log warning
            database_names = ['test_worker_0', 'test_worker_1']
            runner._teardown_databases(database_names)
    
    @patch('django_concurrent_test.runner.ThreadPoolExecutor')
    def test_run_concurrent_tests_success(self, mock_executor):
        """Test successful concurrent test execution."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock executor context manager
            mock_executor_instance = MagicMock()
            mock_executor.return_value.__enter__.return_value = mock_executor_instance
            
            # Mock futures
            mock_future1 = MagicMock()
            mock_future2 = MagicMock()
            mock_future1.result.return_value = {'failures': 0, 'db_operations': 1, 'db_errors': 0}
            mock_future2.result.return_value = {'failures': 1, 'db_operations': 1, 'db_errors': 0}
            
            mock_executor_instance.submit.side_effect = [mock_future1, mock_future2]
            mock_executor_instance.as_completed.return_value = [mock_future1, mock_future2]
            
            test_suites = [[MagicMock()], [MagicMock()]]
            database_names = ['test_worker_0', 'test_worker_1']
            
            result = runner._run_concurrent_tests(test_suites, database_names)
            
            self.assertEqual(result, 1)  # Total failures
            self.assertEqual(runner.database_operations, 2)
            self.assertEqual(runner.database_errors, 0)
    
    @patch('django_concurrent_test.runner.ThreadPoolExecutor')
    def test_run_concurrent_tests_timeout(self, mock_executor):
        """Test concurrent test execution with timeout."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock executor context manager
            mock_executor_instance = MagicMock()
            mock_executor.return_value.__enter__.return_value = mock_executor_instance
            
            # Mock futures with timeout
            mock_future = MagicMock()
            mock_future.result.side_effect = TimeoutError("Worker timeout")
            
            mock_executor_instance.submit.return_value = mock_future
            mock_executor_instance.as_completed.return_value = [mock_future]
            
            test_suites = [[MagicMock()]]
            database_names = ['test_worker_0']
            
            result = runner._run_concurrent_tests(test_suites, database_names)
            
            self.assertEqual(result, 1)  # Timeout counts as failure
            self.assertEqual(runner.database_errors, 1)
    
    @patch('django_concurrent_test.runner.ThreadPoolExecutor')
    def test_run_concurrent_tests_exception(self, mock_executor):
        """Test concurrent test execution with exception."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock executor context manager
            mock_executor_instance = MagicMock()
            mock_executor.return_value.__enter__.return_value = mock_executor_instance
            
            # Mock futures with exception
            mock_future = MagicMock()
            mock_future.result.side_effect = Exception("Worker error")
            
            mock_executor_instance.submit.return_value = mock_future
            mock_executor_instance.as_completed.return_value = [mock_future]
            
            test_suites = [[MagicMock()]]
            database_names = ['test_worker_0']
            
            result = runner._run_concurrent_tests(test_suites, database_names)
            
            self.assertEqual(result, 1)  # Exception counts as failure
            self.assertEqual(runner.database_errors, 1)
    
    @patch('django_concurrent_test.runner.settings')
    @patch('django_concurrent_test.runner.connection')
    def test_configure_worker_database(self, mock_connection, mock_settings):
        """Test worker database configuration."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            runner._configure_worker_database('test_worker_1')
            
            # Check that database settings were updated
            self.assertEqual(mock_settings.DATABASES['default']['NAME'], 'test_worker_1')
            mock_connection.close.assert_called_once()
            mock_connection.ensure_connection.assert_called_once()
    
    def test_generate_benchmark_report(self):
        """Test benchmark report generation."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner(benchmark=True)
            runner.start_time = 100.0
            runner.end_time = 110.0
            runner.worker_results = {
                0: {'duration': 5.0, 'failures': 0},
                1: {'duration': 6.0, 'failures': 1}
            }
            runner.database_operations = 4
            runner.database_errors = 0
            
            # Mock print to capture output
            with patch('builtins.print') as mock_print:
                runner._generate_benchmark_report()
                
                # Check that benchmark report was printed
                mock_print.assert_called()
                calls = [call[0][0] for call in mock_print.call_args_list]
                self.assertTrue(any('[CONCURRENT] Benchmark Report' in call for call in calls))
    
    def test_fallback_to_sequential(self):
        """Test fallback to sequential testing."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock parent run_tests method
            with patch.object(DiscoverRunner, 'run_tests') as mock_parent_run:
                mock_parent_run.return_value = 0
                
                result = runner._fallback_to_sequential(['app1'], verbosity=2)
                
                self.assertEqual(result, 0)
                mock_parent_run.assert_called_with(['app1'], verbosity=2)
    
    def test_run_suite(self):
        """Test running a single test suite."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock DiscoverRunner
            with patch('django_concurrent_test.runner.DiscoverRunner') as mock_discover:
                mock_discover_instance = MagicMock()
                mock_discover.return_value = mock_discover_instance
                mock_discover_instance.run_suite.return_value = 1
                
                suite = [MagicMock()]
                result = runner.run_suite(suite)
                
                self.assertEqual(result, 1)
                mock_discover_instance.run_suite.assert_called_with(suite)


class ConcurrentTestRunnerIntegrationTests(TestCase):
    """Integration tests for ConcurrentTestRunner."""
    
    def setUp(self):
        """Set up test environment."""
        # Clear environment variables
        for key in ['DJANGO_ENABLE_CONCURRENT', 'DJANGO_TEST_WORKERS', 'DJANGO_TEST_TIMEOUT', 'DJANGO_TEST_BENCHMARK']:
            if key in os.environ:
                del os.environ[key]
    
    @patch('django_concurrent_test.runner.validate_environment')
    @patch('django_concurrent_test.runner.check_telemetry_disabled')
    @patch('django_concurrent_test.runner.setup_test_databases')
    @patch('django_concurrent_test.runner.teardown_test_databases')
    @patch('django_concurrent_test.runner.ThreadPoolExecutor')
    def test_run_tests_success(self, mock_executor, mock_teardown, mock_setup, mock_check_telemetry, mock_validate):
        """Test successful test run."""
        # Setup mocks
        mock_setup.return_value = ['test_worker_0', 'test_worker_1']
        
        mock_executor_instance = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_executor_instance
        
        mock_future = MagicMock()
        mock_future.result.return_value = {'failures': 0, 'db_operations': 1, 'db_errors': 0}
        
        mock_executor_instance.submit.return_value = mock_future
        mock_executor_instance.as_completed.return_value = [mock_future]
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock split_suites to return multiple suites
            runner.split_suites = MagicMock(return_value=[[MagicMock()], [MagicMock()]])
            
            result = runner.run_tests(['app1'])
            
            self.assertEqual(result, 0)
            mock_validate.assert_called_once()
            mock_check_telemetry.assert_called_once()
            mock_setup.assert_called_once()
            mock_teardown.assert_called_once()
    
    @patch('django_concurrent_test.runner.validate_environment')
    def test_run_tests_security_exception(self, mock_validate):
        """Test test run with security exception."""
        mock_validate.side_effect = SecurityException("Security check failed")
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock fallback method
            with patch.object(runner, '_fallback_to_sequential') as mock_fallback:
                mock_fallback.return_value = 0
                
                result = runner.run_tests(['app1'])
                
                self.assertEqual(result, 0)
                mock_fallback.assert_called_with(['app1'])
    
    @patch('django_concurrent_test.runner.validate_environment')
    def test_run_tests_unsupported_database(self, mock_validate):
        """Test test run with unsupported database."""
        mock_validate.side_effect = UnsupportedDatabase("Database not supported")
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock fallback method
            with patch.object(runner, '_fallback_to_sequential') as mock_fallback:
                mock_fallback.return_value = 0
                
                result = runner.run_tests(['app1'])
                
                self.assertEqual(result, 0)
                mock_fallback.assert_called_with(['app1'])
    
    @patch('django_concurrent_test.runner.validate_environment')
    def test_run_tests_single_suite(self, mock_validate):
        """Test test run with single test suite."""
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock split_suites to return single suite
            runner.split_suites = MagicMock(return_value=[[MagicMock()]])
            runner.run_suite = MagicMock(return_value=0)
            
            result = runner.run_tests(['app1'])
            
            self.assertEqual(result, 0)
            runner.run_suite.assert_called_once()
    
    @patch('django_concurrent_test.runner.validate_environment')
    def test_run_tests_unexpected_exception(self, mock_validate):
        """Test test run with unexpected exception."""
        mock_validate.side_effect = Exception("Unexpected error")
        
        with patch('django_concurrent_test.runner.get_safe_worker_count') as mock_get_workers:
            mock_get_workers.return_value = 2
            
            runner = ConcurrentTestRunner()
            
            # Mock fallback method
            with patch.object(runner, '_fallback_to_sequential') as mock_fallback:
                mock_fallback.return_value = 0
                
                result = runner.run_tests(['app1'])
                
                self.assertEqual(result, 0)
                mock_fallback.assert_called_with(['app1'])


if __name__ == '__main__':
    unittest.main() 