"""Tests para las funciones que handler.py aún no cubre: marcar_en_proceso,
archivar_documento (con invoke asíncrono) y archivar_ahora."""
import json
from unittest.mock import MagicMock

import pytest

from modulo_documentos import handler


def _event(grupo="", path_params=None, body=None):
    ev = {"requestContext": {"authorizer": {"claims": {"cognito:groups": grupo, "sub": "u-1", "name": "T"}}}}
    if path_params is not None:
        ev["pathParameters"] = path_params
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


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
    monkeypatch.setattr(handler, "get_connection", lambda: conn)
    return conn, cursor


ID = {"id": "doc-1"}


# ---------------------------------------------------------------------------
class TestMarcarEnProceso:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.marcar_en_proceso(_event("Jefe_Maquinas", ID), None)["statusCode"] == 404

    def test_estado_invalido(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "estado": "En proceso"}
        assert handler.marcar_en_proceso(_event("Jefe_Maquinas", ID), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Sanidad", "estado": "Pendiente"}
        assert handler.marcar_en_proceso(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    def test_exito(self, conn_mock):
        conn, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "estado": "Pendiente"}
        body = {"nota": "iniciando revisión"}
        resp = handler.marcar_en_proceso(_event("Jefe_Maquinas", ID, body=body), None)
        assert resp["statusCode"] == 200
        upd = [c for c in cur.execute.call_args_list if "estado = 'En proceso'" in c.args[0]]
        assert len(upd) == 1
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
class TestArchivarDocumento:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.archivar_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 404

    def test_solo_desde_atendido(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "estado": "En proceso"}
        assert handler.archivar_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Sanidad", "estado": "Atendido"}
        assert handler.archivar_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    def test_exito_dispara_lambda_asincrona(self, conn_mock, monkeypatch):
        conn, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "estado": "Atendido"}
        invoke = MagicMock()
        monkeypatch.setattr(handler.lambda_client, "invoke", invoke)
        monkeypatch.setenv("FUNCTION_RESPALDAR_DOCUMENTO", "fn-resp")

        resp = handler.archivar_documento(_event("Jefe_Maquinas", ID), None)

        assert resp["statusCode"] == 200
        assert "Archivado" in json.loads(resp["body"])["mensaje"]
        invoke.assert_called_once()
        kw = invoke.call_args.kwargs
        assert kw["FunctionName"] == "fn-resp"
        assert kw["InvocationType"] == "Event"
        assert json.loads(kw["Payload"]) == {"documento_id": "doc-1"}
        conn.commit.assert_called_once()

    def test_si_lambda_falla_igual_responde_200(self, conn_mock, monkeypatch):
        """El invoke es best-effort: si falla, el cron lo recogerá después."""
        conn, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "estado": "Atendido"}
        monkeypatch.setattr(handler.lambda_client, "invoke", MagicMock(side_effect=RuntimeError("lambda caída")))
        monkeypatch.setenv("FUNCTION_RESPALDAR_DOCUMENTO", "fn-resp")

        resp = handler.archivar_documento(_event("Jefe_Maquinas", ID), None)

        assert resp["statusCode"] == 200
        conn.commit.assert_called_once()

    def test_si_env_var_no_existe_loguea_igual(self, conn_mock, monkeypatch):
        conn, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "estado": "Atendido"}
        monkeypatch.delenv("FUNCTION_RESPALDAR_DOCUMENTO", raising=False)
        resp = handler.archivar_documento(_event("Jefe_Maquinas", ID), None)
        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
class TestArchivarAhora:
    def test_solo_jefe_administracion(self):
        assert handler.archivar_ahora(_event("Jefe_Sanidad"), None)["statusCode"] == 403

    def test_ejecuta_y_devuelve_contadores(self, conn_mock, monkeypatch):
        conn, _ = conn_mock
        # Mockeamos _ejecutar_archivado para no simular toda la lógica
        import modulo_documentos.scheduled as sched
        monkeypatch.setattr(sched, "_ejecutar_archivado", lambda _conn: (3, 2))

        resp = handler.archivar_ahora(_event("Jefe_Administracion"), None)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["archivados"] == 3
        assert body["respaldos_reintentados"] == 2

    def test_si_falla_devuelve_500(self, conn_mock, monkeypatch):
        import modulo_documentos.scheduled as sched
        monkeypatch.setattr(sched, "_ejecutar_archivado", MagicMock(side_effect=RuntimeError("boom")))
        resp = handler.archivar_ahora(_event("Jefe_Administracion"), None)
        assert resp["statusCode"] == 500