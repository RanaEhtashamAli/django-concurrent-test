"""
Django middleware for concurrent testing safety and validation.

This module provides middleware classes that help ensure Django applications
are safe for concurrent testing by detecting potential race conditions,
state mutations, and unsafe operations.
"""

import time
import random
import threading
import logging
from typing import Dict, Any, Optional, Tuple, List, Callable
from contextlib import contextmanager
from django.http import HttpRequest, HttpResponse
from django.conf import settings

logger = logging.getLogger(__name__)

# Runtime configuration for testing overrides
_test_overrides = {
    'delay_range': (0.1, 0.5),  # (min, max) seconds
    'probability': 0.3,  # 30% chance of delay
    'enabled': True
}

def set_test_override(key: str, value: Any) -> None:
    """
    Set a test override for middleware behavior.
    
    Args:
        key: Override key ('delay_range', 'probability', 'enabled')
        value: New value for the override
    """
    if key in _test_overrides:
        _test_overrides[key] = value
        logger.info(f"Test override set: {key} = {value}")
    else:
        logger.warning(f"Unknown test override key: {key}")

def get_test_override(key: str, default: Any = None) -> Any:
    """
    Get a test override value.
    
    Args:
        key: Override key
        default: Default value if key not found
        
    Returns:
        Current override value or default
    """
    return _test_overrides.get(key, default)

def reset_test_overrides() -> None:
    """Reset all test overrides to default values."""
    global _test_overrides
    _test_overrides = {
        'delay_range': (0.1, 0.5),
        'probability': 0.3,
        'enabled': True
    }
    logger.info("Test overrides reset to defaults")

def auto_register_middleware() -> List[str]:
    """
    Auto-register concurrent testing middleware for pytest sessions.
    
    This function automatically adds the concurrent testing middleware
    to Django's MIDDLEWARE setting when running in a test environment.
    
    Returns:
        List of middleware class names that were added
    """
    try:
        # Check if we're in a test environment
        if not hasattr(settings, 'MIDDLEWARE'):
            logger.warning("MIDDLEWARE setting not found, cannot auto-register")
            return []
        
        middleware_classes = list(settings.MIDDLEWARE)
        added_middleware = []
        
        # Add middleware classes if they're not already present
        middleware_to_add = [
            'django_concurrent_test.middleware.ConcurrentSafetyMiddleware',
            'django_concurrent_test.middleware.StateMutationMiddleware',
            'django_concurrent_test.middleware.ConcurrencySimulationMiddleware',
        ]
        
        for middleware_class in middleware_to_add:
            if middleware_class not in middleware_classes:
                middleware_classes.append(middleware_class)
                added_middleware.append(middleware_class)
                logger.info(f"Auto-registered middleware: {middleware_class}")
        
        # Update settings
        settings.MIDDLEWARE = middleware_classes
        
        return added_middleware
        
    except Exception as e:
        logger.warning(f"Failed to auto-register middleware: {e}")
        return []

def get_middleware_config() -> Dict[str, Any]:
    """
    Get recommended middleware configuration for concurrent testing.
    
    Returns:
        Dictionary with middleware configuration recommendations
    """
    return {
        'middleware_classes': [
            'django_concurrent_test.middleware.ConcurrentSafetyMiddleware',
            'django_concurrent_test.middleware.StateMutationMiddleware',
            'django_concurrent_test.middleware.ConcurrencySimulationMiddleware',
        ],
        'settings': {
            'CONCURRENT_TEST_ENABLED': True,
            'CONCURRENT_TEST_DELAY_RANGE': (0.1, 0.5),
            'CONCURRENT_TEST_PROBABILITY': 0.3,
        }
    }

class ConcurrentSafetyMiddleware:
    """
    Middleware to detect and prevent concurrent testing issues.
    
    This middleware monitors request processing to identify potential
    race conditions, state mutations, and unsafe operations that could
    cause problems during concurrent testing.
    """
    
    def __init__(self, get_response: Callable):
        """
        Initialize the middleware.
        
        Args:
            get_response: Django's get_response callable
        """
        self.get_response = get_response
        self.request_count = 0
        self._lock = threading.Lock()
        
    def __call__(self, request: HttpRequest) -> HttpResponse:
        """
        Process the request with concurrent safety checks.
        
        Args:
            request: Django HTTP request
            
        Returns:
            Django HTTP response
        """
        if not get_test_override('enabled', True):
            return self.get_response(request)
        
        with self._lock:
            self.request_count += 1
            current_count = self.request_count
        
        logger.debug(f"Processing request #{current_count}: {request.path}")
        
        # Add request metadata for tracking
        request.concurrent_request_id = current_count
        request.concurrent_start_time = time.time()
        
        try:
            response = self.get_response(request)
            
            # Check for potential issues
            self._check_response_safety(request, response)
            
            return response
            
        except Exception as e:
            logger.error(f"Request #{current_count} failed: {e}")
            raise
        finally:
            duration = time.time() - request.concurrent_start_time
            logger.debug(f"Request #{current_count} completed in {duration:.3f}s")

    def _check_response_safety(self, request: HttpRequest, response: HttpResponse) -> None:
        """
        Check if the response indicates potential concurrent safety issues.
        
        Args:
            request: The processed request
            response: The generated response
        """
        # Check for session modifications
        if hasattr(request, 'session') and request.session.modified:
            logger.warning(f"Session modified in request #{request.concurrent_request_id}")
        
        # Check for long response times (potential blocking)
        duration = time.time() - request.concurrent_start_time
        if duration > 5.0:  # 5 seconds threshold
            logger.warning(f"Slow request #{request.concurrent_request_id}: {duration:.2f}s")
        
        # Check response headers for caching issues
        cache_headers = ['Cache-Control', 'ETag', 'Last-Modified']
        for header in cache_headers:
            if header in response:
                logger.debug(f"Cache header {header} in response #{request.concurrent_request_id}")

class StateMutationMiddleware:
    """
    Middleware to detect state mutations that could cause test interference.
    
    This middleware tracks changes to global state, settings, and other
    mutable objects that could cause issues during concurrent testing.
    """
    
    def __init__(self, get_response: Callable):
        """
        Initialize the middleware.
        
        Args:
            get_response: Django's get_response callable
        """
        self.get_response = get_response
        self._state_snapshots = {}
        self._lock = threading.Lock()
        
    def __call__(self, request: HttpRequest) -> HttpResponse:
        """
        Process the request with state mutation detection.
        
        Args:
            request: Django HTTP request
            
        Returns:
            Django HTTP response
        """
        if not get_test_override('enabled', True):
            return self.get_response(request)
        
        request_id = getattr(request, 'concurrent_request_id', 'unknown')
        
        # Take snapshot of critical state
        before_snapshot = self._take_state_snapshot()
        
        try:
            response = self.get_response(request)
            
            # Check for state mutations
            after_snapshot = self._take_state_snapshot()
            mutations = self._detect_mutations(before_snapshot, after_snapshot)
            
            if mutations:
                logger.warning(f"State mutations detected in request #{request_id}: {mutations}")
            
            return response
            
        except Exception as e:
            logger.error(f"State mutation check failed for request #{request_id}: {e}")
            raise

    def _take_state_snapshot(self) -> Dict[str, Any]:
        """
        Take a snapshot of critical application state.
        
        Returns:
            Dictionary containing state snapshot
        """
        snapshot = {}
        
        # Django settings (read-only copy)
        try:
            snapshot['settings'] = {
                'DEBUG': getattr(settings, 'DEBUG', None),
                'DATABASES': getattr(settings, 'DATABASES', {}),
                'CACHES': getattr(settings, 'CACHES', {}),
            }
        except Exception as e:
            logger.debug(f"Failed to snapshot settings: {e}")
        
        # Thread-local storage
        try:
            from threading import current_thread
            snapshot['thread_id'] = current_thread().ident
        except Exception as e:
            logger.debug(f"Failed to get thread ID: {e}")
        
        return snapshot

    def _detect_mutations(self, before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
        """
        Detect mutations between two state snapshots.
        
        Args:
            before: State snapshot before request
            after: State snapshot after request
            
        Returns:
            List of detected mutations
        """
        mutations = []
        
        # Check settings changes
        if 'settings' in before and 'settings' in after:
            before_settings = before['settings']
            after_settings = after['settings']
            
            for key in before_settings:
                if key in after_settings and before_settings[key] != after_settings[key]:
                    mutations.append(f"Setting {key} changed")
        
        # Check thread changes
        if before.get('thread_id') != after.get('thread_id'):
            mutations.append("Thread context changed")
        
        return mutations

class ConcurrencySimulationMiddleware:
    """
    Middleware to simulate concurrent conditions during testing.
    
    This middleware introduces controlled delays and randomness to
    help detect race conditions and timing-dependent bugs.
    """
    
    def __init__(self, get_response: Callable):
        """
        Initialize the middleware.
        
        Args:
            get_response: Django's get_response callable
        """
        self.get_response = get_response
        
    def __call__(self, request: HttpRequest) -> HttpResponse:
        """
        Process the request with concurrency simulation.
        
        Args:
            request: Django HTTP request
            
        Returns:
            Django HTTP response
        """
        if not get_test_override('enabled', True):
            return self.get_response(request)
        
        # Simulate concurrent conditions
        self._simulate_concurrency()
        
        return self.get_response(request)

    def _simulate_concurrency(self) -> None:
        """
        Simulate concurrent conditions with controlled delays.
        """
        delay_range = get_test_override('delay_range', (0.1, 0.5))
        probability = get_test_override('probability', 0.3)
        
        # Random delay based on probability
        if random.random() < probability:
            delay = random.uniform(*delay_range)
            logger.debug(f"Simulating concurrency with {delay:.3f}s delay")
            time.sleep(delay)

def assert_concurrent_safety(test_func: Callable, max_workers: int = 4, iterations: int = 10) -> None:
    """
    Assert that a function is safe for concurrent execution.
    
    This function runs the provided test function multiple times concurrently
    to detect race conditions and state mutations.
    
    Args:
        test_func: Function to test for concurrent safety
        max_workers: Maximum number of concurrent workers
        iterations: Number of iterations per worker
        
    Raises:
        AssertionError: If concurrent safety issues are detected
    """
    import concurrent.futures
    import threading
    
    results = []
    errors = []
    lock = threading.Lock()
    
    def worker(worker_id: int) -> None:
        """Worker function to execute test iterations."""
        for i in range(iterations):
            try:
                result = test_func()
                with lock:
                    results.append((worker_id, i, result))
            except Exception as e:
                with lock:
                    errors.append((worker_id, i, str(e)))
    
    # Run concurrent workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, i) for i in range(max_workers)]
        concurrent.futures.wait(futures)
    
    # Check for errors
    if errors:
        error_details = "\n".join([f"Worker {w}, Iteration {i}: {e}" for w, i, e in errors])
        raise AssertionError(f"Concurrent safety test failed with {len(errors)} errors:\n{error_details}")
    
    # Check for result consistency
    if len(set(r[2] for r in results)) > 1:
        raise AssertionError("Concurrent safety test failed: inconsistent results detected")
    
    logger.info(f"Concurrent safety test passed: {len(results)} iterations across {max_workers} workers")

def simulate_concurrent_requests(request_func: Callable, num_requests: int = 10, delay_range: Tuple[float, float] = (0.1, 0.3)) -> List[Any]:
    """
    Simulate concurrent requests to test request handling.
    
    Args:
        request_func: Function that simulates a request
        num_requests: Number of concurrent requests to simulate
        delay_range: Range of random delays between requests (min, max) seconds
        
    Returns:
        List of results from all requests
    """
    import concurrent.futures
    import time
    import random
    
    def delayed_request(request_id: int) -> Any:
        """Execute a request with random delay."""
        # Random delay to simulate real-world conditions
        delay = random.uniform(*delay_range)
        time.sleep(delay)
        
        try:
            result = request_func()
            logger.debug(f"Request {request_id} completed successfully")
            return {'request_id': request_id, 'status': 'success', 'result': result}
        except Exception as e:
            logger.error(f"Request {request_id} failed: {e}")
            return {'request_id': request_id, 'status': 'error', 'error': str(e)}
    
    # Execute concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [executor.submit(delayed_request, i) for i in range(num_requests)]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    
    # Analyze results
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    
    logger.info(f"Concurrent request simulation completed: {len(successful)} successful, {len(failed)} failed")
    
    if failed:
        logger.warning(f"Some requests failed: {[f['error'] for f in failed]}")
    
    return results

@contextmanager
def concurrent_test_context():
    """
    Context manager for setting up concurrent testing environment.
    
    This context manager temporarily enables all concurrent testing
    middleware and provides a clean testing environment.
    
    Yields:
        None
    """
    # Store original overrides
    original_overrides = _test_overrides.copy()
    
    try:
        # Enable all concurrent testing features
        _test_overrides.update({
            'enabled': True,
            'delay_range': (0.05, 0.2),  # Shorter delays for testing
            'probability': 0.5,  # Higher probability for testing
        })
        
        logger.info("Concurrent test context activated")
        yield
        
    finally:
        # Restore original overrides
        _test_overrides.clear()
        _test_overrides.update(original_overrides)
        logger.info("Concurrent test context deactivated") 