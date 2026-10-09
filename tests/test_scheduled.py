"""Tests de los jobs programados (modulo_documentos/scheduled.py). BD y Drive mockeados."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from modulo_documentos import scheduled


HOY = date(2026, 1, 1)

class _CursorCtx:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *exc):
        return False


@pytest.fixture
def conn_mock(monkeypatch):
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = _CursorCtx(cursor)
    monkeypatch.setattr(scheduled, "get_connection", lambda: conn)
    return conn, cursor

@pytest.fixture(autouse=True)
def hoy_fijo(monkeypatch):
    """El job usa hoy_lima(): se fija para que el resultado no dependa de la hora real."""
    monkeypatch.setattr(scheduled, "hoy_lima", lambda: HOY)


class TestReclasificarPrioridades:
    def test_actualiza_solo_los_que_cambian(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchall.return_value = [
            # Baja, vence mañana -> debe subir
            {"id": "a", "prioridad": "Baja", "prioridad_manual": False, "fecha_limite": HOY + timedelta(days=1)},
            # Baja, vence en 60 días -> se queda
            {"id": "b", "prioridad": "Baja", "prioridad_manual": False, "fecha_limite": HOY + timedelta(days=60)},
        ]
        r = scheduled.reclasificar_prioridades({}, None)
        assert r == {"actualizados": 1}
        updates = [c for c in cursor.execute.call_args_list if "UPDATE" in c.args[0]]
        assert len(updates) == 1
        assert updates[0].args[1][1] == "a"
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_registra_la_reclasificacion_en_el_historial(self, conn_mock):
        """RN-0014 / CU-006: el cambio automático de prioridad deja rastro con su motivo."""
        _, cursor = conn_mock
        cursor.fetchall.return_value = [
            {"id": "a", "prioridad": "Baja", "prioridad_manual": True, "fecha_limite": HOY + timedelta(days=5)},
        ]
        scheduled.reclasificar_prioridades({}, None)
        insert = [c for c in cursor.execute.call_args_list if "INSERT INTO historial_acciones" in c.args[0]]
        assert len(insert) == 1
        doc_id, sub, nombre, seccion, accion, detalle = insert[0].args[1]
        assert (doc_id, sub, accion) == ("a", "sistema", "reclasificacion_automatica")
        assert "Baja" in detalle and "Alta" in detalle and "5 día" in detalle

    def test_sin_cambios_no_escribe_historial(self, conn_mock):
        _, cursor = conn_mock
        cursor.fetchall.return_value = [
            {"id": "b", "prioridad": "Baja", "prioridad_manual": False, "fecha_limite": HOY + timedelta(days=60)},
        ]
        scheduled.reclasificar_prioridades({}, None)
        assert not [c for c in cursor.execute.call_args_list if "historial_acciones" in c.args[0]]

    def test_sin_pendientes_no_actualiza_nada(self, conn_mock):
        _, cursor = conn_mock
        cursor.fetchall.return_value = []
        assert scheduled.reclasificar_prioridades({}, None) == {"actualizados": 0}


class TestArchivarDocumentos:
    def _doc(self, i):
        return {"id": i, "codigo_unico": f"COD-{i}", "archivo_s3_key": f"documentos/{i}.pdf"}

    def test_archiva_y_confirma_cuando_drive_responde_ok(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchall.side_effect = [[self._doc("1")], []]  # por archivar, pendientes
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: True)
        r = scheduled.archivar_documentos({}, None)
        assert r == {"archivados": 1, "respaldos_reintentados": 0}
        update = [c for c in cursor.execute.call_args_list if "estado = 'Archivado'" in c.args[0]][0]
        assert update.args[1] == (True, "1")

    def test_archiva_pero_no_confirma_si_drive_falla(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchall.side_effect = [[self._doc("1")], []]
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: False)
        scheduled.archivar_documentos({}, None)
        update = [c for c in cursor.execute.call_args_list if "estado = 'Archivado'" in c.args[0]][0]
        assert update.args[1] == (False, "1")  # archivado, pero sin confirmar

    def test_reintenta_los_archivados_sin_confirmar(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchall.side_effect = [[], [self._doc("7"), self._doc("8")]]
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda doc_id, *a: doc_id == "7")
        r = scheduled.archivar_documentos({}, None)
        assert r == {"archivados": 0, "respaldos_reintentados": 1}
        confirmaciones = [c for c in cursor.execute.call_args_list if "confirmado_drive = true" in c.args[0]]
        assert len(confirmaciones) == 1 
        assert confirmaciones[0].args[1] == ("7",)

    def test_commit_por_documento(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchall.side_effect = [[self._doc("1"), self._doc("2")], []]
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: True)
        scheduled.archivar_documentos({}, None)
        assert conn.commit.call_count == 2

    def test_cierra_la_conexion_aunque_falle(self, conn_mock):
        conn, cursor = conn_mock
        cursor.execute.side_effect = RuntimeError("BD caida")
        with pytest.raises(RuntimeError):
            scheduled.archivar_documentos({}, None)
        conn.close.assert_called_once()

    def test_subir_a_drive_delega_en_el_modulo_drive(self, monkeypatch):
        llamado = {}
        monkeypatch.setattr(scheduled.drive, "respaldar", lambda *a: llamado.setdefault("args", a) and True)
        assert scheduled._subir_a_drive("1", "COD", "k.pdf") is True
        assert llamado["args"] == ("1", "COD", "k.pdf")
