"""
Tests for database optimizations in django-concurrent-test package.

This module tests the new optimizations including:
- Template caching for PostgreSQL and MySQL
- Batch database creation
- Thread-safe connection handling
- Database isolation verification
- Connection pooling
"""

import os
import unittest
import threading
import time
from unittest.mock import patch, MagicMock, call
from django.test import TestCase, override_settings
from django.db import connection, connections

from django_concurrent_test.exceptions import (
    DatabaseCloneException,
    UnsupportedDatabase,
)
from django_concurrent_test.db import (
    PostgreSQLCloner,
    MySQLCloner,
    get_thread_safe_connection,
    get_worker_connection,
    close_worker_connection,
    clear_connection_pool,
    get_connection_pool_stats,
    setup_test_databases,
    setup_test_databases_with_connections,
    teardown_test_databases,
    teardown_test_databases_with_connections,
    verify_database_isolation,
    worker_database,
    worker_database_with_isolation,
)


class TemplateCachingTests(TestCase):
    """Test template caching functionality."""
    
    def setUp(self):
        """Set up test environment."""
        # Clear template cache
        from django_concurrent_test.db import _template_cache, _template_cache_lock
        with _template_cache_lock:
            _template_cache.clear()
    
    @patch('django_concurrent_test.db.connection')
    def test_postgresql_template_caching(self, mock_connection):
        """Test PostgreSQL template caching."""
        mock_connection.settings_dict = {
            'NAME': 'test_main',
            'USER': 'test_user',
        }
        mock_connection.vendor = 'postgresql'
        
        cloner = PostgreSQLCloner(mock_connection)
        
        # Mock database operations
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database existence checks
        cloner.database_exists = MagicMock(side_effect=lambda name: name == 'test_main_template')
        
        # First call should create template
        cloner._ensure_template_database()
        
        # Verify template creation was called
        mock_cursor.execute.assert_any_call(
            "CREATE DATABASE test_main_template "
            "TEMPLATE test_main WITH OWNER test_user"
        )
        mock_cursor.execute.assert_any_call(
            "UPDATE pg_database SET datistemplate = true "
            "WHERE datname = 'test_main_template'"
        )
        
        # Second call should reuse template
        mock_cursor.reset_mock()
        cloner._ensure_template_database()
        
        # Verify no additional template creation
        create_calls = [call for call in mock_cursor.execute.call_args_list 
                       if 'CREATE DATABASE' in str(call)]
        self.assertEqual(len(create_calls), 0)
    
    @patch('django_concurrent_test.db.connection')
    def test_mysql_template_caching(self, mock_connection):
        """Test MySQL template caching."""
        mock_connection.settings_dict = {
            'NAME': 'test_main',
        }
        mock_connection.vendor = 'mysql'
        
        cloner = MySQLCloner(mock_connection)
        
        # Mock database operations
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [['table1'], ['table2']]
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database existence checks
        cloner.database_exists = MagicMock(side_effect=lambda name: name == 'test_main_template')
        
        # First call should create template
        cloner._ensure_template_database()
        
        # Verify template creation was called
        mock_cursor.execute.assert_any_call("CREATE DATABASE test_main_template")
        mock_cursor.execute.assert_any_call(
            "CREATE TABLE test_main_template.table1 "
            "LIKE test_main.table1 IGNORE DATA"
        )
        
        # Second call should reuse template
        mock_cursor.reset_mock()
        cloner._ensure_template_database()
        
        # Verify no additional template creation
        create_calls = [call for call in mock_cursor.execute.call_args_list 
                       if 'CREATE DATABASE' in str(call)]
        self.assertEqual(len(create_calls), 0)


class BatchOperationsTests(TestCase):
    """Test batch database operations."""
    
    @patch('django_concurrent_test.db.connection')
    def test_postgresql_batch_cloning(self, mock_connection):
        """Test PostgreSQL batch database cloning."""
        mock_connection.settings_dict = {
            'NAME': 'test_main',
            'USER': 'test_user',
        }
        mock_connection.vendor = 'postgresql'
        
        cloner = PostgreSQLCloner(mock_connection)
        
        # Mock template database
        cloner._template_db_name = 'test_main_template'
        cloner._ensure_template_database = MagicMock()
        
        # Mock database operations
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database existence checks
        cloner.database_exists = MagicMock(return_value=False)
        
        # Test batch cloning
        worker_ids = [0, 1, 2]
        result = cloner.clone_databases_batch(worker_ids)
        
        # Verify transaction was used
        mock_cursor.execute.assert_any_call("BEGIN")
        mock_cursor.execute.assert_any_call("COMMIT")
        
        # Verify databases were created
        expected_names = ['test_main_worker_0', 'test_main_worker_1', 'test_main_worker_2']
        self.assertEqual(result, expected_names)
        
        # Verify each database creation
        for db_name in expected_names:
            mock_cursor.execute.assert_any_call(
                f"CREATE DATABASE {db_name} "
                f"TEMPLATE template0"
            )
    
    @patch('django_concurrent_test.db.connection')
    def test_mysql_batch_cloning(self, mock_connection):
        """Test MySQL batch database cloning."""
        mock_connection.settings_dict = {
            'NAME': 'test_main',
        }
        mock_connection.vendor = 'mysql'
        
        cloner = MySQLCloner(mock_connection)
        
        # Mock template database
        cloner._template_db_name = 'test_main_template'
        cloner._ensure_template_database = MagicMock()
        
        # Mock database operations
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [['table1'], ['table2']]
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database existence checks
        cloner.database_exists = MagicMock(return_value=False)
        
        # Test batch cloning
        worker_ids = [0, 1]
        result = cloner.clone_databases_batch(worker_ids)
        
        # Verify transaction was used
        mock_cursor.execute.assert_any_call("START TRANSACTION")
        mock_cursor.execute.assert_any_call("COMMIT")
        
        # Verify databases were created
        expected_names = ['test_main_worker_0', 'test_main_worker_1']
        self.assertEqual(result, expected_names)
        
        # Verify each database creation
        for db_name in expected_names:
            mock_cursor.execute.assert_any_call(f"CREATE DATABASE {db_name}")


class ThreadSafetyTests(TestCase):
    """Test thread-safe connection handling."""
    
    def setUp(self):
        """Set up test environment."""
        clear_connection_pool()
    
    def tearDown(self):
        """Clean up test environment."""
        clear_connection_pool()
    
    @patch('django_concurrent_test.db.connections')
    def test_get_thread_safe_connection(self, mock_connections):
        """Test thread-safe connection retrieval."""
        mock_connection = MagicMock()
        mock_connections.__getitem__.return_value = mock_connection
        
        result = get_thread_safe_connection('default')
        
        self.assertEqual(result, mock_connection)
        mock_connection.ensure_connection.assert_called_once()
    
    @patch('django_concurrent_test.db.get_thread_safe_connection')
    def test_get_worker_connection(self, mock_get_connection):
        """Test worker connection creation and caching."""
        mock_connection = MagicMock()
        mock_connection.settings_dict = {'NAME': 'test_db'}
        mock_get_connection.return_value = mock_connection
        
        # Test creating new connection
        result = get_worker_connection(1, 'test_worker_1')
        
        self.assertEqual(result, mock_connection)
        self.assertEqual(mock_connection.settings_dict['NAME'], 'test_worker_1')
        
        # Test retrieving cached connection
        result2 = get_worker_connection(1, 'test_worker_1')
        self.assertEqual(result2, mock_connection)
        
        # Verify connection was cached
        pool_stats = get_connection_pool_stats()
        self.assertIn('1_test_worker_1_default', pool_stats['connection_keys'])
    
    def test_connection_pool_thread_safety(self):
        """Test connection pool thread safety."""
        results = []
        errors = []
        
        def worker_operation(worker_id):
            try:
                with patch('django_concurrent_test.db.get_thread_safe_connection') as mock_get:
                    mock_connection = MagicMock()
                    mock_connection.settings_dict = {'NAME': 'test_db'}
                    mock_get.return_value = mock_connection
                    
                    # Create worker connection
                    conn = get_worker_connection(worker_id, f'test_worker_{worker_id}')
                    results.append((worker_id, conn))
                    
                    # Close worker connection
                    close_worker_connection(worker_id, f'test_worker_{worker_id}')
                    
            except Exception as e:
                errors.append((worker_id, e))
        
        # Run multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker_operation, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Verify no errors occurred
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 5)
        
        # Verify pool is empty after cleanup
        pool_stats = get_connection_pool_stats()
        self.assertEqual(pool_stats['total_connections'], 0)
    
    def test_clear_connection_pool(self):
        """Test connection pool clearing."""
        # Create some connections
        with patch('django_concurrent_test.db.get_thread_safe_connection') as mock_get:
            mock_connection = MagicMock()
            mock_connection.settings_dict = {'NAME': 'test_db'}
            mock_get.return_value = mock_connection
            
            get_worker_connection(1, 'test_worker_1')
            get_worker_connection(2, 'test_worker_2')
        
        # Verify connections exist
        pool_stats = get_connection_pool_stats()
        self.assertEqual(pool_stats['total_connections'], 2)
        
        # Clear pool
        clear_connection_pool()
        
        # Verify pool is empty
        pool_stats = get_connection_pool_stats()
        self.assertEqual(pool_stats['total_connections'], 0)


class DatabaseIsolationTests(TestCase):
    """Test database isolation verification."""
    
    @patch('django_concurrent_test.db.connection')
    def test_verify_database_isolation_success(self, mock_connection):
        """Test successful database isolation verification."""
        # Mock worker connections
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        
        mock_cursor1 = MagicMock()
        mock_cursor1.fetchone.return_value = [1]  # First DB has data
        mock_conn1.cursor.return_value.__enter__.return_value = mock_cursor1
        
        mock_cursor2 = MagicMock()
        mock_cursor2.fetchone.return_value = [0]  # Second DB has no data
        mock_conn2.cursor.return_value.__enter__.return_value = mock_cursor2
        
        worker_connections = {
            0: ('test_worker_0', mock_conn1),
            1: ('test_worker_1', mock_conn2),
        }
        
        # Test isolation verification
        result = verify_database_isolation(worker_connections)
        
        self.assertTrue(result)
        
        # Verify test table was created and cleaned up
        mock_cursor1.execute.assert_any_call(
            "CREATE TABLE IF NOT EXISTS isolation_test"
        )
        mock_cursor1.execute.assert_any_call("DROP TABLE IF EXISTS isolation_test")
    
    @patch('django_concurrent_test.db.connection')
    def test_verify_database_isolation_failure(self, mock_connection):
        """Test database isolation verification failure."""
        # Mock worker connections with data leakage
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        
        mock_cursor1 = MagicMock()
        mock_cursor1.fetchone.return_value = [1]  # First DB has data
        mock_conn1.cursor.return_value.__enter__.return_value = mock_cursor1
        
        mock_cursor2 = MagicMock()
        mock_cursor2.fetchone.return_value = [1]  # Second DB also has data (leakage!)
        mock_conn2.cursor.return_value.__enter__.return_value = mock_cursor2
        
        worker_connections = {
            0: ('test_worker_0', mock_conn1),
            1: ('test_worker_1', mock_conn2),
        }
        
        # Test isolation verification should fail
        with self.assertRaises(DatabaseCloneException) as cm:
            verify_database_isolation(worker_connections)
        
        self.assertIn("Database isolation failed", str(cm.exception))
    
    def test_verify_database_isolation_single_db(self):
        """Test database isolation verification with single database."""
        # Single database should pass verification
        worker_connections = {0: ('test_worker_0', MagicMock())}
        
        result = verify_database_isolation(worker_connections)
        self.assertTrue(result)


class SetupTeardownTests(TestCase):
    """Test setup and teardown with new optimizations."""
    
    def setUp(self):
        """Set up test environment."""
        clear_connection_pool()
    
    def tearDown(self):
        """Clean up test environment."""
        clear_connection_pool()
    
    @patch('django_concurrent_test.db.get_database_cloner')
    @patch('django_concurrent_test.db.validate_database_permissions')
    def test_setup_test_databases_batch(self, mock_validate, mock_get_cloner):
        """Test setup test databases with batch operations."""
        # Mock cloner with batch support
        mock_cloner = MagicMock()
        mock_cloner.clone_databases_batch.return_value = ['test_worker_0', 'test_worker_1']
        mock_get_cloner.return_value = mock_cloner
        
        result = setup_test_databases(2)
        
        self.assertEqual(result, ['test_worker_0', 'test_worker_1'])
        mock_cloner.clone_databases_batch.assert_called_once_with([0, 1])
    
    @patch('django_concurrent_test.db.get_database_cloner')
    @patch('django_concurrent_test.db.validate_database_permissions')
    def test_setup_test_databases_individual(self, mock_validate, mock_get_cloner):
        """Test setup test databases with individual operations."""
        # Mock cloner without batch support
        mock_cloner = MagicMock()
        mock_cloner.clone_database.side_effect = ['test_worker_0', 'test_worker_1']
        mock_get_cloner.return_value = mock_cloner
        
        result = setup_test_databases(2)
        
        self.assertEqual(result, ['test_worker_0', 'test_worker_1'])
        self.assertEqual(mock_cloner.clone_database.call_count, 2)
    
    @patch('django_concurrent_test.db.setup_test_databases')
    @patch('django_concurrent_test.db.get_worker_connection')
    def test_setup_test_databases_with_connections(self, mock_get_connection, mock_setup):
        """Test setup test databases with connections."""
        # Mock setup databases
        mock_setup.return_value = ['test_worker_0', 'test_worker_1']
        
        # Mock worker connections
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        mock_get_connection.side_effect = [mock_conn1, mock_conn2]
        
        result = setup_test_databases_with_connections(2)
        
        expected = {
            0: ('test_worker_0', mock_conn1),
            1: ('test_worker_1', mock_conn2),
        }
        self.assertEqual(result, expected)
    
    @patch('django_concurrent_test.db.get_database_cloner')
    def test_teardown_test_databases_with_connections(self, mock_get_cloner):
        """Test teardown test databases with connections."""
        # Mock cloner
        mock_cloner = MagicMock()
        mock_get_cloner.return_value = mock_cloner
        
        # Mock worker connections
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        worker_connections = {
            0: ('test_worker_0', mock_conn1),
            1: ('test_worker_1', mock_conn2),
        }
        
        # Mock database existence
        mock_cloner.database_exists.return_value = True
        
        teardown_test_databases_with_connections(worker_connections)
        
        # Verify databases were dropped
        mock_cloner.drop_database.assert_any_call('test_worker_0')
        mock_cloner.drop_database.assert_any_call('test_worker_1')


class ContextManagerTests(TestCase):
    """Test context managers for worker databases."""
    
    @patch('django_concurrent_test.db.get_database_cloner')
    @patch('django_concurrent_test.db.get_worker_connection')
    def test_worker_database_context(self, mock_get_connection, mock_get_cloner):
        """Test worker database context manager."""
        # Mock cloner
        mock_cloner = MagicMock()
        mock_cloner.clone_database.return_value = 'test_worker_1'
        mock_cloner.database_exists.return_value = True
        mock_get_cloner.return_value = mock_cloner
        
        # Mock worker connection
        mock_connection = MagicMock()
        mock_get_connection.return_value = mock_connection
        
        mock_db_connection = MagicMock()
        
        with worker_database(1, mock_db_connection) as (db_name, conn):
            self.assertEqual(db_name, 'test_worker_1')
            self.assertEqual(conn, mock_connection)
        
        # Verify cleanup
        mock_cloner.drop_database.assert_called_with('test_worker_1')
    
    @patch('django_concurrent_test.db.worker_database')
    @patch('django_concurrent_test.db.wait_for_database_ready')
    def test_worker_database_with_isolation(self, mock_wait, mock_worker_db):
        """Test worker database with isolation context manager."""
        # Mock worker database context
        mock_worker_db.return_value.__enter__.return_value = ('test_worker_1', MagicMock())
        mock_worker_db.return_value.__exit__.return_value = None
        
        # Mock database ready
        mock_wait.return_value = True
        
        mock_db_connection = MagicMock()
        
        with worker_database_with_isolation(1, mock_db_connection) as (db_name, conn):
            self.assertEqual(db_name, 'test_worker_1')
        
        # Verify database ready check
        mock_wait.assert_called_with('test_worker_1')


if __name__ == '__main__':
    unittest.main() 