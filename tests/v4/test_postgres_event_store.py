from __future__ import annotations

import inspect

import pytest

from codex_claude_orchestrator.v4.postgres_event_store import (
    PostgresConfigurationError,
    PostgresEventStore,
    PostgresEventStoreConfig,
    SCHEMA_STATEMENTS,
)


def test_postgres_config_uses_safe_defaults_without_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_HOST", raising=False)
    monkeypatch.delenv("PG_DB", raising=False)
    monkeypatch.delenv("PG_USER", raising=False)
    monkeypatch.delenv("PG_PASSWORD", raising=False)
    monkeypatch.delenv("PG_PORT", raising=False)

    config = PostgresEventStoreConfig.from_env()

    assert config.host == "124.222.58.173"
    assert config.database == "ragbase"
    assert config.user == "ragbase"
    assert config.port == 5432
    assert config.password is None


def test_postgres_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_HOST", "db.example.test")
    monkeypatch.setenv("PG_DB", "agents")
    monkeypatch.setenv("PG_USER", "runner")
    monkeypatch.setenv("PG_PASSWORD", "secret")
    monkeypatch.setenv("PG_PORT", "15432")

    config = PostgresEventStoreConfig.from_env()

    assert config.host == "db.example.test"
    assert config.database == "agents"
    assert config.user == "runner"
    assert config.password == "secret"
    assert config.port == 15432
    assert config.allow_default_endpoint is False


def test_postgres_store_requires_default_endpoint_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_HOST", raising=False)
    monkeypatch.delenv("PG_DB", raising=False)
    monkeypatch.delenv("PG_USER", raising=False)
    monkeypatch.setenv("PG_PASSWORD", "secret")
    monkeypatch.delenv("PG_ALLOW_DEFAULT_ENDPOINT", raising=False)

    config = PostgresEventStoreConfig.from_env()

    with pytest.raises(PostgresConfigurationError, match="PG_ALLOW_DEFAULT_ENDPOINT"):
        config.connect_kwargs()


def test_postgres_store_allows_default_endpoint_with_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_HOST", raising=False)
    monkeypatch.delenv("PG_DB", raising=False)
    monkeypatch.delenv("PG_USER", raising=False)
    monkeypatch.setenv("PG_PASSWORD", "secret")
    monkeypatch.setenv("PG_ALLOW_DEFAULT_ENDPOINT", "1")

    config = PostgresEventStoreConfig.from_env()

    assert config.connect_kwargs()["host"] == "124.222.58.173"


def test_postgres_store_requires_password_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_PASSWORD", raising=False)
    store = PostgresEventStore(PostgresEventStoreConfig.from_env())

    with pytest.raises(PostgresConfigurationError, match="PG_PASSWORD"):
        store.initialize()


def test_postgres_store_health_reports_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_PASSWORD", raising=False)
    store = PostgresEventStore(PostgresEventStoreConfig.from_env())

    health = store.health()

    assert health["backend"] == "postgres"
    assert health["ok"] is False
    assert health["expected_schema_version"] == 2
    assert health["latest_schema_version"] == 0
    assert "PG_PASSWORD" in health["error"]


def test_postgres_config_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_PORT", "not-a-port")

    with pytest.raises(PostgresConfigurationError, match="PG_PORT"):
        PostgresEventStoreConfig.from_env()


def test_postgres_schema_has_real_v2_migration_and_position() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS)

    assert "position BIGSERIAL" in schema
    assert "ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS round_id" in schema
    assert "ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS contract_id" in schema
    assert "agent_events_round_contract_v2" in schema


def test_postgres_replay_queries_order_by_global_position() -> None:
    assert "ORDER BY position ASC" in inspect.getsource(PostgresEventStore.list_by_turn)
    assert "ORDER BY position ASC" in inspect.getsource(PostgresEventStore.list_all)


def test_postgres_insert_uses_idempotency_conflict_handling() -> None:
    source = inspect.getsource(PostgresEventStore._insert_event)

    assert "ON CONFLICT (idempotency_key) WHERE idempotency_key != ''" in source
    assert "DO NOTHING" in source
    assert "RETURNING event_id" in source
