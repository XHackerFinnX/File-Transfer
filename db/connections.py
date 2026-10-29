import json
import logging
from dataclasses import dataclass
from typing import Mapping
from threading import RLock
from urllib.parse import quote_plus

from psycopg_pool import ConnectionPool

from config import config


@dataclass(frozen=True)
class DatabaseTarget:
    """Named PostgreSQL database target.

    The same host/user/password can be reused by multiple project databases.
    Add project targets through POSTGRESQL_DATABASES_JSON.
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


def _configured_database_targets() -> Mapping[str, DatabaseTarget]:
    targets: dict[str, DatabaseTarget] = {
        "default": DatabaseTarget(name="default", database=config.POSTGRESQL_DATABASE),
        "apex": DatabaseTarget(name="apex", database=config.POSTGRESQL_DATABASE_APEX),
        "real_baby": DatabaseTarget(
            name="real_baby", database=config.POSTGRESQL_DATABASE_REAL_BABY
        ),
    }
    if not config.POSTGRESQL_DATABASES_JSON.strip():
        return targets

    try:
        raw_targets = json.loads(config.POSTGRESQL_DATABASES_JSON)
    except json.JSONDecodeError as exc:
        raise ValueError("POSTGRESQL_DATABASES_JSON contains invalid JSON") from exc
    if not isinstance(raw_targets, dict):
        raise ValueError("POSTGRESQL_DATABASES_JSON must be a JSON object")

    for raw_name, raw_database in raw_targets.items():
        name = str(raw_name).strip().lower()
        database = str(raw_database).strip()
        if not name:
            raise ValueError("Database target name must not be empty")
        if not database:
            raise ValueError(f"Database target {name!r} requires database name")
        targets[name] = DatabaseTarget(name=name, database=database)
    return targets


DATABASE_TARGETS: Mapping[str, DatabaseTarget] = _configured_database_targets()

_pools: dict[str, ConnectionPool] = {}
_pools_lock = RLock()
LOGGER = logging.getLogger("tilda.outbox")


def get_database_target(name: str) -> DatabaseTarget:
    normalized_name = name.strip().lower()
    try:
        return DATABASE_TARGETS[normalized_name]
    except KeyError as exc:
        available = ", ".join(sorted(DATABASE_TARGETS))
        raise ValueError(
            f"Unknown database target '{name}'. Available targets: {available}"
        ) from exc


def get_pool(name: str = "default") -> ConnectionPool:
    target = get_database_target(name)
    with _pools_lock:
        pool = _pools.get(target.name)
        if pool is None or pool.closed:
            pool = ConnectionPool(
                target.dsn, open=True, min_size=0, max_size=4, max_waiting=20,
                timeout=config.POSTGRESQL_POOL_TIMEOUT_SECONDS,
                reconnect_timeout=config.POSTGRESQL_RECONNECT_TIMEOUT_SECONDS,
                kwargs={
                    "connect_timeout": config.POSTGRESQL_CONNECT_TIMEOUT_SECONDS,
                    "options": f"-c statement_timeout={config.POSTGRESQL_STATEMENT_TIMEOUT_MS} -c lock_timeout=3000",
                    # Detect silent TCP failures instead of retaining dead sockets for hours.
                    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
                    "keepalives_count": 3, "tcp_user_timeout": 15000,
                },
                check=ConnectionPool.check_connection,
            )
            _pools[target.name] = pool
        return pool


def recover_database_pool(name: str) -> bool:
    """Retire an empty pool stuck reconnecting; the next checkout creates a fresh pool.

    Do not reset healthy idle connections or a fully occupied pool just because a
    request timed out. Closing a pool does not interrupt borrowed transactions.
    """
    target = get_database_target(name)
    with _pools_lock:
        pool = _pools.get(target.name)
        if pool is None:
            return False
        if not pool.closed:
            stats = pool.get_stats()
            if (stats.get("pool_available", 0) > 0 or not stats.get("connections_errors", 0)
                    or stats.get("pool_size", 0) >= pool.max_size):
                return False
        del _pools[target.name]
    # Joining workers under the registry lock would block all other projects.
    pool.close(timeout=0.1)
    LOGGER.warning("database_target=%s event=failed_pool_retired", target.name)
    return True


def open_database_pools(*names: str, wait: bool = False) -> None:
    for name in names:
        get_pool(name).open(wait=wait)


def close_database_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()
