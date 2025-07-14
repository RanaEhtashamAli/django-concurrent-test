# Django Concurrent Test

![PyPI version](https://img.shields.io/pypi/v/django-concurrent-test)
![License](https://img.shields.io/github/license/RanaEhtashamAli/django-concurrent-test)
![Build Status](https://img.shields.io/github/actions/workflow/status/RanaEhtashamAli/django-concurrent-test/tests.yml)
![Python versions](https://img.shields.io/pypi/pyversions/django-concurrent-test)
![Django versions](https://img.shields.io/pypi/djversions/django-concurrent-test)

A production-ready Django package for safe and configurable concurrent testing with isolated databases, timing analytics, and concurrency simulation middleware.

## 🚀 Features

- **🔒 Secure Database Templating**: Zero-config parallel testing with isolated database instances
- **⚡ Concurrent Test Execution**: ThreadPoolExecutor and asyncio-based concurrency
- **📊 Timing Analytics**: Comprehensive test timing analysis and benchmarking
- **🛡️ Concurrency Safety**: Middleware for detecting race conditions and state mutations
- **🔧 Runtime Configuration**: Dynamic worker scaling and timeout management
- **📈 Performance Monitoring**: Connection pooling, resource monitoring, and metrics
- **🎯 DRF Integration**: Optional Django REST Framework compatibility
- **📋 JUnit XML Output**: CI/CD friendly test reporting
- **🔍 Telemetry-Free**: No data collection or external dependencies

## 📦 Installation

```bash
pip install django-concurrent-test
```

## 🏃‍♂️ Quick Start

### Basic Usage

```python
# settings.py
INSTALLED_APPS = [
    # ... your apps
    'django_concurrent_test',
]

# Enable concurrent testing
DJANGO_ENABLE_CONCURRENT = True
```

### Command Line Usage

```bash
# Run tests with concurrent execution
pytest --concurrent

# Specify number of workers
pytest --concurrent --workers 4

# Set timeouts
pytest --concurrent --timeout 300 --test-timeout 60

# Export timing data
pytest --concurrent --export-timings results.json

# Import previous timings and export to CSV
pytest --concurrent --import-timings results.json --export-timings-csv results.csv
```

## 🔧 Configuration

### Environment Variables

```bash
# Enable concurrent testing
export DJANGO_ENABLE_CONCURRENT=True

# Configure workers (auto-detected if not set)
export DJANGO_TEST_WORKERS=4

# Set timeouts
export DJANGO_TEST_TIMEOUT=300
export DJANGO_TEST_BENCHMARK=True
```

### Django Settings

```python
# settings.py
CONCURRENT_TEST = {
    'ENABLED': True,
    'WORKERS': 4,  # Auto-detected if not set
    'TIMEOUT': 300,
    'TEST_TIMEOUT': 60,
    'WORKER_TIMEOUT': 120,
    'BENCHMARK': True,
    'EXPORT_TIMINGS': 'test_timings.json',
}
```

## 🛡️ Concurrency Safety

### Using `assert_concurrent_safety`

Test your functions for concurrent execution safety:

```python
from django_concurrent_test.middleware import assert_concurrent_safety

def test_user_creation():
    """Test that user creation is safe for concurrent execution."""
    
    def create_user():
        from django.contrib.auth.models import User
        return User.objects.create_user(
            username=f'user_{time.time()}',
            email='test@example.com'
        )
    
    # This will run the function concurrently and check for race conditions
    assert_concurrent_safety(create_user, max_workers=4, iterations=10)
```

### Using `simulate_concurrent_requests`

Simulate concurrent request scenarios:

```python
from django_concurrent_test.middleware import simulate_concurrent_requests
from django.test import RequestFactory

def test_api_endpoint_concurrency():
    """Test API endpoint under concurrent load."""
    
    factory = RequestFactory()
    
    def make_request():
        request = factory.get('/api/users/')
        response = your_view_function(request)
        return response.status_code
    
    # Simulate 10 concurrent requests
    results = simulate_concurrent_requests(make_request, num_requests=10)
    
    # Check results
    successful = [r for r in results if r['status'] == 'success']
    assert len(successful) == 10
```

## 🔌 Middleware Integration

### Auto-Registration

The middleware can be auto-registered during pytest sessions:

```python
# conftest.py
import pytest
from django_concurrent_test.middleware import auto_register_middleware

@pytest.fixture(scope='session', autouse=True)
def setup_concurrent_middleware():
    """Auto-register concurrent testing middleware."""
    added_middleware = auto_register_middleware()
    if added_middleware:
        print(f"Auto-registered middleware: {added_middleware}")
```

### Manual Configuration

Add middleware to your Django settings:

```python
# settings.py
MIDDLEWARE = [
    # ... existing middleware
    'django_concurrent_test.middleware.ConcurrentSafetyMiddleware',
    'django_concurrent_test.middleware.StateMutationMiddleware',
    'django_concurrent_test.middleware.ConcurrencySimulationMiddleware',
]
```

### Runtime Configuration

Configure middleware behavior at runtime:

```python
from django_concurrent_test.middleware import (
    set_test_override, 
    concurrent_test_context
)

# Adjust middleware behavior
set_test_override('delay_range', (0.2, 0.8))
set_test_override('probability', 0.5)

# Use context manager for temporary changes
with concurrent_test_context():
    # All middleware uses testing configuration
    run_tests()
```

## 🍰 DRF Integration

### Testing DRF Viewsets

```python
from django_concurrent_test.middleware import assert_concurrent_safety
from rest_framework.test import APITestCase
from rest_framework import status

class UserViewSetTest(APITestCase):
    def test_concurrent_user_creation(self):
        """Test concurrent user creation via DRF."""
        
        def create_user_via_api():
            data = {
                'username': f'user_{time.time()}',
                'email': 'test@example.com',
                'password': 'testpass123'
            }
            response = self.client.post('/api/users/', data)
            return response.status_code
        
        # Test concurrent API calls
        assert_concurrent_safety(create_user_via_api, max_workers=4, iterations=5)
```

### Testing DRF Serializers

```python
from django_concurrent_test.middleware import assert_concurrent_safety
from rest_framework import serializers

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['username', 'email']

def test_serializer_concurrency():
    """Test serializer validation under concurrent load."""
    
    def validate_user_data():
        data = {
            'username': f'user_{time.time()}',
            'email': 'test@example.com'
        }
        serializer = UserSerializer(data=data)
        return serializer.is_valid()
    
    assert_concurrent_safety(validate_user_data, max_workers=4, iterations=10)
```

## 📊 Timing Analytics

### Export and Import Timing Data

```python
from django_concurrent_test.timing_utils import (
    load_timings, 
    save_timings, 
    filter_timings,
    export_timings_to_csv
)

# Load timing data
timings = load_timings('test_timings.json')

# Filter slow tests
slow_tests = filter_timings(timings, min_duration=5.0)

# Export to CSV
export_timings_to_csv(slow_tests, 'slow_tests.csv')

# Analyze timing data
for test_name, timing_data in slow_tests.items():
    print(f"{test_name}: {timing_data['duration']:.2f}s")
```

### Benchmark Analysis

```python
from django_concurrent_test.timing_utils import get_slowest_tests, get_fastest_tests

# Get performance insights
slowest = get_slowest_tests(timings, count=5)
fastest = get_fastest_tests(timings, count=5)

print("Slowest tests:")
for test_name, duration in slowest:
    print(f"  {test_name}: {duration:.2f}s")

print("Fastest tests:")
for test_name, duration in fastest:
    print(f"  {test_name}: {duration:.2f}s")
```

## 🔒 Security Features

### Environment Validation

```python
from django_concurrent_test.security import (
    security_context, 
    get_safe_worker_count,
    validate_environment
)

# Validate environment before testing
with security_context():
    worker_count = get_safe_worker_count()
    print(f"Safe worker count: {worker_count}")

# Manual validation
validate_environment()
```

### Resource Monitoring

```python
from django_concurrent_test.security import check_system_resources

# Check system resources
resources = check_system_resources()
print(f"Available memory: {resources['memory_available_gb']:.1f}GB")
print(f"CPU cores: {resources['cpu_count']}")
print(f"Safe worker count: {resources['safe_worker_count']}")
```

## 🧪 Advanced Testing

### Custom Test Runner

```python
from django_concurrent_test.runner import ConcurrentTestRunner

class CustomConcurrentRunner(ConcurrentTestRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_metrics = {}
    
    def run_tests(self, test_labels, **kwargs):
        # Custom pre-test setup
        self.setup_custom_environment()
        
        # Run tests with concurrent execution
        failures = super().run_tests(test_labels, **kwargs)
        
        # Custom post-test cleanup
        self.cleanup_custom_environment()
        
        return failures
```

### Database Isolation Testing

```python
from django_concurrent_test.db import verify_database_isolation

def test_database_isolation():
    """Verify that worker databases are properly isolated."""
    
    # Run concurrent tests
    runner = ConcurrentTestRunner()
    failures = runner.run_tests(['myapp.tests'])
    
    # Verify isolation
    worker_connections = runner.get_worker_connections()
    isolation_verified = verify_database_isolation(worker_connections)
    
    assert isolation_verified, "Database isolation verification failed"
```

## 📈 Performance Monitoring

### Connection Pool Statistics

```python
from django_concurrent_test.db import get_connection_pool_stats

# Get connection pool metrics
stats = get_connection_pool_stats()
print(f"Active connections: {stats.get('active', 0)}")
print(f"Pooled connections: {stats.get('pooled', 0)}")
print(f"Connection hits: {stats.get('hits', 0)}")
print(f"Connection misses: {stats.get('misses', 0)}")
```

### Memory-Based Scaling

The package automatically scales worker count based on available memory:

```python
from django_concurrent_test.runner import ConcurrentTestRunner

# Memory-based scaling is automatic
runner = ConcurrentTestRunner()
# Worker count will be calculated based on available memory
```

## 🚨 Error Handling

### Timeout Management

```python
from django_concurrent_test.exceptions import TestTimeoutException

def test_with_timeout():
    """Test with custom timeout handling."""
    try:
        # Run test with timeout
        result = run_test_with_timeout(test_function, timeout=30)
        assert result is not None
    except TestTimeoutException:
        pytest.fail("Test timed out")
```

### Database Error Recovery

```python
from django_concurrent_test.db import wait_for_database_ready

def test_database_recovery():
    """Test database connection recovery."""
    
    # Wait for database to be ready
    ready = wait_for_database_ready('test_db', timeout=30)
    assert ready, "Database not ready within timeout"
```

## 🔧 Development

### Running Tests

```bash
# Run package tests
pytest tests/

# Run with coverage
pytest --cov=django_concurrent_test tests/

# Run with concurrent execution
pytest --concurrent tests/
```

### Building Documentation

```bash
# Build package
python -m build

# Upload to PyPI
twine upload dist/*
```

## 📋 Requirements

- Python 3.9+
- Django 3.2+
- PostgreSQL 10+ or MySQL 5.7+ (for production features)
- SQLite (for development)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## 📞 Contact

- **Email**: ranaehtashamali1@gmail.com
- **Phone**: +923224712517
- **GitHub**: [@RanaEhtashamAli](https://github.com/RanaEhtashamAli)

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Django team for the excellent testing framework
- PostgreSQL and MySQL communities for database support
- All contributors who have helped improve this package

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/RanaEhtashamAli/django-concurrent-test/issues)
- **Documentation**: [Read the Docs](https://django-concurrent-test.readthedocs.io/)
- **Discussions**: [GitHub Discussions](https://github.com/RanaEhtashamAli/django-concurrent-test/discussions)
- **Email**: ranaehtashamali1@gmail.com
- **Phone**: +923224712517

---

**Made with ❤️ for the Django community** 