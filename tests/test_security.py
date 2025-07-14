"""
Security tests for django-concurrent-test package.
"""

import os
import unittest
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.db import connection

from django_concurrent_test.exceptions import (
    SecurityException,
    UnsupportedDatabase,
    PermissionException,
)
from django_concurrent_test.security import (
    validate_environment,
    validate_database_configuration,
    is_production_database,
    is_supported_database,
    validate_database_permissions,
    sanitize_database_name,
    get_safe_worker_database_name,
    check_telemetry_disabled,
    validate_worker_count,
    get_safe_worker_count,
)


class SecurityValidationTests(TestCase):
    """Test security validation functions."""
    
    def setUp(self):
        """Set up test environment."""
        # Clear environment variables
        for key in ['DJANGO_ENABLE_CONCURRENT', 'NO_TELEMETRY']:
            if key in os.environ:
                del os.environ[key]
    
    @patch('django_concurrent_test.security.settings')
    def test_validate_environment_disabled(self, mock_settings):
        """Test environment validation when concurrent testing is disabled."""
        mock_settings.DEBUG = True
        
        with self.assertRaises(SecurityException) as cm:
            validate_environment()
        
        self.assertIn("Concurrent testing is disabled", str(cm.exception))
    
    @patch('django_concurrent_test.security.settings')
    def test_validate_environment_debug_false(self, mock_settings):
        """Test environment validation when DEBUG is False."""
        os.environ['DJANGO_ENABLE_CONCURRENT'] = 'True'
        os.environ['NO_TELEMETRY'] = '1'
        mock_settings.DEBUG = False
        
        with self.assertRaises(SecurityException) as cm:
            validate_environment()
        
        self.assertIn("DEBUG=True", str(cm.exception))
    
    @patch('django_concurrent_test.security.settings')
    def test_validate_environment_telemetry_enabled(self, mock_settings):
        """Test environment validation when telemetry is enabled."""
        os.environ['DJANGO_ENABLE_CONCURRENT'] = 'True'
        os.environ['NO_TELEMETRY'] = '0'
        mock_settings.DEBUG = True
        
        with self.assertRaises(SecurityException) as cm:
            validate_environment()
        
        self.assertIn("Telemetry must be disabled", str(cm.exception))
    
    @patch('django_concurrent_test.security.settings')
    @patch('django_concurrent_test.security.connection')
    def test_validate_environment_success(self, mock_connection, mock_settings):
        """Test successful environment validation."""
        os.environ['DJANGO_ENABLE_CONCURRENT'] = 'True'
        os.environ['NO_TELEMETRY'] = '1'
        mock_settings.DEBUG = True
        mock_settings.DATABASES = {
            'default': {'NAME': 'test_main'}
        }
        mock_connection.vendor = 'postgresql'
        
        # Should not raise any exception
        validate_environment()
    
    @patch('django_concurrent_test.security.connection')
    def test_validate_database_configuration_production_name(self, mock_connection):
        """Test database configuration validation with production name."""
        mock_connection.vendor = 'postgresql'
        
        with patch('django_concurrent_test.security.settings') as mock_settings:
            mock_settings.DATABASES = {
                'default': {'NAME': 'prod_main'}
            }
            
            with self.assertRaises(SecurityException) as cm:
                validate_database_configuration()
            
            self.assertIn("Production-like database name", str(cm.exception))
    
    @patch('django_concurrent_test.security.connection')
    def test_validate_database_configuration_unsupported_db(self, mock_connection):
        """Test database configuration validation with unsupported database."""
        mock_connection.vendor = 'sqlite'
        
        with patch('django_concurrent_test.security.settings') as mock_settings:
            mock_settings.DATABASES = {
                'default': {'NAME': 'test_main'}
            }
            
            with self.assertRaises(UnsupportedDatabase) as cm:
                validate_database_configuration()
            
            self.assertIn("not supported", str(cm.exception))


class DatabaseNameValidationTests(TestCase):
    """Test database name validation functions."""
    
    def test_is_production_database_prod_prefix(self):
        """Test production database detection with prod_ prefix."""
        self.assertTrue(is_production_database('prod_main'))
        self.assertTrue(is_production_database('prod_myapp'))
    
    def test_is_production_database_production_name(self):
        """Test production database detection with production name."""
        self.assertTrue(is_production_database('production'))
        self.assertTrue(is_production_database('production_db'))
    
    def test_is_production_database_live_name(self):
        """Test production database detection with live name."""
        self.assertTrue(is_production_database('live_main'))
        self.assertTrue(is_production_database('myapp_live'))
    
    def test_is_production_database_staging_name(self):
        """Test production database detection with staging name."""
        self.assertTrue(is_production_database('staging_main'))
        self.assertTrue(is_production_database('myapp_staging'))
    
    def test_is_production_database_test_name(self):
        """Test production database detection with test name."""
        self.assertFalse(is_production_database('test_main'))
        self.assertFalse(is_production_database('dev_test'))
        self.assertFalse(is_production_database('ci_test'))
        self.assertFalse(is_production_database('myapp_test'))
        self.assertFalse(is_production_database('myapp_testing'))
    
    def test_is_production_database_empty_name(self):
        """Test production database detection with empty name."""
        self.assertFalse(is_production_database(''))
        self.assertFalse(is_production_database(None))
    
    def test_is_supported_database_postgresql(self):
        """Test supported database detection for PostgreSQL."""
        with patch('django_concurrent_test.security.connection') as mock_connection:
            mock_connection.vendor = 'postgresql'
            self.assertTrue(is_supported_database())
    
    def test_is_supported_database_mysql(self):
        """Test supported database detection for MySQL."""
        with patch('django_concurrent_test.security.connection') as mock_connection:
            mock_connection.vendor = 'mysql'
            self.assertTrue(is_supported_database())
    
    def test_is_supported_database_sqlite(self):
        """Test supported database detection for SQLite."""
        with patch('django_concurrent_test.security.connection') as mock_connection:
            mock_connection.vendor = 'sqlite'
            self.assertFalse(is_supported_database())


class DatabasePermissionTests(TestCase):
    """Test database permission validation."""
    
    @patch('django_concurrent_test.security.connection')
    def test_validate_database_permissions_success(self, mock_connection):
        """Test successful database permission validation."""
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Should not raise any exception
        validate_database_permissions()
    
    @patch('django_concurrent_test.security.connection')
    def test_validate_database_permissions_failure(self, mock_connection):
        """Test database permission validation failure."""
        mock_connection.cursor.side_effect = Exception("Permission denied")
        
        with self.assertRaises(PermissionException) as cm:
            validate_database_permissions()
        
        self.assertIn("Insufficient database permissions", str(cm.exception))


class DatabaseNameSanitizationTests(TestCase):
    """Test database name sanitization."""
    
    def test_sanitize_database_name_valid(self):
        """Test sanitization of valid database names."""
        self.assertEqual(sanitize_database_name('test_main'), 'test_main')
        self.assertEqual(sanitize_database_name('test123'), 'test123')
        self.assertEqual(sanitize_database_name('test_main_db'), 'test_main_db')
    
    def test_sanitize_database_name_empty(self):
        """Test sanitization of empty database name."""
        with self.assertRaises(SecurityException) as cm:
            sanitize_database_name('')
        
        self.assertIn("cannot be empty", str(cm.exception))
    
    def test_sanitize_database_name_dangerous_chars(self):
        """Test sanitization of database name with dangerous characters."""
        with self.assertRaises(SecurityException) as cm:
            sanitize_database_name('test-main')
        
        self.assertIn("unsafe characters", str(cm.exception))
    
    def test_get_safe_worker_database_name_valid(self):
        """Test generation of safe worker database name."""
        result = get_safe_worker_database_name('test_main', 1)
        self.assertEqual(result, 'test_main_worker_1')
    
    def test_get_safe_worker_database_name_invalid_prefix(self):
        """Test generation of worker database name with invalid prefix."""
        with self.assertRaises(SecurityException) as cm:
            get_safe_worker_database_name('main', 1)
        
        self.assertIn("must start with 'test_'", str(cm.exception))


class TelemetryTests(TestCase):
    """Test telemetry validation."""
    
    def setUp(self):
        """Set up test environment."""
        if 'NO_TELEMETRY' in os.environ:
            del os.environ['NO_TELEMETRY']
    
    def test_check_telemetry_disabled_enabled(self):
        """Test telemetry check when telemetry is enabled."""
        os.environ['NO_TELEMETRY'] = '0'
        
        with self.assertRaises(SecurityException) as cm:
            check_telemetry_disabled()
        
        self.assertIn("Telemetry must be disabled", str(cm.exception))
    
    def test_check_telemetry_disabled_disabled(self):
        """Test telemetry check when telemetry is disabled."""
        os.environ['NO_TELEMETRY'] = '1'
        
        # Should not raise any exception
        check_telemetry_disabled()


class WorkerCountTests(TestCase):
    """Test worker count validation and generation."""
    
    def test_validate_worker_count_valid(self):
        """Test validation of valid worker counts."""
        # Should not raise any exception
        validate_worker_count(1)
        validate_worker_count(4)
        validate_worker_count(8)
    
    def test_validate_worker_count_invalid(self):
        """Test validation of invalid worker counts."""
        with self.assertRaises(SecurityException) as cm:
            validate_worker_count(0)
        
        self.assertIn("must be positive", str(cm.exception))
        
        with self.assertRaises(SecurityException) as cm:
            validate_worker_count(-1)
        
        self.assertIn("must be positive", str(cm.exception))
        
        with self.assertRaises(SecurityException) as cm:
            validate_worker_count(20)
        
        self.assertIn("cannot exceed 16", str(cm.exception))
    
    @patch('os.cpu_count')
    def test_get_safe_worker_count_auto(self, mock_cpu_count):
        """Test automatic worker count generation."""
        mock_cpu_count.return_value = 4
        
        result = get_safe_worker_count()
        self.assertEqual(result, 4)
    
    @patch('os.cpu_count')
    def test_get_safe_worker_count_environment_override(self, mock_cpu_count):
        """Test worker count override via environment variable."""
        mock_cpu_count.return_value = 4
        os.environ['DJANGO_TEST_WORKERS'] = '6'
        
        result = get_safe_worker_count()
        self.assertEqual(result, 6)
        
        # Clean up
        del os.environ['DJANGO_TEST_WORKERS']
    
    @patch('os.cpu_count')
    def test_get_safe_worker_count_environment_invalid(self, mock_cpu_count):
        """Test worker count with invalid environment variable."""
        mock_cpu_count.return_value = 4
        os.environ['DJANGO_TEST_WORKERS'] = 'invalid'
        
        result = get_safe_worker_count()
        self.assertEqual(result, 4)  # Should fall back to CPU count
        
        # Clean up
        del os.environ['DJANGO_TEST_WORKERS']
    
    @patch('os.cpu_count')
    def test_get_safe_worker_count_limit(self, mock_cpu_count):
        """Test worker count limiting."""
        mock_cpu_count.return_value = 16
        
        result = get_safe_worker_count()
        self.assertEqual(result, 8)  # Should be limited to 8


if __name__ == '__main__':
    unittest.main() 