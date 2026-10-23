import psycopg
from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests

DATABASE_ERRORS = (psycopg.Error, PoolClosed, PoolTimeout, TooManyRequests)


def database_error_code(exc: Exception) -> str:
    if isinstance(exc, PoolTimeout):
        return "database_pool_timeout"
    if isinstance(exc, TooManyRequests):
        return "database_pool_overloaded"
    if isinstance(exc, PoolClosed):
        return "database_pool_closed"
    return "database_unavailable"
