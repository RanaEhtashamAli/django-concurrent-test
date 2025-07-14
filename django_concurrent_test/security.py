"""
Security utilities for django-concurrent-test package.

This module provides security validation, environment checks, and safety
measures to ensure the concurrent testing package operates securely.
"""

import os
import sys
import logging
import psutil
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Environment variable names
ENV_VARS = {
    'ENABLE_CONCURRENT': 'DJANGO_ENABLE_CONCURRENT',
    'WORKER_COUNT': 'DJANGO_TEST_WORKERS',
    'TIMEOUT': 'DJANGO_TEST_TIMEOUT',
    'BENCHMARK': 'DJANGO_TEST_BENCHMARK',
    'TELEMETRY': 'DJANGO_TEST_TELEMETRY',
    'DEBUG': 'DJANGO_DEBUG',
    'DATABASE_URL': 'DATABASE_URL',
    'SECRET_KEY': 'DJANGO_SECRET_KEY',
}

def get_env_var(key: str, default: Any = None, var_type: type = str) -> Any:
    """
    Get environment variable with type conversion and validation.
    
    Args:
        key: Environment variable key
        default: Default value if not found
        var_type: Type to convert the value to (str, int, bool, float)
        
    Returns:
        Environment variable value converted to specified type
    """
    value = os.environ.get(key, default)
    
    if value is None:
        return default
    
    try:
        if var_type == bool:
            # Handle boolean conversion
            if isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            return bool(value)
        elif var_type == int:
            return int(value)
        elif var_type == float:
            return float(value)
        else:
            return str(value)
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to convert environment variable {key}={value} to {var_type.__name__}: {e}")
        return default

def get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean environment variable."""
    return get_env_var(key, default, bool)

def get_env_int(key: str, default: int = 0) -> int:
    """Get integer environment variable."""
    return get_env_var(key, default, int)

def get_env_float(key: str, default: float = 0.0) -> float:
    """Get float environment variable."""
    return get_env_var(key, default, float)

def get_env_str(key: str, default: str = '') -> str:
    """Get string environment variable."""
    return get_env_var(key, default, str)

def validate_environment() -> None:
    """
    Validate the environment for concurrent testing.
    
    This function checks various security and configuration requirements
    to ensure the environment is safe for concurrent testing.
    
    Raises:
        SecurityException: If environment validation fails
    """
    logger.info("Validating environment for concurrent testing")
    
    # Check if concurrent testing is enabled
    if not get_env_bool(ENV_VARS['ENABLE_CONCURRENT'], False):
        raise SecurityException("Concurrent testing is not enabled")
    
    # Validate DEBUG setting
    debug_enabled = get_env_bool(ENV_VARS['DEBUG'], False)
    if debug_enabled:
        logger.warning("DEBUG mode is enabled - this may impact performance and security")
    
    # Check worker count limits
    worker_count = get_env_int(ENV_VARS['WORKER_COUNT'], 0)
    if worker_count > 0:
        max_workers = get_safe_worker_count()
        if worker_count > max_workers:
            raise SecurityException(f"Worker count {worker_count} exceeds maximum {max_workers}")
    
    # Validate timeout settings
    timeout = get_env_int(ENV_VARS['TIMEOUT'], 300)
    if timeout <= 0 or timeout > 3600:  # 1 hour max
        raise SecurityException(f"Invalid timeout value: {timeout}")
    
    # Check for required environment variables
    required_vars = [ENV_VARS['DATABASE_URL'], ENV_VARS['SECRET_KEY']]
    missing_vars = [var for var in required_vars if not get_env_str(var)]
    
    if missing_vars:
        raise SecurityException(f"Missing required environment variables: {missing_vars}")
    
    logger.info("Environment validation passed")

def get_safe_worker_count() -> int:
    """
    Calculate a safe number of worker processes.
    
    This function determines the maximum number of worker processes
    that can be safely used based on system resources and security limits.
    
    Returns:
        Maximum safe worker count
    """
    try:
        # Get CPU count
        cpu_count = psutil.cpu_count(logical=False) or 1
        
        # Get available memory
        memory = psutil.virtual_memory()
        memory_gb = memory.available / (1024 ** 3)
        
        # Calculate based on memory (assume 512MB per worker)
        memory_based_workers = max(1, int(memory_gb / 0.5))
        
        # Calculate based on CPU (use 75% of cores)
        cpu_based_workers = max(1, int(cpu_count * 0.75))
        
        # Take the minimum to be safe
        safe_workers = min(memory_based_workers, cpu_based_workers)
        
        # Apply hard limits
        max_workers = min(safe_workers, 16)  # Absolute maximum
        min_workers = max(max_workers, 1)    # At least 1
        
        logger.info(f"Calculated safe worker count: {min_workers} (CPU: {cpu_count}, Memory: {memory_gb:.1f}GB)")
        return min_workers
        
    except Exception as e:
        logger.warning(f"Failed to calculate worker count: {e}, using default")
        return 4

def check_telemetry_disabled() -> None:
    """
    Ensure telemetry is disabled for security.
    
    Raises:
        SecurityException: If telemetry is enabled
    """
    telemetry_enabled = get_env_bool(ENV_VARS['TELEMETRY'], False)
    
    if telemetry_enabled:
        raise SecurityException("Telemetry is enabled - this package does not collect telemetry data")
    
    logger.debug("Telemetry check passed")

def validate_database_config() -> None:
    """
    Validate database configuration for concurrent testing.
    
    Raises:
        SecurityException: If database configuration is invalid
    """
    database_url = get_env_str(ENV_VARS['DATABASE_URL'])
    
    if not database_url:
        raise SecurityException("Database URL is not configured")
    
    # Check for supported database backends
    supported_backends = ['postgresql', 'mysql', 'sqlite']
    backend_found = any(backend in database_url.lower() for backend in supported_backends)
    
    if not backend_found:
        raise SecurityException(f"Unsupported database backend in URL: {database_url}")
    
    logger.info("Database configuration validation passed")

def check_system_resources() -> Dict[str, Any]:
    """
    Check system resources for concurrent testing.
    
    Returns:
        Dictionary containing resource information
    """
    try:
        # CPU information
        cpu_count = psutil.cpu_count(logical=False) or 1
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # Memory information
        memory = psutil.virtual_memory()
        memory_available_gb = memory.available / (1024 ** 3)
        memory_total_gb = memory.total / (1024 ** 3)
        
        # Disk information
        disk = psutil.disk_usage('/')
        disk_free_gb = disk.free / (1024 ** 3)
        
        resources = {
            'cpu_count': cpu_count,
            'cpu_percent': cpu_percent,
            'memory_available_gb': memory_available_gb,
            'memory_total_gb': memory_total_gb,
            'disk_free_gb': disk_free_gb,
            'safe_worker_count': get_safe_worker_count()
        }
        
        logger.info(f"System resources: {resources}")
        return resources
        
    except Exception as e:
        logger.warning(f"Failed to check system resources: {e}")
        return {}

def validate_file_permissions(path: str) -> None:
    """
    Validate file permissions for security.
    
    Args:
        path: File path to validate
        
    Raises:
        SecurityException: If file permissions are unsafe
    """
    try:
        if not os.path.exists(path):
            return
        
        # Check if file is writable by others
        stat_info = os.stat(path)
        mode = stat_info.st_mode
        
        # Check for world-writable files
        if mode & 0o002:  # World writable
            raise SecurityException(f"Unsafe file permissions on {path}: world writable")
        
        # Check for group writable files (warn only)
        if mode & 0o020:  # Group writable
            logger.warning(f"Group writable file: {path}")
        
    except Exception as e:
        logger.warning(f"Failed to validate file permissions for {path}: {e}")

@contextmanager
def security_context():
    """
    Context manager for security validation.
    
    This context manager performs security checks and provides
    a secure environment for concurrent testing operations.
    
    Yields:
        None
    """
    try:
        # Perform security validations
        validate_environment()
        check_telemetry_disabled()
        validate_database_config()
        
        # Check system resources
        resources = check_system_resources()
        
        logger.info("Security context activated")
        yield
        
    except Exception as e:
        logger.error(f"Security validation failed: {e}")
        raise
    finally:
        logger.info("Security context deactivated")

def sanitize_log_output(text: str) -> str:
    """
    Sanitize log output to remove sensitive information.
    
    Args:
        text: Text to sanitize
        
    Returns:
        Sanitized text
    """
    # Remove potential secrets
    sensitive_patterns = [
        r'password[=:]\s*\S+',
        r'secret[=:]\s*\S+',
        r'key[=:]\s*\S+',
        r'token[=:]\s*\S+',
        r'api_key[=:]\s*\S+',
    ]
    
    import re
    sanitized = text
    
    for pattern in sensitive_patterns:
        sanitized = re.sub(pattern, r'\1=***', sanitized, flags=re.IGNORECASE)
    
    return sanitized

class SecurityException(Exception):
    """Exception raised for security violations."""
    pass

class ResourceException(Exception):
    """Exception raised for resource-related issues."""
    pass 