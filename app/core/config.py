"""Database and cache connection placeholders."""

from dataclasses import dataclass


@dataclass
class PostgresConfig:
    vault_dsn: str
    clinical_dsn: str


@dataclass
class RedisConfig:
    dsn: str


def get_postgres_config() -> PostgresConfig:
    return PostgresConfig(
        vault_dsn="postgresql://vault_user:password@localhost:5432/nexa_vault",
        clinical_dsn="postgresql://clinical_user:password@localhost:5432/nexa_clinical",
    )


def get_redis_config() -> RedisConfig:
    return RedisConfig(dsn="redis://localhost:6379/0")
