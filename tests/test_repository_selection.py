from app import app as app_module


def test_select_repository_uses_turso_when_cloud_secrets_are_available(monkeypatch):
    class DummySQLiteRepository:
        pass

    class DummyTursoRepository:
        def __init__(self, database_url: str, auth_token: str):
            self.database_url = database_url
            self.auth_token = auth_token

        def rows(self, worksheet: str):
            return []

    monkeypatch.setattr(app_module, "SQLiteRepository", DummySQLiteRepository)
    monkeypatch.setattr(app_module, "TursoRepository", DummyTursoRepository)

    repo, demo_mode = app_module.select_repository("", {"turso": {"TURSO_DATABASE_URL": "libsql://example", "TURSO_AUTH_TOKEN": "token"}})

    assert isinstance(repo, DummyTursoRepository)
    assert demo_mode is False


def test_select_repository_falls_back_to_sqlite_when_turso_validation_fails(monkeypatch):
    class DummySQLiteRepository:
        pass

    class FailingTursoRepository:
        def __init__(self, database_url: str, auth_token: str):
            self.database_url = database_url
            self.auth_token = auth_token

        def rows(self, worksheet: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "SQLiteRepository", DummySQLiteRepository)
    monkeypatch.setattr(app_module, "TursoRepository", FailingTursoRepository)

    repo, demo_mode = app_module.select_repository("", {"turso": {"TURSO_DATABASE_URL": "libsql://example", "TURSO_AUTH_TOKEN": "token"}})

    assert isinstance(repo, DummySQLiteRepository)
    assert demo_mode is False
