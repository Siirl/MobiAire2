"""Fixtures compartidas: base de datos temporal aislada para las pruebas."""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))


@pytest.fixture
def db_tmp(tmp_path, monkeypatch):
    """Repunta la base de datos a un archivo temporal y la inicializa."""
    import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mobiaire_test.db")
    db.init_db()
    return db
