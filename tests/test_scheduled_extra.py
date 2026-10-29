"""Tests adicionales de scheduled.py: reclasificación real y _carpeta_raiz."""
from unittest.mock import MagicMock

import pytest

from modulo_documentos import scheduled


@pytest.fixture
def conn_mock(monkeypatch):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None
    monkeypatch.setattr(scheduled, "get_connection", lambda: conn)
    return conn, cursor


class TestReclasificarConCambios:
    """Cubre las líneas del UPDATE + INSERT del historial (las que faltan)."""

    def test_actualiza_prioridad_y_registra_historial(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchall.return_value = [
            {"id": "d1", "prioridad": "Media", "prioridad_manual": False, "fecha_limite": "2026-10-15"},
        ]
        # Forzamos que reclasificar devuelva algo distinto
        monkeypatch.setattr(scheduled, "reclasificar", lambda p, m, f, h: "Alta")
        monkeypatch.setattr(scheduled, "dias_restantes", lambda f, h: 3)
        monkeypatch.setattr(scheduled, "hoy_lima", lambda: "2026-10-10")

        r = scheduled.reclasificar_prioridades({}, None)

        assert r == {"actualizados": 1}
        # El UPDATE de prioridad
        upd = [c for c in cursor.execute.call_args_list if "UPDATE documentos SET prioridad" in c.args[0]]
        assert len(upd) == 1
        assert upd[0].args[1] == ("Alta", "d1")
        # El INSERT al historial
        ins = [c for c in cursor.execute.call_args_list if "INSERT INTO historial_acciones" in c.args[0]]
        assert len(ins) == 1
        assert ins[0].args[1][0] == "d1"
        assert ins[0].args[1][4] == "reclasificacion_automatica"
        conn.commit.assert_called_once()

    def test_sin_cambios_no_toca_la_bd(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchall.return_value = [
            {"id": "d1", "prioridad": "Media", "prioridad_manual": False, "fecha_limite": "2026-10-15"},
        ]
        monkeypatch.setattr(scheduled, "reclasificar", lambda p, m, f, h: "Media")
        monkeypatch.setattr(scheduled, "hoy_lima", lambda: "2026-10-10")

        r = scheduled.reclasificar_prioridades({}, None)

        assert r == {"actualizados": 0}
        assert not [c for c in cursor.execute.call_args_list if "UPDATE" in c.args[0]]
        assert not [c for c in cursor.execute.call_args_list if "INSERT" in c.args[0]]


class TestCarpetaRaiz:
    def test_usa_configuracion_si_existe(self, conn_mock):
        _, cursor = conn_mock
        cursor.fetchone.return_value = {"valor": {"folder_id": "cfg-folder-1"}}
        assert scheduled._carpeta_raiz(cursor) == "cfg-folder-1"

    def test_usa_env_var_si_no_hay_config(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchone.return_value = None
        monkeypatch.setenv("DRIVE_FOLDER_ID", "env-folder-2")
        assert scheduled._carpeta_raiz(cursor) == "env-folder-2"

    def test_devuelve_none_si_no_hay_nada(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchone.return_value = None
        monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
        assert scheduled._carpeta_raiz(cursor) is None