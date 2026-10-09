"""
Tests adicionales de modulo_documentos/handler.py: endpoints que
test_handler.py no cubría (detalle, archivo, atender, prioridad, envío
externo, descarga, URL de subida) y sus ramas de error. BD y S3 mockeados.
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from modulo_documentos import handler


def _event(grupo="", path_params=None, body=None, query=None):
    ev = {"requestContext": {"authorizer": {"claims": {"cognito:groups": grupo, "sub": "u-1", "name": "Tester"}}}}
    if path_params is not None:
        ev["pathParameters"] = path_params
    if body is not None:
        ev["body"] = json.dumps(body)
    if query is not None:
        ev["queryStringParameters"] = query
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


@pytest.fixture(autouse=True)
def s3_valido(monkeypatch):
    monkeypatch.setattr(handler.s3util, "validar_pdf", lambda key: True)


ID = {"id": "doc-1"}


def _doc(seccion="Maquinas", estado="En proceso", **extra):
    return {"seccion_responsable": seccion, "estado": estado, **extra}


# ---------------------------------------------------------------------------
class TestSolicitarUrlSubida:
    def test_devuelve_la_url_y_la_key(self, monkeypatch):
        monkeypatch.setattr(
            handler.s3util, "generar_url_subida",
            lambda nombre: {"uploadUrl": "https://s3/x", "archivo_s3_key": "documentos/a.pdf", "expiraEn": 300},
        )
        resp = handler.solicitar_url_subida(_event("Jefe_Sanidad", body={"nombre_archivo": "a.pdf"}), None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["archivo_s3_key"] == "documentos/a.pdf"


# ---------------------------------------------------------------------------
class TestObtenerDocumento:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.obtener_documento(_event("Jefatura", ID), None)["statusCode"] == 404

    def test_otra_seccion_no_puede_leer(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Sanidad", "fecha_limite": date.today()}
        assert handler.obtener_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    def test_incluye_historial_y_marca_vencido(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "fecha_limite": date.today() - timedelta(days=1)}
        cur.fetchall.return_value = [{"accion": "registro"}]
        resp = handler.obtener_documento(_event("Jefe_Maquinas", ID), None)
        body = json.loads(resp["body"])
        assert resp["statusCode"] == 200
        assert body["historial"] == [{"accion": "registro"}]
        assert body["vencido"] is True


# ---------------------------------------------------------------------------
class TestListarConFiltro:
    def test_jefatura_filtra_por_estado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchall.return_value = []
        resp = handler.listar_documentos(_event("Jefatura", query={"estado": "Pendiente"}), None)
        assert resp["statusCode"] == 200
        sql, params = cur.execute.call_args.args
        assert "estado = %s" in sql
        assert params == ("Pendiente",)


# ---------------------------------------------------------------------------
class TestActualizarArchivo:
    def test_requiere_key(self, conn_mock):
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={}), None)["statusCode"] == 400

    def test_pdf_invalido(self, conn_mock, monkeypatch):
        monkeypatch.setattr(handler.s3util, "validar_pdf", lambda k: False)
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 400

    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 404

    def test_solo_en_proceso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Pendiente")
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)["statusCode"] == 403

    def test_exito(self, conn_mock):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc()
        resp = handler.actualizar_archivo(_event("Jefe_Maquinas", ID, body={"archivo_s3_key": "k"}), None)
        assert resp["statusCode"] == 200
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
class TestMarcarAtendido:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 404

    def test_estado_invalido(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Archivado")
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    @pytest.mark.parametrize("estado", ["Pendiente", "En proceso"])
    def test_exito_desde_pendiente_o_en_proceso(self, conn_mock, estado):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc(estado=estado)
        assert handler.marcar_atendido(_event("Jefe_Maquinas", ID), None)["statusCode"] == 200
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
class TestAsignarPrioridad:
    def test_prioridad_invalida(self, conn_mock):
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={"prioridad": "Urgente"}), None)["statusCode"] == 400

    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={"prioridad": "Alta"}), None)["statusCode"] == 404

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.asignar_prioridad(_event("Jefe_Maquinas", ID, body={"prioridad": "Alta"}), None)["statusCode"] == 403

    def test_sin_campos(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc()
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={}), None)["statusCode"] == 400

    def test_fecha_mal_formateada(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc()
        assert handler.asignar_prioridad(_event("Jefatura", ID, body={"fecha_limite": "31/12/2026"}), None)["statusCode"] == 400

    def test_exito_marca_prioridad_manual(self, conn_mock):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc()
        body = {"prioridad": "Alta", "fecha_limite": "2026-12-31"}
        assert handler.asignar_prioridad(_event("Jefatura", ID, body=body), None)["statusCode"] == 200
        update = [c for c in cur.execute.call_args_list if "UPDATE documentos" in c.args[0]][0]
        assert "prioridad_manual = true" in update.args[0]
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
class TestEnvioExterno:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID), None)["statusCode"] == 404

    def test_ya_archivado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(estado="Archivado")
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID), None)["statusCode"] == 409

    def test_sin_permiso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = _doc(seccion="Sanidad")
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    def test_exito_cierra_como_atendido(self, conn_mock):
        conn, cur = conn_mock
        cur.fetchone.return_value = _doc()
        assert handler.registrar_envio_externo(_event("Jefe_Maquinas", ID), None)["statusCode"] == 200
        update = [c for c in cur.execute.call_args_list if "UPDATE documentos" in c.args[0]][0]
        assert "'Atendido'" in update.args[0]


# ---------------------------------------------------------------------------
class TestDescargar:
    def test_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.descargar_documento(_event("Jefatura", ID), None)["statusCode"] == 404

    def test_sin_acceso(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Sanidad", "archivo_s3_key": "k"}
        assert handler.descargar_documento(_event("Jefe_Maquinas", ID), None)["statusCode"] == 403

    def test_devuelve_url_de_descarga(self, conn_mock, monkeypatch):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas", "archivo_s3_key": "k"}
        monkeypatch.setattr(handler.s3util, "generar_url_descarga", lambda key: f"https://s3/{key}")
        resp = handler.descargar_documento(_event("Jefe_Maquinas", ID), None)
        assert json.loads(resp["body"]) == {"downloadUrl": "https://s3/k"}


# ---------------------------------------------------------------------------
class TestErroresInesperados:
    def test_derivar_devuelve_500_y_hace_rollback(self, conn_mock):
        conn, cur = conn_mock
        cur.execute.side_effect = [None, RuntimeError("BD caida")]
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas"}
        resp = handler.derivar_documento(_event("Jefatura", ID, body={"seccion_destino": "Sanidad"}), None)
        assert resp["statusCode"] == 500
        conn.rollback.assert_called_once()

    def test_derivar_a_la_misma_seccion(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = {"seccion_responsable": "Maquinas"}
        assert handler.derivar_documento(_event("Jefatura", ID, body={"seccion_destino": "Maquinas"}), None)["statusCode"] == 400

    def test_eliminar_no_encontrado(self, conn_mock):
        _, cur = conn_mock
        cur.fetchone.return_value = None
        assert handler.eliminar_documento(_event("Jefe_Administracion", ID), None)["statusCode"] == 404
