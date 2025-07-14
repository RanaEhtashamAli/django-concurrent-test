"""
Custom exceptions for django-concurrent-test package.
"""


class DatabaseTemplateException(Exception):
    """Base exception for database template operations."""
    pass


class WorkerTimeout(DatabaseTemplateException):
    """Raised when a worker process times out."""
    pass


class SecurityException(DatabaseTemplateException):
    """Raised when security checks fail."""
    pass


class UnsupportedDatabase(DatabaseTemplateException):
    """Raised when the database backend is not supported."""
    pass


class DatabaseCloneException(DatabaseTemplateException):
    """Raised when database cloning fails."""
    pass


class ConfigurationException(DatabaseTemplateException):
    """Raised when configuration is invalid."""
    pass


class PermissionException(DatabaseTemplateException):
    """Raised when database permissions are insufficient."""
    pass


class WorkerRetryException(DatabaseTemplateException):
    """Raised when a worker should be retried due to recoverable errors."""
    pass 