# Transition Guide: Django Default Runner to Enhanced ConcurrentTestRunner

## Overview

This guide helps you migrate from Django's default test runner to the enhanced `django-concurrent-test` package. The transition is designed to be smooth and backward-compatible, with automatic fallback to sequential testing when needed.

## Quick Start

### 1. Installation

```bash
# Install the package
pip install django-concurrent-test

# Add to requirements.txt
echo "django-concurrent-test>=1.0.0" >> requirements.txt
```

### 2. Basic Configuration

```python
# settings.py
TEST_RUNNER = 'django_concurrent_test.runner.ConcurrentTestRunner'
```

### 3. Run Tests

```bash
# Run tests with default settings
python manage.py test

# Or specify test apps
python manage.py test myapp
```

That's it! Your tests will now run concurrently with automatic optimizations.

## Step-by-Step Migration

### Phase 1: Basic Setup (5 minutes)

1. **Install the package**
   ```bash
   pip install django-concurrent-test
   ```

2. **Update settings.py**
   ```python
   # Add to your Django settings
   TEST_RUNNER = 'django_concurrent_test.runner.ConcurrentTestRunner'
   ```

3. **Test the setup**
   ```bash
   python manage.py test --verbosity=2
   ```

### Phase 2: Environment Configuration (10 minutes)

1. **Set worker count**
   ```bash
   # For development
   export DJANGO_TEST_WORKERS=4
   
   # For CI/CD (adjust based on available cores)
   export DJANGO_TEST_WORKERS=8
   ```

2. **Configure timeouts**
   ```bash
   # Set worker timeout (default: 300 seconds)
   export DJANGO_TEST_TIMEOUT=600
   ```

3. **Enable benchmarking**
   ```bash
   # Enable performance reporting
   export DJANGO_TEST_BENCHMARK=true
   ```

### Phase 3: Advanced Features (15 minutes)

1. **Enable JUnit reporting**
   ```python
   # In your test command or management command
   runner = ConcurrentTestRunner(junitxml='test-results.xml')
   runner.run_tests(['myapp'])
   ```

2. **Configure connection management**
   ```bash
   # Optimize connection recycling
   export DJANGO_TEST_MAX_OPERATIONS=1000
   export DJANGO_TEST_HEALTH_CHECK_INTERVAL=60
   ```

3. **Set up retry mechanism**
   ```bash
   # Configure retry behavior
   export DJANGO_TEST_MAX_RETRIES=3
   export DJANGO_TEST_RETRY_DELAY=1.0
   ```

## Configuration Comparison

### Before (Django Default)

```python
# settings.py
# No special configuration needed

# Running tests
python manage.py test
```

### After (Enhanced ConcurrentTestRunner)

```python
# settings.py
TEST_RUNNER = 'django_concurrent_test.runner.ConcurrentTestRunner'

# Environment variables
DJANGO_TEST_WORKERS=4
DJANGO_TEST_BENCHMARK=true
DJANGO_TEST_TIMEOUT=300

# Running tests
python manage.py test --junitxml=test-results.xml
```

## Feature Migration Guide

### 1. Test Discovery

**Before:**
```bash
# Django discovers tests automatically
python manage.py test
```

**After:**
```bash
# Same behavior, but with concurrent execution
python manage.py test

# Enhanced discovery with adaptive chunking
python manage.py test --verbosity=2
```

### 2. Test Output

**Before:**
```
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
...
----------------------------------------------------------------------
Ran 25 tests in 45.234s

OK
Destroying test database for alias 'default'...
```

**After:**
```
[CONCURRENT] Starting production-grade concurrent test run with 4 workers
[CONCURRENT] Setting up 4 test databases with template caching
[CONCURRENT] Using cached database template
[CONCURRENT] Successfully setup 4 databases
[CONCURRENT] Verifying database isolation
[CONCURRENT] Chunk 0: 7 tests, estimated time: 12.34s
[CONCURRENT] Chunk 1: 6 tests, estimated time: 11.89s
[CONCURRENT] Chunk 2: 6 tests, estimated time: 12.01s
[CONCURRENT] Chunk 3: 6 tests, estimated time: 11.67s
...
[CONCURRENT] Production-Grade Benchmark Report
================================================================
Test Execution:
  Total tests: 25
  Workers used: 4
  Total duration: 12.45s
  Worker utilization: 95.2%
  Estimated speedup: 72.5% faster than sequential

Worker Performance:
  Average worker duration: 11.84s
  Min worker duration: 11.67s
  Max worker duration: 12.34s
  Duration variance: 0.67s

Database Operations:
  Total operations: 1,234
  Total errors: 0
  Error rate: 0.00%

Test Metrics:
  Average test duration: 0.498s
  95th percentile duration: 0.892s
  Total queries: 1,234
  Total writes: 456
  Total reads: 778
  Write ratio: 37.0%

Connection Pool:
  Active connections: 4
  Pooled connections: 0
  Connection hits: 1,234
  Connection misses: 0
================================================================
```

### 3. CI/CD Integration

**Before:**
```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run tests
        run: python manage.py test
```

**After:**
```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:13
        env:
          POSTGRES_PASSWORD: postgres
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.9
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        env:
          DJANGO_TEST_WORKERS: 4
          DJANGO_TEST_BENCHMARK: true
          DJANGO_TEST_TIMEOUT: 600
        run: |
          python manage.py test \
            --runner=django_concurrent_test.runner.ConcurrentTestRunner \
            --junitxml=test-results.xml
      - name: Upload test results
        uses: actions/upload-artifact@v2
        with:
          name: test-results
          path: test-results.xml
      - name: Publish test results
        uses: EnricoMi/publish-unit-test-result-action@v2
        if: always()
        with:
          files: test-results.xml
```

### 4. Custom Test Commands

**Before:**
```python
# management/commands/runtests.py
from django.core.management.base import BaseCommand
from django.test.utils import get_runner
from django.conf import settings

class Command(BaseCommand):
    def handle(self, *args, **options):
        TestRunner = get_runner(settings)
        test_runner = TestRunner()
        failures = test_runner.run_tests(['myapp'])
        return failures
```

**After:**
```python
# management/commands/runtests.py
from django.core.management.base import BaseCommand
from django_concurrent_test.runner import ConcurrentTestRunner

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--junitxml',
            type=str,
            help='Path to JUnit XML output file'
        )
        parser.add_argument(
            '--benchmark',
            action='store_true',
            help='Enable benchmark reporting'
        )

    def handle(self, *args, **options):
        runner = ConcurrentTestRunner(
            junitxml=options.get('junitxml'),
            benchmark=options.get('benchmark', True)
        )
        failures = runner.run_tests(['myapp'])
        return failures
```

## Troubleshooting Common Issues

### 1. Database Permission Errors

**Problem:**
```
django_concurrent_test.exceptions.PermissionException: Missing permission: CREATE
```

**Solution:**
```sql
-- Grant necessary permissions to your database user
GRANT CREATE, DROP, CONNECT ON DATABASE your_db TO your_user;
```

### 2. Worker Timeouts

**Problem:**
```
[CONCURRENT] Worker 1 timed out after 300s
```

**Solution:**
```bash
# Increase timeout
export DJANGO_TEST_TIMEOUT=600

# Or reduce worker count
export DJANGO_TEST_WORKERS=2
```

### 3. Memory Issues

**Problem:**
```
MemoryError: Unable to allocate memory for worker
```

**Solution:**
```bash
# Reduce worker count
export DJANGO_TEST_WORKERS=2

# Enable connection recycling
export DJANGO_TEST_MAX_OPERATIONS=500
```

### 4. Template Creation Failures

**Problem:**
```
django_concurrent_test.exceptions.DatabaseTemplateException: Failed to create template database
```

**Solution:**
```bash
# Check database permissions
# Ensure CREATE DATABASE permission

# Or disable template caching temporarily
export DJANGO_TEST_TEMPLATE_CACHE=false
```

### 5. Fallback to Sequential

**Problem:**
```
[CONCURRENT] Security check failed: Concurrent testing not allowed in production
[CONCURRENT] Falling back to sequential testing
```

**Solution:**
```bash
# This is expected behavior in production
# The runner automatically falls back to sequential testing
# No action needed - tests will still run successfully
```

## Performance Expectations

### Typical Improvements

| Test Suite Size | Sequential Time | Concurrent Time | Speedup |
|----------------|----------------|----------------|---------|
| 50 tests       | 30s            | 12s            | 60%     |
| 200 tests      | 120s           | 35s            | 71%     |
| 500 tests      | 300s           | 75s            | 75%     |
| 1000 tests     | 600s           | 140s           | 77%     |

### Resource Usage

- **CPU**: 100% utilization across all workers
- **Memory**: ~50MB per worker (varies by test complexity)
- **Database Connections**: 1 per worker + template connections
- **Disk I/O**: Minimal increase due to template caching

## Rollback Plan

If you need to rollback to the Django default runner:

### 1. Quick Rollback

```python
# settings.py
# Comment out or remove the TEST_RUNNER setting
# TEST_RUNNER = 'django_concurrent_test.runner.ConcurrentTestRunner'
```

### 2. Environment Cleanup

```bash
# Remove environment variables
unset DJANGO_TEST_WORKERS
unset DJANGO_TEST_BENCHMARK
unset DJANGO_TEST_TIMEOUT
```

### 3. Database Cleanup

```sql
-- Clean up any template databases (optional)
DROP DATABASE IF EXISTS your_db_template;
```

## Best Practices

### 1. Gradual Migration

1. **Start with a subset** of your test suite
2. **Monitor performance** and resource usage
3. **Adjust worker count** based on your environment
4. **Enable features incrementally**

### 2. Environment-Specific Configuration

```bash
# Development
export DJANGO_TEST_WORKERS=2
export DJANGO_TEST_BENCHMARK=true

# CI/CD
export DJANGO_TEST_WORKERS=8
export DJANGO_TEST_TIMEOUT=600
export DJANGO_TEST_MAX_RETRIES=3

# Production (fallback only)
# No environment variables needed
```

### 3. Monitoring and Metrics

```python
# Access metrics programmatically
runner = ConcurrentTestRunner(benchmark=True)
failures = runner.run_tests(['myapp'])

# Get performance summary
summary = runner.prometheus_metrics.get_metrics_summary()
print(f"Average test duration: {summary['test_duration_avg']:.3f}s")
```

### 4. Test Optimization

1. **Use database transactions** in your tests
2. **Avoid global state** that could cause conflicts
3. **Keep tests independent** and isolated
4. **Use setUp/tearDown** properly

## Support and Resources

### Documentation

- [Enhanced Features Documentation](ENHANCED_FEATURES.md)
- [API Reference](README.md#api-reference)
- [Configuration Guide](README.md#configuration)

### Community

- [GitHub Issues](https://github.com/your-repo/django-concurrent-test/issues)
- [Discussions](https://github.com/your-repo/django-concurrent-test/discussions)

### Migration Checklist

- [ ] Install django-concurrent-test
- [ ] Update settings.py with TEST_RUNNER
- [ ] Configure environment variables
- [ ] Test basic functionality
- [ ] Enable JUnit reporting
- [ ] Configure CI/CD integration
- [ ] Monitor performance
- [ ] Optimize worker count
- [ ] Enable advanced features
- [ ] Document team procedures

## Conclusion

The transition to the enhanced ConcurrentTestRunner is designed to be smooth and risk-free. The automatic fallback mechanism ensures your tests will always run, even if concurrent execution isn't possible. Start with the basic setup and gradually enable advanced features as you become comfortable with the new system.

The performance improvements are typically significant, with 60-80% faster test execution being common. The enhanced metrics and reporting provide valuable insights into your test suite's performance and help identify optimization opportunities. 