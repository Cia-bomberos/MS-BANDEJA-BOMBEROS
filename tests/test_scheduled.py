"""Tests de scheduled.py (jobs de CloudWatch / EventBridge)."""
from unittest.mock import MagicMock

import pytest

from modulo_documentos import scheduled


OK = {"file_id": "drv-1", "folder_id": "mes-1"}


# ---------------------------------------------------------------------------
# Fixtures y helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def conn_mock(monkeypatch):
    """Devuelve (conn, cursor) con get_connection ya parcheado."""
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    monkeypatch.setattr(scheduled, "get_connection", lambda: conn)
    monkeypatch.setattr(scheduled, "_carpeta_raiz", lambda _cur: "raiz")
    return conn, cursor


def _doc(id_, estado="Atendido"):
    return {
        "id": id_,
        "codigo_unico": f"OF-{id_}",
        "archivo_s3_key": f"k-{id_}",
        "fecha_creacion": "2026-10-01",
        "estado": estado,
    }


def _updates(cursor, fragmento):
    resultado = []
    for c in cursor.execute.call_args_list:
        sql = c.args[0] if c.args else ""
        if not sql.lstrip().upper().startswith("UPDATE"):   # <-- filtro nuevo
            continue
        if fragmento not in sql:
            continue
        resultado.append(c)
    return resultado


def _respaldar_update(cursor):
    """El UPDATE de _respaldar_doc (el que setea confirmado_drive)."""
    matches = _updates(cursor, "confirmado_drive =")
    assert matches, "No se ejecutó el UPDATE de _respaldar_doc"
    return matches[-1]


def _archivar_update(cursor):
    """El UPDATE que cambia el estado a 'Archivado'."""
    matches = _updates(cursor, "estado = 'Archivado'")
    assert matches, "No se ejecutó el UPDATE de estado a 'Archivado'"
    return matches[-1]


# ---------------------------------------------------------------------------
# reclasificar_prioridades (ya cubierto antes)
# ---------------------------------------------------------------------------

class TestReclasificarPrioridades:
    def test_sin_documentos_pendientes_no_hace_nada(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchall.return_value = []
        monkeypatch.setattr(scheduled, "hoy_lima", lambda: "2026-10-01")

        r = scheduled.reclasificar_prioridades({}, None)

        assert r == {"actualizados": 0}
        assert len(_updates(cursor, "UPDATE documentos SET prioridad")) == 0


# ---------------------------------------------------------------------------
# archivar_documentos (comportamiento end-to-end del cron)
# ---------------------------------------------------------------------------

class TestArchivarDocumentos:
    def test_sin_nada_que_archivar_ni_reintentar(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchall.side_effect = [[], []]
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: OK)

        r = scheduled.archivar_documentos({}, None)

        assert r == {"archivados": 0, "respaldos_reintentados": 0}
        assert len(_updates(cursor, "estado = 'Archivado'")) == 0

    def test_archiva_y_confirma_cuando_drive_responde_ok(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchall.side_effect = [[_doc("1")], []]
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: OK)

        r = scheduled.archivar_documentos({}, None)

        assert r == {"archivados": 1, "respaldos_reintentados": 0}

        # 1) _respaldar_doc actualizó confirmado_drive + drive_* con OK
        resp = _respaldar_update(cursor)
        assert resp.args[1] == (True, "drv-1", "mes-1", True, "1")

        # 2) el estado pasó a 'Archivado'
        arch = _archivar_update(cursor)
        assert arch.args[1] == ("1",)

        # 3) commit por documento
        assert conn.commit.call_count == 1

    def test_archiva_pero_no_confirma_si_drive_falla(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchall.side_effect = [[_doc("1")], []]
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: None)

        r = scheduled.archivar_documentos({}, None)

        # se cuenta como archivado aunque no se confirme el respaldo
        assert r == {"archivados": 1, "respaldos_reintentados": 0}

        # confirmado_drive = False y drive_* en None
        resp = _respaldar_update(cursor)
        assert resp.args[1] == (False, None, None, False, "1")

        # igual pasa a 'Archivado'
        arch = _archivar_update(cursor)
        assert arch.args[1] == ("1",)

        # commit igual se hace (para no perder el cambio de estado)
        assert conn.commit.call_count == 1

    def test_reintenta_los_archivados_sin_confirmar(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchall.side_effect = [[], [_doc("7", "Archivado"), _doc("8", "Archivado")]]
        monkeypatch.setattr(
            scheduled, "_subir_a_drive",
            lambda doc, raiz: OK if doc["id"] == "7" else None,
        )

        r = scheduled.archivar_documentos({}, None)

        assert r == {"archivados": 0, "respaldos_reintentados": 1}

        # Solo el doc 7 debe haberse confirmado
        resp_updates = _updates(cursor, "confirmado_drive =")
        # 2 intentos de _respaldar_doc (7 y 8)
        assert len(resp_updates) == 2
        assert resp_updates[0].args[1] == (True, "drv-1", "mes-1", True, "7")
        assert resp_updates[1].args[1] == (False, None, None, False, "8")

        # Solo un commit (el del doc 7 que sí confirmó)
        assert conn.commit.call_count == 1

    def test_si_un_documento_lanza_excepcion_el_resto_no_se_procesa(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchall.side_effect = [[_doc("1"), _doc("2")], []]

        def _falla(doc, raiz):
            if doc["id"] == "1":
                raise RuntimeError("drive caído")
            return OK

        monkeypatch.setattr(scheduled, "_subir_a_drive", _falla)

        with pytest.raises(RuntimeError, match="drive caído"):
            scheduled.archivar_documentos({}, None)


# ---------------------------------------------------------------------------
# respaldar_un_documento (respaldo individual, invocado desde archivar_documento)
# ---------------------------------------------------------------------------

class TestRespaldarUnDocumento:
    def test_sin_id_devuelve_error(self):
        r = scheduled.respaldar_un_documento({}, None)
        assert r == {"ok": False, "error": "documento_id requerido"}

    def test_documento_no_encontrado(self, conn_mock, monkeypatch):
        _, cursor = conn_mock
        cursor.fetchone.return_value = None

        r = scheduled.respaldar_un_documento({"documento_id": "x"}, None)
        assert r == {"ok": False, "error": "documento no encontrado"}

    def test_respaldo_exitoso(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = _doc("1", "Archivado")
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: OK)

        r = scheduled.respaldar_un_documento({"documento_id": "1"}, None)

        assert r == {"ok": True}
        resp = _respaldar_update(cursor)
        assert resp.args[1] == (True, "drv-1", "mes-1", True, "1")
        assert conn.commit.call_count == 1

    def test_respaldo_falla_devuelve_false(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = _doc("1", "Archivado")
        monkeypatch.setattr(scheduled, "_subir_a_drive", lambda *a: None)

        r = scheduled.respaldar_un_documento({"documento_id": "1"}, None)

        assert r == {"ok": False}
        resp = _respaldar_update(cursor)
        assert resp.args[1] == (False, None, None, False, "1")
        assert conn.commit.call_count == 1

    def test_excepcion_hace_rollback_y_devuelve_error(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        cursor.fetchone.side_effect = RuntimeError("boom")

        r = scheduled.respaldar_un_documento({"documento_id": "1"}, None)

        assert r["ok"] is False
        assert "boom" in r["error"]
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()