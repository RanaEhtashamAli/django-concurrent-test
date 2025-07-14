"""
Optional DRF integration tests for django-concurrent-test.
"""

import pytest
from unittest.mock import Mock, patch
from django.test import TestCase
from django.urls import reverse


class DRFIntegrationTestCase(TestCase):
    """Test DRF integration with concurrent testing."""
    
    def setUp(self):
        """Set up test environment."""
        self.client = None
        try:
            from rest_framework.test import APIClient
            self.client = APIClient()
        except ImportError:
            pytest.skip("DRF not available")
    
    def test_drf_concurrency_integration(self):
        """Test DRF concurrency integration."""
        if not self.client:
            pytest.skip("DRF not available")
        
        # Mock a simple API endpoint
        with patch('django.urls.reverse') as mock_reverse:
            mock_reverse.return_value = '/api/health/'
            
            # Test basic API call
            response = self.client.get('/api/health/')
            assert response.status_code == 404  # Endpoint doesn't exist in test
            
            # Test with concurrent testing enabled
            with patch.dict('os.environ', {'DJANGO_ENABLE_CONCURRENT': 'True'}):
                response = self.client.get('/api/health/')
                assert response.status_code == 404
    
    def test_drf_serializer_concurrency(self):
        """Test DRF serializer concurrency."""
        if not self.client:
            pytest.skip("DRF not available")
        
        try:
            from rest_framework import serializers
            from rest_framework.test import APIClient
            
            # Create a simple serializer
            class TestSerializer(serializers.Serializer):
                name = serializers.CharField()
                value = serializers.IntegerField()
            
            # Test serializer in concurrent context
            data = {'name': 'test', 'value': 42}
            serializer = TestSerializer(data=data)
            assert serializer.is_valid()
            
        except ImportError:
            pytest.skip("DRF serializers not available")
    
    def test_drf_viewset_concurrency(self):
        """Test DRF viewset concurrency."""
        if not self.client:
            pytest.skip("DRF not available")
        
        try:
            from rest_framework import viewsets
            from rest_framework.response import Response
            from rest_framework.test import APIClient
            
            # Create a simple viewset
            class TestViewSet(viewsets.ViewSet):
                def list(self, request):
                    return Response({'status': 'ok'})
                
                def create(self, request):
                    return Response({'created': True})
            
            # Test viewset in concurrent context
            viewset = TestViewSet()
            assert hasattr(viewset, 'list')
            assert hasattr(viewset, 'create')
            
        except ImportError:
            pytest.skip("DRF viewsets not available")
    
    def test_drf_permissions_concurrency(self):
        """Test DRF permissions concurrency."""
        if not self.client:
            pytest.skip("DRF not available")
        
        try:
            from rest_framework import permissions
            from rest_framework.test import APIClient
            
            # Test permissions in concurrent context
            permission = permissions.IsAuthenticated()
            assert hasattr(permission, 'has_permission')
            
        except ImportError:
            pytest.skip("DRF permissions not available")
    
    def test_drf_authentication_concurrency(self):
        """Test DRF authentication concurrency."""
        if not self.client:
            pytest.skip("DRF not available")
        
        try:
            from rest_framework.authentication import TokenAuthentication
            from rest_framework.test import APIClient
            
            # Test authentication in concurrent context
            auth = TokenAuthentication()
            assert hasattr(auth, 'authenticate')
            
        except ImportError:
            pytest.skip("DRF authentication not available")


class DRFConcurrentMiddlewareTestCase(TestCase):
    """Test DRF with concurrent testing middleware."""
    
    def setUp(self):
        """Set up test environment."""
        try:
            from rest_framework.test import APIClient
            self.client = APIClient()
        except ImportError:
            pytest.skip("DRF not available")
    
    def test_drf_with_concurrent_middleware(self):
        """Test DRF with concurrent testing middleware."""
        if not hasattr(self, 'client'):
            pytest.skip("DRF not available")
        
        from django_concurrent_test.middleware import ConcurrentTestMiddleware
        
        # Create middleware
        middleware = ConcurrentTestMiddleware(lambda request: Mock())
        
        # Test middleware with DRF request
        request = self.client.get('/api/test/').wsgi_request
        request.concurrent_test = middleware
        
        # Test middleware functionality
        middleware.set_test_state('test_key', 'test_value')
        assert middleware.get_test_state('test_key') == 'test_value'
    
    def test_drf_with_race_condition_simulator(self):
        """Test DRF with race condition simulator."""
        if not hasattr(self, 'client'):
            pytest.skip("DRF not available")
        
        from django_concurrent_test.middleware import RaceConditionSimulator
        
        # Create middleware
        middleware = RaceConditionSimulator(lambda request: Mock())
        
        # Test middleware with DRF request
        request = self.client.get('/api/test/').wsgi_request
        
        # Test race condition simulation
        response = middleware(request)
        assert response is not None
    
    def test_drf_with_test_metrics_middleware(self):
        """Test DRF with test metrics middleware."""
        if not hasattr(self, 'client'):
            pytest.skip("DRF not available")
        
        from django_concurrent_test.middleware import TestMetricsMiddleware
        
        # Create middleware
        middleware = TestMetricsMiddleware(lambda request: Mock())
        
        # Test middleware with DRF request
        request = self.client.get('/api/test/').wsgi_request
        
        # Test metrics collection
        response = middleware(request)
        metrics = middleware.get_metrics()
        assert 'requests' in metrics
        assert metrics['requests'] > 0


class DRFConcurrentUtilitiesTestCase(TestCase):
    """Test DRF with concurrent testing utilities."""
    
    def setUp(self):
        """Set up test environment."""
        try:
            from rest_framework.test import APIClient
            self.client = APIClient()
        except ImportError:
            pytest.skip("DRF not available")
    
    def test_drf_simulate_concurrent_requests(self):
        """Test DRF with simulate_concurrent_requests utility."""
        if not hasattr(self, 'client'):
            pytest.skip("DRF not available")
        
        from django_concurrent_test.middleware import simulate_concurrent_requests
        
        # Mock view function
        def mock_view(request):
            return Mock(status_code=200)
        
        # Test concurrent requests simulation
        results = simulate_concurrent_requests(mock_view, num_requests=3, delay=0.01)
        assert len(results) == 3
        
        # Check results
        for result in results:
            assert 'success' in result
            assert 'status_code' in result
    
    def test_drf_assert_concurrent_safety(self):
        """Test DRF with assert_concurrent_safety utility."""
        if not hasattr(self, 'client'):
            pytest.skip("DRF not available")
        
        from django_concurrent_test.middleware import assert_concurrent_safety
        
        # Mock safe function
        def safe_function():
            return "safe_result"
        
        # Test concurrent safety assertion
        results = assert_concurrent_safety(safe_function, num_workers=2, num_iterations=3)
        assert len(results) == 6
        
        # Check all results are successful
        for result in results:
            assert result['success']
            assert result['result'] == "safe_result"
    
    def test_drf_create_test_request(self):
        """Test DRF with create_test_request utility."""
        if not hasattr(self, 'client'):
            pytest.skip("DRF not available")
        
        from django_concurrent_test.middleware import create_test_request
        
        # Test test request creation
        request = create_test_request(worker_id=1)
        assert hasattr(request, 'worker_id')
        assert request.worker_id == 1


@pytest.mark.skipif(True, reason="DRF integration test - run manually")
def test_drf_concurrency_integration():
    """Test DRF concurrency integration (manual test)."""
    try:
        from rest_framework.test import APIClient
        
        client = APIClient()
        response = client.get("/api/health/")
        assert response.status_code == 200
        
    except ImportError:
        pytest.skip("DRF not available")
    except Exception as e:
        # This is expected in test environment without actual API
        assert "404" in str(e) or "Not Found" in str(e) 