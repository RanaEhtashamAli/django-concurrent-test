"""
Basic usage example for django-concurrent-test package.
"""

import os
import django
from django.conf import settings
from django.test import TestCase

# Configure Django settings for testing
if not settings.configured:
    settings.configure(
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': 'test_main',
                'USER': 'test_user',
                'PASSWORD': 'test_pass',
                'HOST': 'localhost',
                'PORT': '5432',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.auth',
            'django.contrib.contenttypes',
            'django.contrib.sessions',
        ],
        SECRET_KEY='test-secret-key',
        DEBUG=True,
    )
    django.setup()

# Enable concurrent testing
os.environ['DJANGO_ENABLE_CONCURRENT'] = 'True'
os.environ['NO_TELEMETRY'] = '1'

from django_concurrent_test.runner import ConcurrentTestRunner


class ExampleTestCase(TestCase):
    """Example test case for demonstration."""
    
    def test_basic_functionality(self):
        """Test basic functionality."""
        self.assertTrue(True)
        self.assertEqual(1 + 1, 2)
    
    def test_database_operation(self):
        """Test database operation."""
        from django.contrib.auth.models import User
        
        # Create a test user
        user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Verify user was created
        self.assertEqual(user.username, 'testuser')
        self.assertEqual(user.email, 'test@example.com')
        
        # Check user exists in database
        user_from_db = User.objects.get(username='testuser')
        self.assertEqual(user_from_db.id, user.id)


def run_concurrent_tests():
    """Run tests using the concurrent test runner."""
    print("Running tests with ConcurrentTestRunner...")
    
    # Create test runner instance
    runner = ConcurrentTestRunner(
        benchmark=True,
        junitxml='test-results.xml'
    )
    
    # Run tests
    failures = runner.run_tests(['examples.basic_usage'])
    
    print(f"Tests completed with {failures} failures")
    return failures


if __name__ == '__main__':
    run_concurrent_tests() 