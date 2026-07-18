from app import app as app_module


def test_select_repository_defaults_to_sqlite_when_turso_is_unavailable(monkeypatch):
    class DummySQLiteRepository:
        pass

    class DummyInMemoryRepository:
        pass

    def failing_turso_factory(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "SQLiteRepository", DummySQLiteRepository)
    monkeypatch.setattr(app_module, "InMemoryRepository", DummyInMemoryRepository)
    monkeypatch.setattr(app_module, "TursoRepository", failing_turso_factory)

    repo, demo_mode = app_module.select_repository("", {})

    assert isinstance(repo, DummySQLiteRepository)
    assert demo_mode is False
