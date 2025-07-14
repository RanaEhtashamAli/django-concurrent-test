"""
Pytest configuration and fixtures for django-concurrent-test tests.
"""

import pytest
import os
import tempfile
from unittest.mock import Mock, patch


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables for concurrent testing."""
    monkeypatch.setenv("DJANGO_ENABLE_CONCURRENT", "True")
    monkeypatch.setenv("DJANGO_TEST_WORKERS", "2")
    monkeypatch.setenv("DJANGO_TEST_TIMEOUT_PER_TEST", "10")
    monkeypatch.setenv("DJANGO_TEST_TIMEOUT_PER_WORKER", "30")
    monkeypatch.setenv("DJANGO_TEST_TIMEOUT_GLOBAL", "60")
    monkeypatch.setenv("NO_TELEMETRY", "1")
    return {
        "DJANGO_ENABLE_CONCURRENT": "True",
        "DJANGO_TEST_WORKERS": "2",
        "DJANGO_TEST_TIMEOUT_PER_TEST": "10",
        "DJANGO_TEST_TIMEOUT_PER_WORKER": "30",
        "DJANGO_TEST_TIMEOUT_GLOBAL": "60",
        "NO_TELEMETRY": "1"
    }


@pytest.fixture
def mock_django_settings(monkeypatch):
    """Mock Django settings for testing."""
    mock_settings = Mock()
    mock_settings.DEBUG = False
    mock_settings.DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'test_db',
            'USER': 'test_user',
            'PASSWORD': 'test_pass',
            'HOST': 'localhost',
            'PORT': '5432',
        }
    }
    mock_settings.PROJECT_NAME = 'test_project'
    
    with patch('django.conf.settings', mock_settings):
        yield mock_settings


@pytest.fixture
def mock_connection():
    """Mock database connection for testing."""
    mock_conn = Mock()
    mock_conn.vendor = 'postgresql'
    mock_conn.settings_dict = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'test_db',
        'USER': 'test_user',
        'PASSWORD': 'test_pass',
        'HOST': 'localhost',
        'PORT': '5432',
    }
    mock_conn.cursor.return_value.__enter__.return_value = Mock()
    return mock_conn


@pytest.fixture
def temp_timing_file():
    """Create a temporary timing file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"test1": 1.5, "test2": 2.0}')
        temp_file = f.name
    
    yield temp_file
    
    # Cleanup
    try:
        os.unlink(temp_file)
    except OSError:
        pass


@pytest.fixture
def mock_psutil():
    """Mock psutil for memory-based scaling tests."""
    mock_memory = Mock()
    mock_memory.available = 8 * (1024 ** 3)  # 8GB available
    
    mock_psutil = Mock()
    mock_psutil.virtual_memory.return_value = mock_memory
    
    with patch('django_concurrent_test.runner.psutil', mock_psutil):
        yield mock_psutil


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    with patch('django_concurrent_test.runner.logger') as mock_logger:
        yield mock_logger


@pytest.fixture
def sample_test_suites():
    """Sample test suites for testing."""
    return [
        [Mock(_testMethodName=f'test{i}') for i in range(5)],
        [Mock(_testMethodName=f'test{i}') for i in range(5, 10)],
        [Mock(_testMethodName=f'test{i}') for i in range(10, 15)]
    ]


@pytest.fixture
def mock_worker_connections():
    """Mock worker connections for testing."""
    return {
        0: ('test_db_0', Mock()),
        1: ('test_db_1', Mock()),
        2: ('test_db_2', Mock())
    }


@pytest.fixture
def mock_test_metrics():
    """Mock test metrics for testing."""
    from django_concurrent_test.runner import TestMetrics
    
    return [
        TestMetrics(
            test_id="test_app.test_module.TestClass.test1",
            duration=1.5,
            queries=10,
            query_time=0.1,
            writes=2,
            reads=8,
            memory_usage=50.0,
            status="success",
            worker_id=0
        ),
        TestMetrics(
            test_id="test_app.test_module.TestClass.test2",
            duration=2.0,
            queries=15,
            query_time=0.2,
            writes=3,
            reads=12,
            memory_usage=75.0,
            status="success",
            worker_id=1
        )
    ]


@pytest.fixture
def mock_worker_metrics():
    """Mock worker metrics for testing."""
    from django_concurrent_test.runner import WorkerMetrics
    
    return [
        WorkerMetrics(
            worker_id=0,
            start_time=1000.0,
            end_time=1005.0,
            tests_run=5,
            tests_failed=0,
            tests_skipped=1,
            total_queries=50,
            total_query_time=1.0,
            total_writes=10,
            total_reads=40,
            memory_peak=100.0,
            connection_errors=0,
            retry_count=0,
            database_name="test_db_0"
        ),
        WorkerMetrics(
            worker_id=1,
            start_time=1000.0,
            end_time=1006.0,
            tests_run=5,
            tests_failed=1,
            tests_skipped=0,
            total_queries=60,
            total_query_time=1.5,
            total_writes=12,
            total_reads=48,
            memory_peak=120.0,
            connection_errors=1,
            retry_count=1,
            database_name="test_db_1"
        )
    ]


@pytest.fixture
def ci_environment(monkeypatch):
    """Mock CI environment for testing."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    return {
        "CI": "true",
        "GITHUB_ACTIONS": "true"
    }


@pytest.fixture
def production_environment(monkeypatch):
    """Mock production environment for testing."""
    monkeypatch.setenv("DJANGO_ENV", "production")
    return {
        "DJANGO_ENV": "production"
    }


@pytest.fixture
def debug_environment(monkeypatch):
    """Mock debug environment for testing."""
    monkeypatch.setenv("DJANGO_DEBUG", "True")
    return {
        "DJANGO_DEBUG": "True"
    }


@pytest.fixture
def mock_template_cache():
    """Mock template cache for testing."""
    cache = {
        'test_db_test_user': 'test_db_template'
    }
    fingerprints = {
        'test_db_test_user': 'abc12345'
    }
    
    with patch('django_concurrent_test.db._template_cache', cache):
        with patch('django_concurrent_test.db._template_fingerprints', fingerprints):
            yield {
                'cache': cache,
                'fingerprints': fingerprints
            }


@pytest.fixture
def mock_connection_pool():
    """Mock connection pool for testing."""
    pool = {
        '0_test_db_0_default': Mock(),
        '1_test_db_1_default': Mock(),
        '2_test_db_2_default': Mock()
    }
    
    with patch('django_concurrent_test.db._connection_pool', pool):
        yield pool


@pytest.fixture
def sample_timing_data():
    """Sample timing data for testing."""
    return {
        "test_app.test_module.TestClass.test1": 1.5,
        "test_app.test_module.TestClass.test2": 2.0,
        "test_app.test_module.TestClass.test3": 0.8,
        "test_app.test_module.TestClass.test4": 3.2,
        "test_app.test_module.TestClass.test5": 1.1
    }


@pytest.fixture
def mock_pytest_session():
    """Mock pytest session for testing."""
    session = Mock()
    session.config.option.concurrent = True
    session.config.option.workers = 2
    session.config.option.concurrent_timeout = 300
    session.config.option.concurrent_benchmark = True
    session.start_time = 1000.0
    session.end_time = 1010.0
    session.worker_count = 2
    session.database_names = ['test_db_0', 'test_db_1']
    return session


@pytest.fixture
def mock_pytest_item():
    """Mock pytest item for testing."""
    item = Mock()
    item.nodeid = "test_app/test_module.py::TestClass::test_method"
    item.worker_id = 0
    item.database_name = "test_db_0"
    item.config.worker_id = "worker_0"
    return item 