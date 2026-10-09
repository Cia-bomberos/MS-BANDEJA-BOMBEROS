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

from modulo_documentos import tiempo
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
    """Por defecto, cualquier archivo_s3_key se valida como PDF OK y se 'promueve'
    a su key definitiva. Se registra qué keys se eliminaron de S3."""
    monkeypatch.setattr(handler.s3util, "promover_pdf", lambda key: key.replace("pendientes/", "documentos/"))
    eliminados = []
    monkeypatch.setattr(handler.s3util, "eliminar", eliminados.append)
    return eliminados


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
            "fecha_limite": tiempo.hoy_lima() + timedelta(days=20), "fecha_creacion": "2026-01-01T00:00:00",
        }
        body = {"origen": "externo", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(tiempo.hoy_lima() + timedelta(days=20))}
        event = _event(grupo="Jefe_Sanidad", body=body)

        resp = handler.crear_documento(event, None)

        assert resp["statusCode"] == 201
        conn.commit.assert_called_once()

    def test_interno_sin_tipo_es_invalido(self, conn_mock):
        body = {"origen": "interno", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(tiempo.hoy_lima() + timedelta(days=20))}
        event = _event(grupo="Jefe_Sanidad", body=body)
        resp = handler.crear_documento(event, None)
        assert resp["statusCode"] == 400

    def test_interno_con_tipo_genera_codigo(self, conn_mock, correlativo_mock):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {
            "id": "doc-2", "codigo_unico": "OFICIO N° 001-2026/CGBVP/IVCDLC/B3", "tipo": "oficio",
            "modalidad": "completo", "estado": "Pendiente", "prioridad": "Media",
            "fecha_limite": tiempo.hoy_lima() + timedelta(days=20), "fecha_creacion": "2026-01-01T00:00:00",
        }
        body = {"origen": "interno", "tipo": "oficio", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(tiempo.hoy_lima() + timedelta(days=20))}
        event = _event(grupo="Jefe_Maquinas", body=body)

        resp = handler.crear_documento(event, None)

        assert resp["statusCode"] == 201
        body_resp = json.loads(resp["body"])
        assert body_resp["codigo_unico"] == "OFICIO N° 001-2026/CGBVP/IVCDLC/B3"

    def test_pdf_invalido_se_rechaza(self, conn_mock, monkeypatch):
        monkeypatch.setattr(handler.s3util, "promover_pdf", lambda key: None)
        body = {"origen": "interno", "tipo": "oficio", "archivo_s3_key": "documentos/x.pdf",
                "fecha_limite": str(tiempo.hoy_lima() + timedelta(days=20))}
        event = _event(grupo="Jefe_Maquinas", body=body)
        resp = handler.crear_documento(event, None)
        assert resp["statusCode"] == 400

    def test_pdf_invalido_no_crea_nada_en_la_bd(self, conn_mock, monkeypatch):
        conn, cursor = conn_mock
        monkeypatch.setattr(handler.s3util, "promover_pdf", lambda key: None)
        body = {"origen": "externo", "archivo_s3_key": "pendientes/x.pdf",
                "fecha_limite": str(tiempo.hoy_lima() + timedelta(days=20))}
        resp = handler.crear_documento(_event(grupo="Jefe_Sanidad", body=body), None)
        assert resp["statusCode"] == 400
        conn.commit.assert_not_called()

    def test_guarda_la_key_definitiva_no_la_de_pendientes(self, conn_mock, s3_valido):
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {"id": "d", "codigo_unico": None, "tipo": None, "modalidad": "simplificado",
                                        "estado": "Pendiente", "prioridad": "Media",
                                        "fecha_limite": tiempo.hoy_lima(), "fecha_creacion": "x"}
        body = {"origen": "externo", "archivo_s3_key": "pendientes/x.pdf",
                "fecha_limite": str(tiempo.hoy_lima() + timedelta(days=20))}
        handler.crear_documento(_event(grupo="Jefe_Sanidad", body=body), None)
        insert = [c for c in cursor.execute.call_args_list if "INSERT INTO documentos" in c.args[0]][0]
        assert insert.args[1][-1] == "documentos/x.pdf"

    def test_error_de_bd_elimina_el_pdf_para_no_dejarlo_huerfano(self, conn_mock, s3_valido):
        conn, cursor = conn_mock
        cursor.execute.side_effect = RuntimeError("BD caida")
        body = {"origen": "externo", "archivo_s3_key": "pendientes/x.pdf",
                "fecha_limite": str(tiempo.hoy_lima() + timedelta(days=20))}
        resp = handler.crear_documento(_event(grupo="Jefe_Sanidad", body=body), None)
        assert resp["statusCode"] == 500
        conn.rollback.assert_called_once()
        assert s3_valido == ["documentos/x.pdf"]

    def test_el_anio_del_codigo_es_el_de_lima(self, conn_mock, monkeypatch):
        """A las 23:30 del 31/12 en Lima, UTC ya es 2027; el código debe ser 2026."""
        from datetime import datetime
        from modulo_documentos import tiempo
        fijo = datetime(2026, 12, 31, 23, 30, tzinfo=tiempo.LIMA)
        monkeypatch.setattr(handler, "hoy_lima", lambda: fijo.date())
        anios = []
        monkeypatch.setattr(handler, "siguiente_correlativo", lambda c, tipo, anio: anios.append(anio) or 1)
        conn, cursor = conn_mock
        cursor.fetchone.return_value = {"id": "d", "codigo_unico": "x", "tipo": "oficio", "modalidad": "completo",
                                        "estado": "Pendiente", "prioridad": "Media",
                                        "fecha_limite": date.today(), "fecha_creacion": "x"}
        body = {"origen": "interno", "tipo": "oficio", "archivo_s3_key": "pendientes/x.pdf", "fecha_limite": "2027-03-01"}
        handler.crear_documento(_event(grupo="Jefe_Maquinas", body=body), None)
        assert anios == [2026]

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
        assert args[1] == ("Sanidad", "Sanidad")

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
