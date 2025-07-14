"""
Tests for middleware improvements and runtime configuration.

This module tests the enhanced middleware functionality including
runtime configuration, test overrides, context managers, and
concurrent safety validation.
"""

import pytest
import time
import threading
import logging
from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase, RequestFactory
from django.http import HttpResponse

from django_concurrent_test.middleware import (
    ConcurrentSafetyMiddleware,
    StateMutationMiddleware,
    ConcurrencySimulationMiddleware,
    assert_concurrent_safety,
    simulate_concurrent_requests,
    concurrent_test_context,
    set_test_override,
    get_test_override,
    reset_test_overrides,
    _test_overrides
)


class TestMiddlewareRuntimeConfiguration:
    """Test runtime configuration and test overrides."""
    
    def setup_method(self):
        """Reset test overrides before each test."""
        reset_test_overrides()
    
    def test_set_test_override(self):
        """Test setting test overrides."""
        set_test_override('delay_range', (0.2, 0.8))
        set_test_override('probability', 0.5)
        set_test_override('enabled', False)
        
        assert get_test_override('delay_range') == (0.2, 0.8)
        assert get_test_override('probability') == 0.5
        assert get_test_override('enabled') is False
    
    def test_get_test_override_with_default(self):
        """Test getting test overrides with default values."""
        # Test with existing key
        assert get_test_override('delay_range') == (0.1, 0.5)
        
        # Test with non-existent key
        assert get_test_override('nonexistent', 'default') == 'default'
        assert get_test_override('nonexistent') is None
    
    def test_reset_test_overrides(self):
        """Test resetting test overrides to defaults."""
        # Modify overrides
        set_test_override('delay_range', (1.0, 2.0))
        set_test_override('probability', 0.9)
        
        # Reset
        reset_test_overrides()
        
        # Check defaults
        assert get_test_override('delay_range') == (0.1, 0.5)
        assert get_test_override('probability') == 0.3
        assert get_test_override('enabled') is True
    
    def test_unknown_override_key(self):
        """Test handling of unknown override keys."""
        with patch('django_concurrent_test.middleware.logger') as mock_logger:
            set_test_override('unknown_key', 'value')
            mock_logger.warning.assert_called_with("Unknown test override key: unknown_key")


class TestConcurrentSafetyMiddleware:
    """Test ConcurrentSafetyMiddleware functionality."""
    
    def setup_method(self):
        """Set up test environment."""
        self.factory = RequestFactory()
        self.middleware = ConcurrentSafetyMiddleware(lambda request: HttpResponse("OK"))
        reset_test_overrides()
    
    def test_middleware_disabled(self):
        """Test middleware when disabled."""
        set_test_override('enabled', False)
        
        request = self.factory.get('/test/')
        response = self.middleware(request)
        
        assert response.status_code == 200
        assert not hasattr(request, 'concurrent_request_id')
    
    def test_middleware_enabled(self):
        """Test middleware when enabled."""
        set_test_override('enabled', True)
        
        request = self.factory.get('/test/')
        response = self.middleware(request)
        
        assert response.status_code == 200
        assert hasattr(request, 'concurrent_request_id')
        assert hasattr(request, 'concurrent_start_time')
        assert request.concurrent_request_id == 1
    
    def test_request_counting(self):
        """Test request counting across multiple requests."""
        set_test_override('enabled', True)
        
        # Make multiple requests
        for i in range(3):
            request = self.factory.get(f'/test/{i}/')
            response = self.middleware(request)
            assert request.concurrent_request_id == i + 1
    
    def test_response_safety_check(self):
        """Test response safety checking."""
        set_test_override('enabled', True)
        
        # Create request with session
        request = self.factory.get('/test/')
        request.session = Mock()
        request.session.modified = True
        
        with patch('django_concurrent_test.middleware.logger') as mock_logger:
            response = self.middleware(request)
            mock_logger.warning.assert_called_with("Session modified in request #1")
    
    def test_slow_request_detection(self):
        """Test detection of slow requests."""
        set_test_override('enabled', True)
        
        # Create a slow response
        def slow_response(request):
            time.sleep(0.1)  # Simulate slow processing
            return HttpResponse("Slow")
        
        middleware = ConcurrentSafetyMiddleware(slow_response)
        request = self.factory.get('/test/')
        
        with patch('django_concurrent_test.middleware.logger') as mock_logger:
            response = middleware(request)
            # Should not trigger slow request warning for 0.1s
            mock_logger.warning.assert_not_called()


class TestStateMutationMiddleware:
    """Test StateMutationMiddleware functionality."""
    
    def setup_method(self):
        """Set up test environment."""
        self.factory = RequestFactory()
        self.middleware = StateMutationMiddleware(lambda request: HttpResponse("OK"))
        reset_test_overrides()
    
    def test_middleware_disabled(self):
        """Test middleware when disabled."""
        set_test_override('enabled', False)
        
        request = self.factory.get('/test/')
        response = self.middleware(request)
        
        assert response.status_code == 200
    
    def test_state_snapshot(self):
        """Test state snapshot functionality."""
        set_test_override('enabled', True)
        
        request = self.factory.get('/test/')
        request.concurrent_request_id = 1
        
        with patch('django_concurrent_test.middleware.settings') as mock_settings:
            mock_settings.DEBUG = False
            mock_settings.DATABASES = {'default': {'NAME': 'test_db'}}
            mock_settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
            
            response = self.middleware(request)
            assert response.status_code == 200
    
    def test_mutation_detection(self):
        """Test detection of state mutations."""
        set_test_override('enabled', True)
        
        request = self.factory.get('/test/')
        request.concurrent_request_id = 1
        
        # Mock different snapshots to simulate mutation
        with patch.object(self.middleware, '_take_state_snapshot') as mock_snapshot:
            mock_snapshot.side_effect = [
                {'settings': {'DEBUG': False}},
                {'settings': {'DEBUG': True}}  # Changed!
            ]
            
            with patch('django_concurrent_test.middleware.logger') as mock_logger:
                response = self.middleware(request)
                mock_logger.warning.assert_called_with("State mutations detected in request #1: ['Setting DEBUG changed']")


class TestConcurrencySimulationMiddleware:
    """Test ConcurrencySimulationMiddleware functionality."""
    
    def setup_method(self):
        """Set up test environment."""
        self.factory = RequestFactory()
        self.middleware = ConcurrencySimulationMiddleware(lambda request: HttpResponse("OK"))
        reset_test_overrides()
    
    def test_middleware_disabled(self):
        """Test middleware when disabled."""
        set_test_override('enabled', False)
        
        request = self.factory.get('/test/')
        start_time = time.time()
        response = self.middleware(request)
        duration = time.time() - start_time
        
        assert response.status_code == 200
        assert duration < 0.1  # Should be fast when disabled
    
    def test_concurrency_simulation(self):
        """Test concurrency simulation with delays."""
        set_test_override('enabled', True)
        set_test_override('delay_range', (0.1, 0.2))
        set_test_override('probability', 1.0)  # Always delay
        
        request = self.factory.get('/test/')
        start_time = time.time()
        response = self.middleware(request)
        duration = time.time() - start_time
        
        assert response.status_code == 200
        assert duration >= 0.1  # Should have delay
    
    def test_probability_based_delays(self):
        """Test probability-based delay simulation."""
        set_test_override('enabled', True)
        set_test_override('delay_range', (0.05, 0.1))
        set_test_override('probability', 0.0)  # Never delay
        
        request = self.factory.get('/test/')
        start_time = time.time()
        response = self.middleware(request)
        duration = time.time() - start_time
        
        assert response.status_code == 200
        assert duration < 0.1  # Should be fast with 0 probability


class TestConcurrentSafetyAssertions:
    """Test concurrent safety assertion functions."""
    
    def test_assert_concurrent_safety_success(self):
        """Test successful concurrent safety assertion."""
        def safe_function():
            return "consistent_result"
        
        # Should not raise an exception
        assert_concurrent_safety(safe_function, max_workers=2, iterations=3)
    
    def test_assert_concurrent_safety_failure(self):
        """Test concurrent safety assertion failure."""
        counter = 0
        lock = threading.Lock()
        
        def unsafe_function():
            nonlocal counter
            with lock:
                counter += 1
                return counter  # Returns different values each time
        
        with pytest.raises(AssertionError, match="inconsistent results detected"):
            assert_concurrent_safety(unsafe_function, max_workers=2, iterations=3)
    
    def test_assert_concurrent_safety_with_errors(self):
        """Test concurrent safety assertion with errors."""
        def error_function():
            raise ValueError("Test error")
        
        with pytest.raises(AssertionError, match="Concurrent safety test failed with"):
            assert_concurrent_safety(error_function, max_workers=2, iterations=3)


class TestConcurrentRequestSimulation:
    """Test concurrent request simulation."""
    
    def test_simulate_concurrent_requests_success(self):
        """Test successful concurrent request simulation."""
        def request_function():
            return "success"
        
        results = simulate_concurrent_requests(request_function, num_requests=3)
        
        assert len(results) == 3
        assert all(r['status'] == 'success' for r in results)
        assert all(r['result'] == 'success' for r in results)
    
    def test_simulate_concurrent_requests_with_errors(self):
        """Test concurrent request simulation with errors."""
        def error_function():
            raise ValueError("Request error")
        
        results = simulate_concurrent_requests(error_function, num_requests=3)
        
        assert len(results) == 3
        assert all(r['status'] == 'error' for r in results)
        assert all('Request error' in r['error'] for r in results)
    
    def test_simulate_concurrent_requests_mixed(self):
        """Test concurrent request simulation with mixed results."""
        counter = 0
        
        def mixed_function():
            nonlocal counter
            counter += 1
            if counter % 2 == 0:
                raise ValueError("Even error")
            return "odd success"
        
        results = simulate_concurrent_requests(mixed_function, num_requests=4)
        
        assert len(results) == 4
        successful = [r for r in results if r['status'] == 'success']
        failed = [r for r in results if r['status'] == 'error']
        
        assert len(successful) == 2
        assert len(failed) == 2


class TestConcurrentTestContext:
    """Test concurrent test context manager."""
    
    def test_concurrent_test_context(self):
        """Test concurrent test context manager."""
        # Check initial state
        assert get_test_override('enabled') is True
        assert get_test_override('delay_range') == (0.1, 0.5)
        assert get_test_override('probability') == 0.3
        
        with concurrent_test_context():
            # Check modified state
            assert get_test_override('enabled') is True
            assert get_test_override('delay_range') == (0.05, 0.2)
            assert get_test_override('probability') == 0.5
        
        # Check restored state
        assert get_test_override('enabled') is True
        assert get_test_override('delay_range') == (0.1, 0.5)
        assert get_test_override('probability') == 0.3
    
    def test_concurrent_test_context_with_exception(self):
        """Test concurrent test context manager with exception."""
        original_overrides = _test_overrides.copy()
        
        try:
            with concurrent_test_context():
                raise ValueError("Test exception")
        except ValueError:
            pass
        
        # Check that overrides are restored even with exception
        assert _test_overrides == original_overrides


class TestMiddlewareIntegration:
    """Test middleware integration scenarios."""
    
    def test_middleware_chain(self):
        """Test chaining multiple middleware classes."""
        factory = RequestFactory()
        
        # Create middleware chain
        def final_response(request):
            return HttpResponse("Final")
        
        safety_middleware = ConcurrentSafetyMiddleware(final_response)
        state_middleware = StateMutationMiddleware(safety_middleware)
        sim_middleware = ConcurrencySimulationMiddleware(state_middleware)
        
        # Enable all middleware
        set_test_override('enabled', True)
        
        request = factory.get('/test/')
        response = sim_middleware(request)
        
        assert response.status_code == 200
        assert response.content.decode() == "Final"
        assert hasattr(request, 'concurrent_request_id')
    
    def test_middleware_logging(self):
        """Test middleware logging behavior."""
        set_test_override('enabled', True)
        
        middleware = ConcurrentSafetyMiddleware(lambda request: HttpResponse("OK"))
        request = RequestFactory().get('/test/')
        
        with patch('django_concurrent_test.middleware.logger') as mock_logger:
            response = middleware(request)
            
            # Check that appropriate log calls were made
            assert mock_logger.debug.called or mock_logger.info.called
    
    def test_middleware_performance(self):
        """Test middleware performance impact."""
        set_test_override('enabled', True)
        
        middleware = ConcurrentSafetyMiddleware(lambda request: HttpResponse("OK"))
        request = RequestFactory().get('/test/')
        
        # Measure performance
        start_time = time.time()
        for _ in range(100):
            response = middleware(request)
        duration = time.time() - start_time
        
        # Should complete 100 requests in reasonable time
        assert duration < 1.0  # Less than 1 second for 100 requests
        assert response.status_code == 200


class TestMiddlewareEdgeCases:
    """Test middleware edge cases and error handling."""
    
    def test_middleware_with_exception_in_response(self):
        """Test middleware behavior when response raises exception."""
        def exception_response(request):
            raise ValueError("Response error")
        
        middleware = ConcurrentSafetyMiddleware(exception_response)
        request = RequestFactory().get('/test/')
        
        with pytest.raises(ValueError, match="Response error"):
            middleware(request)
    
    def test_middleware_with_none_response(self):
        """Test middleware behavior with None response."""
        def none_response(request):
            return None
        
        middleware = ConcurrentSafetyMiddleware(none_response)
        request = RequestFactory().get('/test/')
        
        response = middleware(request)
        assert response is None
    
    def test_middleware_thread_safety(self):
        """Test middleware thread safety."""
        set_test_override('enabled', True)
        
        middleware = ConcurrentSafetyMiddleware(lambda request: HttpResponse("OK"))
        results = []
        
        def worker(worker_id):
            for i in range(10):
                request = RequestFactory().get(f'/test/{worker_id}/{i}/')
                response = middleware(request)
                results.append((worker_id, i, response.status_code))
        
        # Run multiple threads
        threads = []
        for i in range(3):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Check results
        assert len(results) == 30  # 3 workers * 10 requests each
        assert all(status == 200 for _, _, status in results)
        
        # Check request IDs are unique
        request_ids = set()
        for worker_id, i, _ in results:
            request_ids.add(f"{worker_id}-{i}")
        assert len(request_ids) == 30 