"""
Database tests for django-concurrent-test package.
"""

import unittest
from unittest.mock import patch, MagicMock, call
from django.test import TestCase, override_settings

from django_concurrent_test.exceptions import (
    DatabaseCloneException,
    UnsupportedDatabase,
    PermissionException,
)
from django_concurrent_test.db import (
    DatabaseCloner,
    PostgreSQLCloner,
    MySQLCloner,
    get_database_cloner,
    worker_database,
    setup_test_databases,
    teardown_test_databases,
    wait_for_database_ready,
)


class DatabaseClonerBaseTests(TestCase):
    """Test base DatabaseCloner class."""
    
    def test_database_cloner_abstract_methods(self):
        """Test that DatabaseCloner is abstract."""
        cloner = DatabaseCloner(MagicMock())
        
        with self.assertRaises(NotImplementedError):
            cloner.clone_database(1)
        
        with self.assertRaises(NotImplementedError):
            cloner.drop_database('test_db')
        
        with self.assertRaises(NotImplementedError):
            cloner.database_exists('test_db')


class PostgreSQLClonerTests(TestCase):
    """Test PostgreSQL database cloning."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_connection = MagicMock()
        self.mock_connection.vendor = 'postgresql'
        self.mock_connection.settings_dict = {
            'NAME': 'test_main',
            'USER': 'test_user',
        }
        self.cloner = PostgreSQLCloner(self.mock_connection)
    
    @patch('django_concurrent_test.db.get_safe_worker_database_name')
    def test_clone_database_success(self, mock_get_name):
        """Test successful PostgreSQL database cloning."""
        mock_get_name.return_value = 'test_main_worker_1'
        
        mock_cursor = MagicMock()
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database exists check
        self.cloner.database_exists = MagicMock(return_value=False)
        
        result = self.cloner.clone_database(1)
        
        self.assertEqual(result, 'test_main_worker_1')
        mock_cursor.execute.assert_called_with(
            "CREATE DATABASE test_main_worker_1 "
            "TEMPLATE test_main WITH OWNER test_user NO DATA"
        )
    
    @patch('django_concurrent_test.db.get_safe_worker_database_name')
    def test_clone_database_exists(self, mock_get_name):
        """Test PostgreSQL database cloning when database already exists."""
        mock_get_name.return_value = 'test_main_worker_1'
        
        mock_cursor = MagicMock()
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database exists check
        self.cloner.database_exists = MagicMock(return_value=True)
        self.cloner.drop_database = MagicMock()
        
        result = self.cloner.clone_database(1)
        
        self.assertEqual(result, 'test_main_worker_1')
        self.cloner.drop_database.assert_called_with('test_main_worker_1')
    
    @patch('django_concurrent_test.db.get_safe_worker_database_name')
    def test_clone_database_failure(self, mock_get_name):
        """Test PostgreSQL database cloning failure."""
        mock_get_name.return_value = 'test_main_worker_1'
        
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Database error")
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        self.cloner.database_exists = MagicMock(return_value=False)
        
        with self.assertRaises(DatabaseCloneException) as cm:
            self.cloner.clone_database(1)
        
        self.assertIn("Failed to clone PostgreSQL database", str(cm.exception))
    
    def test_drop_database_success(self):
        """Test successful PostgreSQL database dropping."""
        mock_cursor = MagicMock()
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        self.cloner.drop_database('test_db')
        
        # Check that terminate and drop commands were executed
        calls = mock_cursor.execute.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("pg_terminate_backend", str(calls[0]))
        self.assertIn("DROP DATABASE IF EXISTS test_db", str(calls[1]))
    
    def test_drop_database_failure(self):
        """Test PostgreSQL database dropping failure."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Drop error")
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        with self.assertRaises(DatabaseCloneException) as cm:
            self.cloner.drop_database('test_db')
        
        self.assertIn("Failed to drop PostgreSQL database", str(cm.exception))
    
    def test_database_exists_true(self):
        """Test PostgreSQL database existence check - exists."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1]
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = self.cloner.database_exists('test_db')
        
        self.assertTrue(result)
        mock_cursor.execute.assert_called_with(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            ['test_db']
        )
    
    def test_database_exists_false(self):
        """Test PostgreSQL database existence check - doesn't exist."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = self.cloner.database_exists('test_db')
        
        self.assertFalse(result)
    
    def test_database_exists_exception(self):
        """Test PostgreSQL database existence check with exception."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Query error")
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = self.cloner.database_exists('test_db')
        
        self.assertFalse(result)


class MySQLClonerTests(TestCase):
    """Test MySQL database cloning."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_connection = MagicMock()
        self.mock_connection.vendor = 'mysql'
        self.mock_connection.settings_dict = {
            'NAME': 'test_main',
        }
        self.cloner = MySQLCloner(self.mock_connection)
    
    @patch('django_concurrent_test.db.get_safe_worker_database_name')
    def test_clone_database_success(self, mock_get_name):
        """Test successful MySQL database cloning."""
        mock_get_name.return_value = 'test_main_worker_1'
        
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [['table1'], ['table2']]
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database exists check
        self.cloner.database_exists = MagicMock(return_value=False)
        
        result = self.cloner.clone_database(1)
        
        self.assertEqual(result, 'test_main_worker_1')
        
        # Check that create database and table commands were executed
        calls = mock_cursor.execute.call_args_list
        self.assertIn("CREATE DATABASE test_main_worker_1", str(calls[0]))
        self.assertIn("CREATE TABLE test_main_worker_1.table1", str(calls[1]))
        self.assertIn("CREATE TABLE test_main_worker_1.table2", str(calls[2]))
    
    @patch('django_concurrent_test.db.get_safe_worker_database_name')
    def test_clone_database_exists(self, mock_get_name):
        """Test MySQL database cloning when database already exists."""
        mock_get_name.return_value = 'test_main_worker_1'
        
        mock_cursor = MagicMock()
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock database exists check
        self.cloner.database_exists = MagicMock(return_value=True)
        self.cloner.drop_database = MagicMock()
        
        result = self.cloner.clone_database(1)
        
        self.assertEqual(result, 'test_main_worker_1')
        self.cloner.drop_database.assert_called_with('test_main_worker_1')
    
    @patch('django_concurrent_test.db.get_safe_worker_database_name')
    def test_clone_database_failure(self, mock_get_name):
        """Test MySQL database cloning failure."""
        mock_get_name.return_value = 'test_main_worker_1'
        
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Database error")
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        self.cloner.database_exists = MagicMock(return_value=False)
        
        with self.assertRaises(DatabaseCloneException) as cm:
            self.cloner.clone_database(1)
        
        self.assertIn("Failed to clone MySQL database", str(cm.exception))
    
    def test_drop_database_success(self):
        """Test successful MySQL database dropping."""
        mock_cursor = MagicMock()
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        self.cloner.drop_database('test_db')
        
        mock_cursor.execute.assert_called_with("DROP DATABASE IF EXISTS test_db")
    
    def test_drop_database_failure(self):
        """Test MySQL database dropping failure."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Drop error")
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        with self.assertRaises(DatabaseCloneException) as cm:
            self.cloner.drop_database('test_db')
        
        self.assertIn("Failed to drop MySQL database", str(cm.exception))
    
    def test_database_exists_true(self):
        """Test MySQL database existence check - exists."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1]
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = self.cloner.database_exists('test_db')
        
        self.assertTrue(result)
        mock_cursor.execute.assert_called_with(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            ['test_db']
        )
    
    def test_database_exists_false(self):
        """Test MySQL database existence check - doesn't exist."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = self.cloner.database_exists('test_db')
        
        self.assertFalse(result)
    
    def test_database_exists_exception(self):
        """Test MySQL database existence check with exception."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Query error")
        self.mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = self.cloner.database_exists('test_db')
        
        self.assertFalse(result)


class DatabaseClonerFactoryTests(TestCase):
    """Test database cloner factory function."""
    
    def test_get_database_cloner_postgresql(self):
        """Test getting PostgreSQL cloner."""
        mock_connection = MagicMock()
        mock_connection.vendor = 'postgresql'
        
        cloner = get_database_cloner(mock_connection)
        
        self.assertIsInstance(cloner, PostgreSQLCloner)
        self.assertEqual(cloner.vendor, 'postgresql')
    
    def test_get_database_cloner_mysql(self):
        """Test getting MySQL cloner."""
        mock_connection = MagicMock()
        mock_connection.vendor = 'mysql'
        
        cloner = get_database_cloner(mock_connection)
        
        self.assertIsInstance(cloner, MySQLCloner)
        self.assertEqual(cloner.vendor, 'mysql')
    
    def test_get_database_cloner_unsupported(self):
        """Test getting cloner for unsupported database."""
        mock_connection = MagicMock()
        mock_connection.vendor = 'sqlite'
        
        with self.assertRaises(UnsupportedDatabase) as cm:
            get_database_cloner(mock_connection)
        
        self.assertIn("not supported", str(cm.exception))


class WorkerDatabaseContextTests(TestCase):
    """Test worker database context manager."""
    
    @patch('django_concurrent_test.db.get_database_cloner')
    def test_worker_database_success(self, mock_get_cloner):
        """Test successful worker database context."""
        mock_cloner = MagicMock()
        mock_cloner.clone_database.return_value = 'test_worker_1'
        mock_cloner.database_exists.return_value = True
        mock_get_cloner.return_value = mock_cloner
        
        mock_connection = MagicMock()
        
        with worker_database(1, mock_connection) as db_name:
            self.assertEqual(db_name, 'test_worker_1')
        
        # Check that database was cloned and dropped
        mock_cloner.clone_database.assert_called_with(1)
        mock_cloner.drop_database.assert_called_with('test_worker_1')
    
    @patch('django_concurrent_test.db.get_database_cloner')
    def test_worker_database_clone_failure(self, mock_get_cloner):
        """Test worker database context with clone failure."""
        mock_cloner = MagicMock()
        mock_cloner.clone_database.side_effect = DatabaseCloneException("Clone failed")
        mock_get_cloner.return_value = mock_cloner
        
        mock_connection = MagicMock()
        
        with self.assertRaises(DatabaseCloneException):
            with worker_database(1, mock_connection):
                pass
        
        # Check that drop was not called
        mock_cloner.drop_database.assert_not_called()
    
    @patch('django_concurrent_test.db.get_database_cloner')
    def test_worker_database_drop_failure(self, mock_get_cloner):
        """Test worker database context with drop failure."""
        mock_cloner = MagicMock()
        mock_cloner.clone_database.return_value = 'test_worker_1'
        mock_cloner.database_exists.return_value = True
        mock_cloner.drop_database.side_effect = Exception("Drop failed")
        mock_get_cloner.return_value = mock_cloner
        
        mock_connection = MagicMock()
        
        # Should not raise exception, just log warning
        with worker_database(1, mock_connection) as db_name:
            self.assertEqual(db_name, 'test_worker_1')


class DatabaseSetupTeardownTests(TestCase):
    """Test database setup and teardown functions."""
    
    @patch('django_concurrent_test.db.setup_test_databases')
    @patch('django_concurrent_test.db.validate_database_permissions')
    def test_setup_test_databases_success(self, mock_validate, mock_setup):
        """Test successful test database setup."""
        mock_setup.return_value = ['test_worker_0', 'test_worker_1']
        
        result = setup_test_databases(2)
        
        self.assertEqual(result, ['test_worker_0', 'test_worker_1'])
        mock_validate.assert_called_once()
        mock_setup.assert_called_with(2)
    
    @patch('django_concurrent_test.db.setup_test_databases')
    @patch('django_concurrent_test.db.validate_database_permissions')
    def test_setup_test_databases_failure(self, mock_validate, mock_setup):
        """Test test database setup failure."""
        mock_setup.side_effect = Exception("Setup failed")
        
        with self.assertRaises(DatabaseCloneException) as cm:
            setup_test_databases(2)
        
        self.assertIn("Failed to setup test databases", str(cm.exception))
    
    @patch('django_concurrent_test.db.teardown_test_databases')
    def test_teardown_test_databases_success(self, mock_teardown):
        """Test successful test database teardown."""
        database_names = ['test_worker_0', 'test_worker_1']
        
        teardown_test_databases(database_names)
        
        mock_teardown.assert_called_with(database_names)
    
    @patch('django_concurrent_test.db.teardown_test_databases')
    def test_teardown_test_databases_failure(self, mock_teardown):
        """Test test database teardown with failure."""
        mock_teardown.side_effect = Exception("Teardown failed")
        
        # Should not raise exception, just log warning
        teardown_test_databases(['test_worker_0'])


class DatabaseReadyTests(TestCase):
    """Test database ready checking."""
    
    @patch('django_concurrent_test.db.get_database_cloner')
    def test_wait_for_database_ready_success(self, mock_get_cloner):
        """Test successful database ready check."""
        mock_cloner = MagicMock()
        mock_cloner.database_exists.return_value = True
        mock_get_cloner.return_value = mock_cloner
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        result = wait_for_database_ready('test_db', timeout=1)
        
        self.assertTrue(result)
        mock_cloner.database_exists.assert_called_with('test_db')
        mock_cursor.execute.assert_called_with("SELECT 1")
    
    @patch('django_concurrent_test.db.get_database_cloner')
    def test_wait_for_database_ready_timeout(self, mock_get_cloner):
        """Test database ready check timeout."""
        mock_cloner = MagicMock()
        mock_cloner.database_exists.return_value = False
        mock_get_cloner.return_value = mock_cloner
        
        mock_connection = MagicMock()
        
        with self.assertRaises(DatabaseCloneException) as cm:
            wait_for_database_ready('test_db', timeout=0.1)
        
        self.assertIn("not ready within", str(cm.exception))
    
    @patch('django_concurrent_test.db.get_database_cloner')
    def test_wait_for_database_ready_connection_failure(self, mock_get_cloner):
        """Test database ready check with connection failure."""
        mock_cloner = MagicMock()
        mock_cloner.database_exists.return_value = True
        mock_get_cloner.return_value = mock_cloner
        
        mock_connection = MagicMock()
        mock_connection.cursor.side_effect = Exception("Connection failed")
        
        with self.assertRaises(DatabaseCloneException) as cm:
            wait_for_database_ready('test_db', timeout=0.1)
        
        self.assertIn("not ready within", str(cm.exception))


if __name__ == '__main__':
    unittest.main() 