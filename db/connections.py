from dataclasses import dataclass
from typing import Mapping
from urllib.parse import quote_plus

from psycopg_pool import ConnectionPool

from config import config


@dataclass(frozen=True)
class DatabaseTarget:
    """Named PostgreSQL database target.

    The same host/user/password can be reused by multiple project databases.
    Add a new target in ``DATABASE_TARGETS`` when another project database is
    introduced.
    """

    name: str
    database: str

    @property
    def dsn(self) -> str:
        user = quote_plus(config.POSTGRESQL_USER)
        password = quote_plus(config.POSTGRESQL_PASSWORD.get_secret_value())
        host = config.POSTGRESQL_HOST
        database = quote_plus(self.database)
        sslmode = quote_plus(config.POSTGRESQL_SSLMODE)
        return f"postgresql://{user}:{password}@{host}/{database}?sslmode={sslmode}"


DATABASE_TARGETS: Mapping[str, DatabaseTarget] = {
    "default": DatabaseTarget(name="default", database=config.POSTGRESQL_DATABASE),
    "apex": DatabaseTarget(name="apex", database=config.POSTGRESQL_DATABASE_APEX),
}

_pools: dict[str, ConnectionPool] = {}


def get_database_target(name: str) -> DatabaseTarget:
    try:
        return DATABASE_TARGETS[name]
    except KeyError as exc:
        available = ", ".join(sorted(DATABASE_TARGETS))
        raise ValueError(
            f"Unknown database target '{name}'. Available targets: {available}"
        ) from exc


def get_pool(name: str = "default") -> ConnectionPool:
    target = get_database_target(name)
    pool = _pools.get(target.name)
    if pool is None:
        pool = ConnectionPool(target.dsn, open=False)
        _pools[target.name] = pool
    return pool


def open_database_pools(*names: str) -> None:
    for name in names:
        get_pool(name).open(wait=True)


def close_database_pools() -> None:
    for pool in _pools.values():
        pool.close()
    _pools.clear()