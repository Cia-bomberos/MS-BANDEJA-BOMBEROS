"""
Unit tests para modulo_documentos/handler.py.

La BD y S3 se mockean por completo: estos tests no tocan AWS ni Postgres
reales. Cubren el flujo principal y las validaciones/autorización de cada
endpoint, no cada combinación posible (ver README para lo que queda fuera
de esta primera versión).
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from modulo_documentos import handler


def _event(grupo="", path_params=None, body=None, query=None):
    ev = {
        "requestContext": {
            "authorizer": {"claims": {"cognito:groups": grupo, "sub": "u-1", "name": "Tester"}}
        }
    }
    if path_params is not None:
        ev["pathParameters"] = path_params
    if body is not None:
        ev["body"] = json.dumps(body)
    if query is not None:
        ev["queryStringParameters"] = query
    return ev


class _CursorCtx:
    """Mock mínimo de `with conn.cursor() as cur:` sobre un MagicMock de cursor."""

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
    """Por defecto, cualquier archivo_s3_key se valida como PDF OK."""
    monkeypatch.setattr(handler.s3util, "validar_pdf", lambda key: True)


@pytest.fixture
def correlativo_mock(monkeypatch):
    monkeypatch.setattr(handler, "siguiente_correlativo", lambda conn, tipo, anio: 1)


# ---------------------------------------------------------------------------
# POST /documentos
# ---------------------------------------------------------------------------

class TestCrearDocumento:
    def test_jefatura_no_puede_registrar(self, conn_mock):
        event = _event(grupo="Jefatura", body={"origen": "interno"})
        resp = handler.crear_documento(event, None)
        assert resp["statusCode"] == 403

    def test_origen_invalido(self, conn_mock):
        event = _event(grupo="Jefe_Sanidad", body={"origen": "otro"})
        resp = handler.crear_documento(event, None)
        assert resp["statusCode"] == 400

    def test_externo_sin_tipo_es_simplificado_y_valido(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {
            "id": "doc-1", "codigo_unico": None, "tipo": None, "modalidad": "simplificado",
            "estado": "Pendiente", "prioridad": "Media",
            "fecha_limite": date.today() + timedelta(days=20), "fecha_creacion": "2026-01-01T00:00:00",
        }
        body = {"origen": "externo", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(date.today() + timedelta(days=20))}
        event = _event(grupo="Jefe_Sanidad", body=body)

        resp = handler.crear_documento(event, None)

        assert resp["statusCode"] == 201
        conn.commit.assert_called_once()

    def test_interno_sin_tipo_es_invalido(self, conn_mock):
        body = {"origen": "interno", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(date.today() + timedelta(days=20))}
        event = _event(grupo="Jefe_Sanidad", body=body)
        resp = handler.crear_documento(event, None)
        assert resp["statusCode"] == 400

    def test_interno_con_tipo_genera_codigo(self, conn_mock, correlativo_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {
            "id": "doc-2", "codigo_unico": "OFICIO N° 001-2026/CGBVP/IVCDLC/B3", "tipo": "oficio",
            "modalidad": "completo", "estado": "Pendiente", "prioridad": "Media",
            "fecha_limite": date.today() + timedelta(days=20), "fecha_creacion": "2026-01-01T00:00:00",
        }
        body = {"origen": "interno", "tipo": "oficio", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(date.today() + timedelta(days=20))}
        event = _event(grupo="Jefe_Maquinas", body=body)

        resp = handler.crear_documento(event, None)

        assert resp["statusCode"] == 201
        body_resp = json.loads(resp["body"])
        assert body_resp["codigo_unico"] == "OFICIO N° 001-2026/CGBVP/IVCDLC/B3"

    def test_pdf_invalido_se_rechaza(self, conn_mock, monkeypatch):
        monkeypatch.setattr(handler.s3util, "validar_pdf", lambda key: False)
        body = {"origen": "interno", "tipo": "oficio", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(date.today() + timedelta(days=20))}
        event = _event(grupo="Jefe_Maquinas", body=body)
        resp = handler.crear_documento(event, None)
        assert resp["statusCode"] == 400

    def test_fecha_limite_mal_formateada(self, conn_mock):
        body = {"origen": "interno", "tipo": "oficio", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": "20-20-2026"}
        event = _event(grupo="Jefe_Maquinas", body=body)
        resp = handler.crear_documento(event, None)
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# GET /documentos
# ---------------------------------------------------------------------------

class TestListarDocumentos:
    def test_jefatura_consulta_todas_las_secciones_sin_filtro_de_seccion(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchall.return_value = []
        event = _event(grupo="Jefatura")

        resp = handler.listar_documentos(event, None)

        assert resp["statusCode"] == 200
        sql_ejecutado = cursor.execute.call_args[0][0]
        assert "WHERE seccion_responsable" not in sql_ejecutado

    def test_jefe_de_seccion_se_filtra_a_su_seccion(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchall.return_value = []
        event = _event(grupo="Jefe_Sanidad")

        resp = handler.listar_documentos(event, None)

        assert resp["statusCode"] == 200
        args = cursor.execute.call_args[0]
        assert "seccion_responsable" in args[0]
        assert args[1] == ("Sanidad",)

    def test_sin_rol_reconocido_se_rechaza(self, conn_mock):
        cursor = conn_mock[1]
        cursor.fetchall.return_value = []
        event = _event(grupo="")
        resp = handler.listar_documentos(event, None)
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# PATCH /documentos/{id}/derivar
# ---------------------------------------------------------------------------

class TestDerivarDocumento:
    def test_seccion_destino_invalida(self, conn_mock):
        event = _event(grupo="Jefe_Sanidad", path_params={"id": "doc-1"}, body={"seccion_destino": "NoExiste"})
        resp = handler.derivar_documento(event, None)
        assert resp["statusCode"] == 400

    def test_documento_no_encontrado(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = None
        event = _event(grupo="Jefe_Sanidad", path_params={"id": "doc-1"}, body={"seccion_destino": "Maquinas"})
        resp = handler.derivar_documento(event, None)
        assert resp["statusCode"] == 404

    def test_otra_seccion_no_puede_derivar(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {"seccion_responsable": "Sanidad"}
        event = _event(grupo="Jefe_Maquinas", path_params={"id": "doc-1"}, body={"seccion_destino": "Administracion"})
        resp = handler.derivar_documento(event, None)
        assert resp["statusCode"] == 403

    def test_derivacion_exitosa(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {"seccion_responsable": "Sanidad"}
        event = _event(grupo="Jefe_Sanidad", path_params={"id": "doc-1"}, body={"seccion_destino": "Administracion"})

        resp = handler.derivar_documento(event, None)

        assert resp["statusCode"] == 200
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# DELETE /documentos/{id}
# ---------------------------------------------------------------------------

class TestEliminarDocumento:
    def test_solo_jefe_administracion_puede_eliminar(self, conn_mock):
        event = _event(grupo="Jefatura", path_params={"id": "doc-1"})
        resp = handler.eliminar_documento(event, None)
        assert resp["statusCode"] == 403

    def test_no_se_puede_eliminar_si_no_esta_archivado(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {"estado": "Atendido", "confirmado_drive": False}
        event = _event(grupo="Jefe_Administracion", path_params={"id": "doc-1"})
        resp = handler.eliminar_documento(event, None)
        assert resp["statusCode"] == 409

    def test_no_se_puede_eliminar_sin_confirmar_drive(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {"estado": "Archivado", "confirmado_drive": False}
        event = _event(grupo="Jefe_Administracion", path_params={"id": "doc-1"})
        resp = handler.eliminar_documento(event, None)
        assert resp["statusCode"] == 409

    def test_eliminacion_exitosa(self, conn_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {"estado": "Archivado", "confirmado_drive": True}
        event = _event(grupo="Jefe_Administracion", path_params={"id": "doc-1"})

        resp = handler.eliminar_documento(event, None)

        assert resp["statusCode"] == 200
        conn.commit.assert_called_once()
