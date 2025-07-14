"""
Django Concurrent Test - Zero-config parallel testing with secure database templating.
"""

__version__ = "1.0.0"
__author__ = "Django Concurrent Test Team"
__email__ = "dev@example.com"

from .runner import ConcurrentTestRunner
from .exceptions import (
    DatabaseTemplateException,
    WorkerTimeout,
    SecurityException,
    UnsupportedDatabase,
)

__all__ = [
    "ConcurrentTestRunner",
    "DatabaseTemplateException",
    "WorkerTimeout",
    "SecurityException",
    "UnsupportedDatabase",
] 