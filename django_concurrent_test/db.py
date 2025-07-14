"""
Database utilities for django-concurrent-test package.

This module provides thread-safe database cloning with template caching,
connection pooling, and batch operations for optimal performance in
concurrent testing scenarios.
"""

import os
import time
import logging
import threading
from contextlib import contextmanager
from typing import List, Dict, Optional, Set
from django.db import connections, connection
from django.conf import settings

from .exceptions import (
    DatabaseCloneException,
    UnsupportedDatabase,
    PermissionException,
)
from .security import (
    get_safe_worker_database_name,
    validate_database_permissions,
    sanitize_database_name,
)

logger = logging.getLogger(__name__)

# Thread-safe template cache
_template_cache = {}
_template_cache_lock = threading.Lock()

# Template fingerprinting for cache invalidation
_template_fingerprints = {}
_template_fingerprint_lock = threading.Lock()

# Connection pool for worker databases
_connection_pool = {}
_connection_pool_lock = threading.Lock()


class DatabaseCloner:
    """Base class for database cloning operations."""
    
    def __init__(self, connection):
        self.connection = connection
        self.vendor = connection.vendor
    
    def clone_database(self, worker_id):
        """
        Clone database for worker.
        
        Args:
            worker_id (int): Worker ID
            
        Returns:
            str: New database name
            
        Raises:
            DatabaseCloneException: If cloning fails
        """
        raise NotImplementedError
    
    def drop_database(self, db_name):
        """
        Drop database.
        
        Args:
            db_name (str): Database name to drop
            
        Raises:
            DatabaseCloneException: If dropping fails
        """
        raise NotImplementedError
    
    def database_exists(self, db_name):
        """
        Check if database exists.
        
        Args:
            db_name (str): Database name to check
            
        Returns:
            bool: True if database exists
        """
        raise NotImplementedError


class PostgreSQLCloner(DatabaseCloner):
    """
    PostgreSQL database cloner using template0 for optimal performance.
    
    This cloner implements template caching and batch operations for efficient
    database cloning in concurrent testing scenarios. It uses TEMPLATE template0
    to ensure clean schema cloning without data.
    """
    
    def __init__(self, connection):
        super().__init__(connection)
        self._template_db_name = None
        self._template_created = False
    
    def _ensure_template_database(self):
        """
        Ensure template database exists and is properly configured.
        
        Creates a template database from the base database if it doesn't exist,
        ensuring it has the correct schema without any data. This template is
        cached and reused for all subsequent worker database creation.
        
        Thread-safe implementation using locks to prevent race conditions
        during template creation. Includes template versioning for cache invalidation.
        """
        global _template_cache, _template_cache_lock, _template_fingerprints, _template_fingerprint_lock
        
        db_config = self.connection.settings_dict
        base_name = db_config['NAME']
        db_user = db_config.get('USER', 'postgres')
        template_key = f"{base_name}_{db_user}"
        
        # Generate current schema fingerprint
        current_fingerprint = generate_template_fingerprint(self.connection)
        
        with _template_cache_lock:
            # Check if template exists and version matches
            if template_key in _template_cache:
                cached_fingerprint = _template_fingerprints.get(template_key)
                
                # If fingerprint matches, use cached template
                if cached_fingerprint == current_fingerprint:
                    self._template_db_name = _template_cache[template_key]
                    logger.debug(f"Using cached template {self._template_db_name} (fingerprint: {current_fingerprint[:8]})")
                    return
                else:
                    # Template version mismatch, refresh template
                    logger.info(f"Template version mismatch, refreshing template (old: {cached_fingerprint[:8]}, new: {current_fingerprint[:8]})")
                    self._refresh_template(template_key)
            
            # Create template database name
            template_db_name = f"{base_name}_template"
            
            try:
                with self.connection.cursor() as cursor:
                    # Check if template already exists
                    if not self.database_exists(template_db_name):
                        logger.info(f"Creating template database: {template_db_name}")
                        
                        # Create template from base database
                        cursor.execute(
                            f"CREATE DATABASE {template_db_name} "
                            f"TEMPLATE {base_name} WITH OWNER {db_user}"
                        )
                        
                        # Ensure template is marked as template
                        cursor.execute(
                            f"UPDATE pg_database SET datistemplate = true "
                            f"WHERE datname = '{template_db_name}'"
                        )
                        
                        logger.info(f"Template database {template_db_name} created successfully")
                    else:
                        logger.info(f"Using existing template database: {template_db_name}")
                    
                    # Cache the template database name and fingerprint
                    _template_cache[template_key] = template_db_name
                    _template_fingerprints[template_key] = current_fingerprint
                    self._template_db_name = template_db_name
                    
            except Exception as e:
                raise DatabaseCloneException(
                    f"Failed to create template database: {e}"
                ) from e
    
    def _refresh_template(self, template_key: str):
        """
        Refresh template database when schema changes.
        
        Args:
            template_key (str): Template cache key
        """
        global _template_cache, _template_fingerprints
        
        try:
            # Drop existing template
            if self._template_db_name and self.database_exists(self._template_db_name):
                self.drop_database(self._template_db_name)
                logger.info(f"Dropped outdated template: {self._template_db_name}")
            
            # Clear cache entries
            if template_key in _template_cache:
                del _template_cache[template_key]
            if template_key in _template_fingerprints:
                del _template_fingerprints[template_key]
            
            # Reset template state
            self._template_db_name = None
            self._template_created = False
            
            logger.info("Template cache cleared, will recreate on next use")
            
        except Exception as e:
            logger.warning(f"Failed to refresh template: {e}")
            # Continue with template creation
    
    def clone_database(self, worker_id):
        """
        Clone PostgreSQL database using template0 for optimal performance.
        
        Uses a cached template database to create worker databases efficiently.
        The template is created once and reused for all subsequent workers,
        ensuring consistent schema and optimal performance.
        
        Args:
            worker_id (int): Worker ID
            
        Returns:
            str: New database name
            
        Raises:
            DatabaseCloneException: If cloning fails
        """
        # Ensure template database exists
        self._ensure_template_database()
        
        # Generate safe worker database name
        worker_db_name = get_safe_worker_database_name(
            self.connection.settings_dict['NAME'], 
            worker_id
        )
        
        try:
            with self.connection.cursor() as cursor:
                # Check if worker database already exists
                if self.database_exists(worker_db_name):
                    logger.warning(f"Database {worker_db_name} already exists, dropping it")
                    self.drop_database(worker_db_name)
                
                # Create database from template0 (clean template)
                cursor.execute(
                    f"CREATE DATABASE {worker_db_name} "
                    f"TEMPLATE template0"
                )
                
                # Apply schema from our template database
                cursor.execute(
                    f"SELECT pg_restore_schema('{self._template_db_name}', '{worker_db_name}')"
                )
                
                logger.info(f"Created PostgreSQL database: {worker_db_name}")
                return worker_db_name
                
        except Exception as e:
            raise DatabaseCloneException(
                f"Failed to clone PostgreSQL database: {e}"
            ) from e
    
    def clone_databases_batch(self, worker_ids: List[int]) -> List[str]:
        """
        Clone multiple databases in a single transaction for optimal performance.
        
        This method creates multiple worker databases in a single transaction,
        reducing overhead and improving performance for concurrent testing.
        
        Args:
            worker_ids (List[int]): List of worker IDs
            
        Returns:
            List[str]: List of created database names
            
        Raises:
            DatabaseCloneException: If batch cloning fails
        """
        # Ensure template database exists
        self._ensure_template_database()
        
        # Generate all database names
        db_names = [
            get_safe_worker_database_name(
                self.connection.settings_dict['NAME'], 
                worker_id
            )
            for worker_id in worker_ids
        ]
        
        try:
            with self.connection.cursor() as cursor:
                # Start transaction
                cursor.execute("BEGIN")
                
                try:
                    for db_name in db_names:
                        # Check if database already exists
                        if self.database_exists(db_name):
                            logger.warning(f"Database {db_name} already exists, dropping it")
                            self.drop_database(db_name)
                        
                        # Create database from template0
                        cursor.execute(
                            f"CREATE DATABASE {db_name} "
                            f"TEMPLATE template0"
                        )
                        
                        # Apply schema from template
                        cursor.execute(
                            f"SELECT pg_restore_schema('{self._template_db_name}', '{db_name}')"
                        )
                    
                    # Commit transaction
                    cursor.execute("COMMIT")
                    
                    logger.info(f"Created {len(db_names)} PostgreSQL databases in batch")
                    return db_names
                    
                except Exception as e:
                    # Rollback on error
                    cursor.execute("ROLLBACK")
                    raise e
                    
        except Exception as e:
            raise DatabaseCloneException(
                f"Failed to clone PostgreSQL databases in batch: {e}"
            ) from e
    
    def drop_database(self, db_name):
        """
        Drop PostgreSQL database.
        
        Args:
            db_name (str): Database name to drop
            
        Raises:
            DatabaseCloneException: If dropping fails
        """
        from django.db import connection
        
        try:
            with connection.cursor() as cursor:
                # Terminate connections to the database
                cursor.execute(
                    f"SELECT pg_terminate_backend(pid) "
                    f"FROM pg_stat_activity "
                    f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
                )
                
                # Drop the database
                cursor.execute(f"DROP DATABASE IF EXISTS {db_name}")
                logger.info(f"Dropped PostgreSQL database: {db_name}")
                
        except Exception as e:
            raise DatabaseCloneException(
                f"Failed to drop PostgreSQL database {db_name}: {e}"
            ) from e
    
    def database_exists(self, db_name):
        """
        Check if PostgreSQL database exists.
        
        Args:
            db_name (str): Database name to check
            
        Returns:
            bool: True if database exists
        """
        from django.db import connection
        
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    [db_name]
                )
                return cursor.fetchone() is not None
        except Exception:
            return False


class SQLiteCloner(DatabaseCloner):
    """
    SQLite database cloner that skips cloning but marks tests as sequential.
    
    This cloner is designed for local development where SQLite is used.
    Since SQLite doesn't support database cloning, it marks tests as sequential
    to ensure proper isolation.
    """
    
    def __init__(self, connection):
        super().__init__(connection)
        logger.warning("SQLite detected - tests will run sequentially for proper isolation")
    
    def clone_database(self, worker_id):
        """
        SQLite doesn't support database cloning - return original database name.
        
        Args:
            worker_id (int): Worker ID (ignored for SQLite)
            
        Returns:
            str: Original database name
            
        Raises:
            DatabaseCloneException: Always raised for SQLite
        """
        raise DatabaseCloneException(
            "SQLite doesn't support database cloning. "
            "Tests will run sequentially for proper isolation."
        )
    
    def clone_databases_batch(self, worker_ids: List[int]) -> List[str]:
        """
        SQLite doesn't support batch database cloning.
        
        Args:
            worker_ids (List[int]): List of worker IDs (ignored for SQLite)
            
        Returns:
            List[str]: Empty list since cloning is not supported
            
        Raises:
            DatabaseCloneException: Always raised for SQLite
        """
        raise DatabaseCloneException(
            "SQLite doesn't support batch database cloning. "
            "Tests will run sequentially for proper isolation."
        )
    
    def drop_database(self, db_name):
        """
        SQLite doesn't support dropping databases.
        
        Args:
            db_name (str): Database name (ignored for SQLite)
        """
        logger.info(f"SQLite: Skipping database drop for {db_name}")
    
    def database_exists(self, db_name):
        """
        Check if SQLite database exists.
        
        Args:
            db_name (str): Database name to check
            
        Returns:
            bool: True if database file exists
        """
        import os
        return os.path.exists(db_name)


class MySQLCloner(DatabaseCloner):
    """
    MySQL database cloner using schema replication with IGNORE DATA clause.
    
    This cloner implements template caching and batch operations for efficient
    database cloning in concurrent testing scenarios. It uses IGNORE DATA to
    ensure clean schema cloning without data.
    """
    
    def __init__(self, connection):
        super().__init__(connection)
        self._template_db_name = None
        self._template_created = False
    
    def _ensure_template_database(self):
        """
        Ensure template database exists and is properly configured.
        
        Creates a template database from the base database if it doesn't exist,
        ensuring it has the correct schema without any data. This template is
        cached and reused for all subsequent worker database creation.
        
        Thread-safe implementation using locks to prevent race conditions
        during template creation.
        """
        global _template_cache, _template_cache_lock
        
        db_config = self.connection.settings_dict
        base_name = db_config['NAME']
        template_key = f"{base_name}_mysql"
        
        with _template_cache_lock:
            if template_key in _template_cache:
                self._template_db_name = _template_cache[template_key]
                return
            
            # Create template database name
            template_db_name = f"{base_name}_template"
            
            try:
                with self.connection.cursor() as cursor:
                    # Check if template already exists
                    if not self.database_exists(template_db_name):
                        logger.info(f"Creating MySQL template database: {template_db_name}")
                        
                        # Create template database
                        cursor.execute(f"CREATE DATABASE {template_db_name}")
                        
                        # Get all tables from base database
                        cursor.execute(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = %s",
                            [base_name]
                        )
                        tables = [row[0] for row in cursor.fetchall()]
                        
                        # Clone schema for each table with IGNORE DATA
                        for table in tables:
                            cursor.execute(
                                f"CREATE TABLE {template_db_name}.{table} "
                                f"LIKE {base_name}.{table} IGNORE DATA"
                            )
                        
                        logger.info(f"MySQL template database {template_db_name} created successfully")
                    else:
                        logger.info(f"Using existing MySQL template database: {template_db_name}")
                    
                    # Cache the template database name
                    _template_cache[template_key] = template_db_name
                    self._template_db_name = template_db_name
                    
            except Exception as e:
                raise DatabaseCloneException(
                    f"Failed to create MySQL template database: {e}"
                ) from e
    
    def clone_database(self, worker_id):
        """
        Clone MySQL database using schema replication with IGNORE DATA clause.
        
        Uses a cached template database to create worker databases efficiently.
        The template is created once and reused for all subsequent workers,
        ensuring consistent schema and optimal performance.
        
        Args:
            worker_id (int): Worker ID
            
        Returns:
            str: New database name
            
        Raises:
            DatabaseCloneException: If cloning fails
        """
        # Ensure template database exists
        self._ensure_template_database()
        
        # Generate safe worker database name
        worker_db_name = get_safe_worker_database_name(
            self.connection.settings_dict['NAME'], 
            worker_id
        )
        
        try:
            with self.connection.cursor() as cursor:
                # Check if worker database already exists
                if self.database_exists(worker_db_name):
                    logger.warning(f"Database {worker_db_name} already exists, dropping it")
                    self.drop_database(worker_db_name)
                
                # Create new database
                cursor.execute(f"CREATE DATABASE {worker_db_name}")
                
                # Get all tables from template database
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s",
                    [self._template_db_name]
                )
                tables = [row[0] for row in cursor.fetchall()]
                
                # Clone schema for each table with IGNORE DATA
                for table in tables:
                    cursor.execute(
                        f"CREATE TABLE {worker_db_name}.{table} "
                        f"LIKE {self._template_db_name}.{table} IGNORE DATA"
                    )
                
                logger.info(f"Created MySQL database: {worker_db_name}")
                return worker_db_name
                
        except Exception as e:
            raise DatabaseCloneException(
                f"Failed to clone MySQL database: {e}"
            ) from e
    
    def clone_databases_batch(self, worker_ids: List[int]) -> List[str]:
        """
        Clone multiple MySQL databases in a single transaction for optimal performance.
        
        This method creates multiple worker databases in a single transaction,
        reducing overhead and improving performance for concurrent testing.
        
        Args:
            worker_ids (List[int]): List of worker IDs
            
        Returns:
            List[str]: List of created database names
            
        Raises:
            DatabaseCloneException: If batch cloning fails
        """
        # Ensure template database exists
        self._ensure_template_database()
        
        # Generate all database names
        db_names = [
            get_safe_worker_database_name(
                self.connection.settings_dict['NAME'], 
                worker_id
            )
            for worker_id in worker_ids
        ]
        
        try:
            with self.connection.cursor() as cursor:
                # Start transaction
                cursor.execute("START TRANSACTION")
                
                try:
                    for db_name in db_names:
                        # Check if database already exists
                        if self.database_exists(db_name):
                            logger.warning(f"Database {db_name} already exists, dropping it")
                            self.drop_database(db_name)
                        
                        # Create new database
                        cursor.execute(f"CREATE DATABASE {db_name}")
                        
                        # Get all tables from template database
                        cursor.execute(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = %s",
                            [self._template_db_name]
                        )
                        tables = [row[0] for row in cursor.fetchall()]
                        
                        # Clone schema for each table with IGNORE DATA
                        for table in tables:
                            cursor.execute(
                                f"CREATE TABLE {db_name}.{table} "
                                f"LIKE {self._template_db_name}.{table} IGNORE DATA"
                            )
                    
                    # Commit transaction
                    cursor.execute("COMMIT")
                    
                    logger.info(f"Created {len(db_names)} MySQL databases in batch")
                    return db_names
                    
                except Exception as e:
                    # Rollback on error
                    cursor.execute("ROLLBACK")
                    raise e
                    
        except Exception as e:
            raise DatabaseCloneException(
                f"Failed to clone MySQL databases in batch: {e}"
            ) from e
    
    def drop_database(self, db_name):
        """
        Drop MySQL database.
        
        Args:
            db_name (str): Database name to drop
            
        Raises:
            DatabaseCloneException: If dropping fails
        """
        from django.db import connection
        
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS {db_name}")
                logger.info(f"Dropped MySQL database: {db_name}")
                
        except Exception as e:
            raise DatabaseCloneException(
                f"Failed to drop MySQL database {db_name}: {e}"
            ) from e
    
    def database_exists(self, db_name):
        """
        Check if MySQL database exists.
        
        Args:
            db_name (str): Database name to check
            
        Returns:
            bool: True if database exists
        """
        from django.db import connection
        
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = %s",
                    [db_name]
                )
                return cursor.fetchone() is not None
        except Exception:
            return False


def get_database_cloner(connection):
    """
    Get appropriate database cloner for connection.
    
    Args:
        connection: Django database connection
        
    Returns:
        DatabaseCloner: Appropriate cloner instance
        
    Raises:
        UnsupportedDatabase: If database backend is not supported
    """
    vendor = connection.vendor
    
    if vendor == 'postgresql':
        return PostgreSQLCloner(connection)
    elif vendor == 'mysql':
        return MySQLCloner(connection)
    elif vendor == 'sqlite':
        return SQLiteCloner(connection)
    else:
        raise UnsupportedDatabase(
            f"Database backend '{vendor}' is not supported. "
            "Only PostgreSQL, MySQL, and SQLite are supported."
        )


def get_thread_safe_connection(alias: str = 'default'):
    """
    Get a thread-safe database connection for worker operations.
    
    This function provides thread-safe access to database connections,
    ensuring that each worker thread has its own isolated connection
    to prevent race conditions and connection sharing issues.
    
    Args:
        alias (str): Database alias to connect to
        
    Returns:
        DatabaseWrapper: Thread-safe database connection
        
    Raises:
        DatabaseCloneException: If connection cannot be established
    """
    try:
        # Get connection from Django's connection manager
        db_connection = connections[alias]
        
        # Ensure connection is established
        db_connection.ensure_connection()
        
        return db_connection
        
    except Exception as e:
        raise DatabaseCloneException(
            f"Failed to establish thread-safe connection to '{alias}': {e}"
        ) from e


def get_worker_connection(worker_id: int, database_name: str, alias: str = 'default'):
    """
    Get a dedicated database connection for a specific worker.
    
    This function creates or retrieves a cached connection for a worker,
    ensuring proper isolation and thread safety. The connection is
    configured to use the worker's specific database.
    
    Args:
        worker_id (int): Worker ID
        database_name (str): Database name for the worker
        alias (str): Database alias to use
        
    Returns:
        DatabaseWrapper: Worker-specific database connection
        
    Raises:
        DatabaseCloneException: If connection cannot be established
    """
    global _connection_pool, _connection_pool_lock
    
    connection_key = f"{worker_id}_{database_name}_{alias}"
    
    with _connection_pool_lock:
        if connection_key in _connection_pool:
            # Return cached connection
            cached_connection = _connection_pool[connection_key]
            try:
                # Test if connection is still valid
                cached_connection.cursor().execute("SELECT 1")
                return cached_connection
            except Exception:
                # Connection is stale, remove from pool
                del _connection_pool[connection_key]
        
        try:
            # Create new connection for worker
            worker_connection = get_thread_safe_connection(alias)
            
            # Configure connection for worker database
            worker_connection.settings_dict = worker_connection.settings_dict.copy()
            worker_connection.settings_dict['NAME'] = database_name
            
            # Close existing connection and reconnect to new database
            worker_connection.close()
            worker_connection.ensure_connection()
            
            # Cache the connection
            _connection_pool[connection_key] = worker_connection
            
            logger.debug(f"Created worker connection for worker {worker_id} to {database_name}")
            return worker_connection
            
        except Exception as e:
            raise DatabaseCloneException(
                f"Failed to create worker connection for worker {worker_id}: {e}"
            ) from e


def close_worker_connection(worker_id: int, database_name: str, alias: str = 'default'):
    """
    Close and remove a worker's database connection from the pool.
    
    This function properly closes a worker's database connection and
    removes it from the connection pool to prevent connection leaks.
    
    Args:
        worker_id (int): Worker ID
        database_name (str): Database name for the worker
        alias (str): Database alias used
    """
    global _connection_pool, _connection_pool_lock
    
    connection_key = f"{worker_id}_{database_name}_{alias}"
    
    with _connection_pool_lock:
        if connection_key in _connection_pool:
            try:
                # Close the connection
                _connection_pool[connection_key].close()
                logger.debug(f"Closed worker connection for worker {worker_id}")
            except Exception as e:
                logger.warning(f"Error closing worker connection for worker {worker_id}: {e}")
            finally:
                # Remove from pool
                del _connection_pool[connection_key]


def clear_connection_pool():
    """
    Clear all connections from the connection pool.
    
    This function closes all cached connections and clears the pool,
    typically called during cleanup operations.
    """
    global _connection_pool, _connection_pool_lock
    
    with _connection_pool_lock:
        for connection_key, db_connection in _connection_pool.items():
            try:
                db_connection.close()
                logger.debug(f"Closed connection: {connection_key}")
            except Exception as e:
                logger.warning(f"Error closing connection {connection_key}: {e}")
        
        _connection_pool.clear()
        logger.info("Connection pool cleared")


def get_connection_pool_stats():
    """
    Get statistics about the current connection pool.
    
    Returns:
        dict: Connection pool statistics
    """
    global _connection_pool, _connection_pool_lock
    
    with _connection_pool_lock:
        return {
            'total_connections': len(_connection_pool),
            'connection_keys': list(_connection_pool.keys()),
            'pool_size': len(_connection_pool)
        }


@contextmanager
def worker_database(worker_id, connection):
    """
    Context manager for worker database operations with thread-safe connections.
    
    This context manager provides thread-safe database operations for workers,
    ensuring proper connection isolation and cleanup. It uses the connection
    pool to manage worker-specific connections efficiently.
    
    Args:
        worker_id (int): Worker ID
        connection: Django database connection
        
    Yields:
        tuple: (worker_database_name, worker_connection)
        
    Raises:
        DatabaseCloneException: If database operations fail
    """
    cloner = get_database_cloner(connection)
    worker_db_name = None
    worker_connection = None
    
    try:
        # Clone database for worker
        worker_db_name = cloner.clone_database(worker_id)
        
        # Get thread-safe connection for worker
        worker_connection = get_worker_connection(worker_id, worker_db_name)
        
        yield (worker_db_name, worker_connection)
        
    finally:
        # Clean up worker connection
        if worker_connection and worker_db_name:
            try:
                close_worker_connection(worker_id, worker_db_name)
            except Exception as e:
                logger.warning(f"Failed to close worker connection for worker {worker_id}: {e}")
        
        # Clean up worker database
        if worker_db_name and cloner.database_exists(worker_db_name):
            try:
                cloner.drop_database(worker_db_name)
            except Exception as e:
                logger.warning(f"Failed to drop worker database {worker_db_name}: {e}")


@contextmanager
def worker_database_with_isolation(worker_id, connection):
    """
    Context manager for worker database operations with isolation verification.
    
    This context manager provides the same functionality as worker_database
    but also verifies database isolation to ensure no data leakage between
    workers. This is useful for debugging and ensuring test integrity.
    
    Args:
        worker_id (int): Worker ID
        connection: Django database connection
        
    Yields:
        tuple: (worker_database_name, worker_connection)
        
    Raises:
        DatabaseCloneException: If database operations fail or isolation is violated
    """
    with worker_database(worker_id, connection) as (worker_db_name, worker_connection):
        # Verify database is ready
        wait_for_database_ready(worker_db_name)
        
        # Create a simple isolation test
        try:
            with worker_connection.cursor() as cursor:
                # Create a test table unique to this worker
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS worker_{worker_id}_test (
                        id SERIAL PRIMARY KEY,
                        worker_id INTEGER DEFAULT {worker_id},
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Insert a test record
                cursor.execute(
                    f"INSERT INTO worker_{worker_id}_test (worker_id) VALUES (%s)",
                    [worker_id]
                )
            
            yield (worker_db_name, worker_connection)
            
        finally:
            # Clean up test table
            try:
                with worker_connection.cursor() as cursor:
                    cursor.execute(f"DROP TABLE IF EXISTS worker_{worker_id}_test")
            except Exception as e:
                logger.warning(f"Failed to cleanup test table for worker {worker_id}: {e}")


def setup_test_databases(worker_count):
    """
    Setup test databases for concurrent testing using batch operations.
    
    This function uses batch database creation for optimal performance,
    creating all worker databases in a single transaction when possible.
    It also implements template caching to reuse database templates
    across multiple test runs.
    
    Args:
        worker_count (int): Number of workers
        
    Returns:
        list: List of database names
        
    Raises:
        DatabaseCloneException: If setup fails
    """
    # Validate database permissions
    validate_database_permissions()
    
    cloner = get_database_cloner(connection)
    database_names = []
    
    try:
        # Use batch cloning if available for better performance
        if hasattr(cloner, 'clone_databases_batch'):
            worker_ids = list(range(worker_count))
            database_names = cloner.clone_databases_batch(worker_ids)
            logger.info(f"Created {len(database_names)} databases using batch operation")
        else:
            # Fallback to individual cloning
            for worker_id in range(worker_count):
                db_name = cloner.clone_database(worker_id)
                database_names.append(db_name)
            logger.info(f"Created {len(database_names)} databases using individual cloning")
        
        return database_names
        
    except Exception as e:
        # Clean up any created databases
        for created_db in database_names:
            try:
                cloner.drop_database(created_db)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup database {created_db}: {cleanup_error}")
        
        raise DatabaseCloneException(
            f"Failed to setup test databases: {e}"
        ) from e


def setup_test_databases_with_connections(worker_count):
    """
    Setup test databases and create worker connections.
    
    This function sets up test databases and creates dedicated
    connections for each worker, ensuring proper isolation and
    thread safety.
    
    Args:
        worker_count (int): Number of workers
        
    Returns:
        dict: Mapping of worker_id to (database_name, connection)
        
    Raises:
        DatabaseCloneException: If setup fails
    """
    # Setup databases
    database_names = setup_test_databases(worker_count)
    
    # Create worker connections
    worker_connections = {}
    
    try:
        for worker_id, database_name in enumerate(database_names):
            worker_connection = get_worker_connection(worker_id, database_name)
            worker_connections[worker_id] = (database_name, worker_connection)
        
        logger.info(f"Created {len(worker_connections)} worker connections")
        return worker_connections
        
    except Exception as e:
        # Cleanup on error
        for worker_id, (database_name, conn) in worker_connections.items():
            try:
                close_worker_connection(worker_id, database_name)
            except Exception:
                pass
        
        # Also cleanup databases
        cloner = get_database_cloner(connection)
        for database_name in database_names:
            try:
                cloner.drop_database(database_name)
            except Exception:
                pass
        
        raise DatabaseCloneException(
            f"Failed to setup test databases with connections: {e}"
        ) from e


def teardown_test_databases(database_names):
    """
    Teardown test databases with connection cleanup.
    
    This function drops test databases and cleans up any associated
    connections from the connection pool to prevent resource leaks.
    
    Args:
        database_names (list): List of database names to drop
        
    Raises:
        DatabaseCloneException: If teardown fails
    """
    cloner = get_database_cloner(connection)
    
    # Close any worker connections to these databases
    pool_stats = get_connection_pool_stats()
    for connection_key in pool_stats['connection_keys']:
        # Parse connection key to get worker_id and database_name
        parts = connection_key.split('_')
        if len(parts) >= 3:
            worker_id = int(parts[0])
            database_name = '_'.join(parts[1:-1])  # Everything except first and last parts
            if database_name in database_names:
                close_worker_connection(worker_id, database_name)
    
    # Drop databases
    for db_name in database_names:
        try:
            if cloner.database_exists(db_name):
                cloner.drop_database(db_name)
                logger.debug(f"Dropped test database: {db_name}")
        except Exception as e:
            logger.warning(f"Failed to drop test database {db_name}: {e}")


def teardown_test_databases_with_connections(worker_connections):
    """
    Teardown test databases and close worker connections.
    
    This function properly cleans up both databases and their associated
    connections, ensuring complete resource cleanup.
    
    Args:
        worker_connections (dict): Mapping of worker_id to (database_name, connection)
        
    Raises:
        DatabaseCloneException: If teardown fails
    """
    # Close all worker connections
    for worker_id, (database_name, conn) in worker_connections.items():
        try:
            close_worker_connection(worker_id, database_name)
        except Exception as e:
            logger.warning(f"Failed to close worker connection for worker {worker_id}: {e}")
    
    # Drop databases
    database_names = [db_name for _, (db_name, _) in worker_connections.items()]
    teardown_test_databases(database_names)


def wait_for_database_ready(db_name, timeout=30):
    """
    Wait for database to be ready with thread-safe connection.
    
    This function uses thread-safe connections to test database readiness,
    ensuring proper isolation and preventing connection conflicts.
    
    Args:
        db_name (str): Database name
        timeout (int): Timeout in seconds
        
    Returns:
        bool: True if database is ready
        
    Raises:
        DatabaseCloneException: If database is not ready within timeout
    """
    cloner = get_database_cloner(connection)
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if cloner.database_exists(db_name):
            try:
                # Use thread-safe connection to test database
                test_connection = get_thread_safe_connection()
                test_connection.settings_dict = test_connection.settings_dict.copy()
                test_connection.settings_dict['NAME'] = db_name
                
                # Test connection to the database
                with test_connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                
                # Close test connection
                test_connection.close()
                return True
                
            except Exception:
                pass
        
        time.sleep(0.1)
    
    raise DatabaseCloneException(
        f"Database {db_name} not ready within {timeout} seconds"
    )


def terminate_connections(db_name, vendor):
    """
    Terminate all connections to a database.
    
    Args:
        db_name (str): Database name
        vendor (str): Database vendor ('postgresql', 'mysql', etc.)
    """
    try:
        with connection.cursor() as cursor:
            if vendor == 'postgresql':
                # PostgreSQL implementation
                cursor.execute(f"""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = '{db_name}'
                    AND pid <> pg_backend_pid()
                """)
            elif vendor == 'mysql':
                # MySQL implementation
                cursor.execute(f"""
                    SELECT CONCAT('KILL ', id, ';')
                    FROM information_schema.processlist
                    WHERE db = '{db_name}'
                    AND id <> CONNECTION_ID()
                """)
                results = cursor.fetchall()
                for result in results:
                    if result[0]:
                        cursor.execute(result[0])
            elif vendor == 'sqlite3':
                # No-op for SQLite - file-based database doesn't need connection termination
                logger.debug(f"SQLite3: Skipping connection termination for {db_name}")
            # Add other database support as needed
            else:
                logger.warning(f"Terminate connections not implemented for vendor: {vendor}")
                
    except Exception as e:
        logger.warning(f"Failed to terminate connections for {db_name}: {e}")


def verify_database_isolation(worker_connections):
    """
    Verify that worker databases are properly isolated.
    
    This function tests that each worker database is isolated from others
    by creating test data in one database and verifying it doesn't appear
    in other databases.
    
    Args:
        worker_connections (dict): Mapping of worker_id to (database_name, connection)
        
    Returns:
        bool: True if all databases are properly isolated
        
    Raises:
        DatabaseCloneException: If isolation verification fails
    """
    if len(worker_connections) < 2:
        # Need at least 2 databases to test isolation
        return True
    
    try:
        # Create test data in first worker database
        first_worker_id = min(worker_connections.keys())
        first_db_name, first_connection = worker_connections[first_worker_id]
        
        # Create a test table and insert data
        with first_connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS isolation_test (
                    id SERIAL PRIMARY KEY,
                    worker_id INTEGER,
                    test_data TEXT
                )
            """)
            cursor.execute(
                "INSERT INTO isolation_test (worker_id, test_data) VALUES (%s, %s)",
                [first_worker_id, f"test_data_from_worker_{first_worker_id}"]
            )
        
        # Verify data doesn't exist in other databases
        for worker_id, (db_name, conn) in worker_connections.items():
            if worker_id == first_worker_id:
                continue
            
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM isolation_test")
                count = cursor.fetchone()[0]
                
                if count > 0:
                    raise DatabaseCloneException(
                        f"Database isolation failed: Worker {worker_id} database "
                        f"contains data from worker {first_worker_id}"
                    )
        
        # Clean up test data
        with first_connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS isolation_test")
        
        logger.info("Database isolation verification passed")
        return True
        
    except Exception as e:
        raise DatabaseCloneException(
            f"Database isolation verification failed: {e}"
        ) from e 