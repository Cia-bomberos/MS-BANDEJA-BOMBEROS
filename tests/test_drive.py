"""Tests de modulo_documentos/drive.py: sin red ni AWS reales."""

import io
import json
from unittest.mock import MagicMock

import pytest

from modulo_documentos import drive


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER_ID", "carpeta-123")
    monkeypatch.setenv("DOCUMENTS_BUCKET", "bucket-test")


def _resp(json_data=None, status=200):
    r = MagicMock()
    r.json.return_value = json_data or {}
    if status >= 400:
        r.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
    return r


class TestNombreArchivo:
    def test_usa_el_codigo_unico_y_limpia_caracteres_invalidos(self):
        assert drive.nombre_archivo("OFICIO N° 001-2026/CGBVP/B3", "id-1") == "OFICIO N° 001-2026-CGBVP-B3.pdf"

    def test_sin_codigo_usa_el_id(self):
        assert drive.nombre_archivo(None, "abc-123") == "abc-123.pdf"


class TestConfigurado:
    def test_false_si_falta_la_carpeta(self, monkeypatch):
        monkeypatch.delenv("DRIVE_FOLDER_ID")
        assert drive.configurado() is False

    def test_true_con_carpeta(self):
        assert drive.configurado() is True


class TestSubirPdf:
    def test_sube_y_devuelve_el_id(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: _resp({"files": []}))
        enviado = {}

        def _post(url, params=None, headers=None, data=None, timeout=None):
            enviado.update(url=url, params=params, headers=headers, data=data, timeout=timeout)
            return _resp({"id": "drive-999"})

        monkeypatch.setattr(drive.requests, "post", _post)
        assert drive.subir_pdf("doc.pdf", b"%PDF-1.4 hola", "tok") == "drive-999"
        assert enviado["headers"]["Authorization"] == "Bearer tok"
        assert b'"parents": ["carpeta-123"]' in enviado["data"]
        assert b"%PDF-1.4 hola" in enviado["data"]
        assert enviado["timeout"] == drive.TIMEOUT_SEGUNDOS

    def test_no_duplica_si_ya_existe(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: _resp({"files": [{"id": "ya-estaba"}]}))
        monkeypatch.setattr(drive.requests, "post", lambda *a, **k: pytest.fail("no debe subir de nuevo"))
        assert drive.subir_pdf("doc.pdf", b"x", "tok") == "ya-estaba"

    def test_error_http_se_propaga(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: _resp({"files": []}))
        monkeypatch.setattr(drive.requests, "post", lambda *a, **k: _resp(status=403))
        with pytest.raises(RuntimeError):
            drive.subir_pdf("doc.pdf", b"x", "tok")


class TestToken:
    def _s3(self, cred):
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(cred).encode())}
        return s3

    def test_cambia_el_refresh_token_por_un_access_token(self, monkeypatch):
        visto = {}

        def _post(url, data=None, timeout=None):
            visto.update(url=url, data=data, timeout=timeout)
            return _resp({"access_token": "acceso-1"})

        monkeypatch.setattr(drive.requests, "post", _post)
        cred = {"client_id": "cid", "client_secret": "sec", "refresh_token": "ref"}
        assert drive._token(self._s3(cred)) == "acceso-1"
        assert visto["url"] == drive.TOKEN_URL
        assert visto["data"] == {"client_id": "cid", "client_secret": "sec", "refresh_token": "ref", "grant_type": "refresh_token"}

    def test_lee_las_credenciales_de_la_key_configurada(self, monkeypatch):
        monkeypatch.setenv("DRIVE_CREDENTIALS_S3_KEY", "otra/ruta.json")
        monkeypatch.setattr(drive.requests, "post", lambda *a, **k: _resp({"access_token": "x"}))
        s3 = self._s3({"client_id": "a", "client_secret": "b", "refresh_token": "c"})
        drive._token(s3)
        assert s3.get_object.call_args.kwargs == {"Bucket": "bucket-test", "Key": "otra/ruta.json"}

    def test_token_revocado_se_propaga(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "post", lambda *a, **k: _resp(status=400))
        s3 = self._s3({"client_id": "a", "client_secret": "b", "refresh_token": "c"})
        with pytest.raises(RuntimeError):
            drive._token(s3)


class TestRespaldar:
    def _s3(self, contenido=b"%PDF-1.4"):
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": io.BytesIO(contenido)}
        return s3

    def test_false_si_no_esta_configurado(self, monkeypatch):
        monkeypatch.delenv("DRIVE_FOLDER_ID")
        assert drive.respaldar("1", "COD", "documentos/a.pdf", s3=self._s3()) is False

    def test_true_solo_cuando_drive_confirma(self, monkeypatch):
        monkeypatch.setattr(drive, "_token", lambda s3=None: "tok")
        monkeypatch.setattr(drive, "subir_pdf", lambda nombre, contenido, token: "drive-1")
        assert drive.respaldar("1", "COD", "documentos/a.pdf", s3=self._s3()) is True

    def test_false_y_sin_lanzar_si_drive_falla(self, monkeypatch):
        monkeypatch.setattr(drive, "_token", lambda s3=None: "tok")

        def _falla(*a, **k):
            raise RuntimeError("Drive caido")

        monkeypatch.setattr(drive, "subir_pdf", _falla)
        assert drive.respaldar("1", "COD", "documentos/a.pdf", s3=self._s3()) is False

    def test_false_si_no_se_puede_leer_de_s3(self):
        s3 = MagicMock()
        s3.get_object.side_effect = RuntimeError("NoSuchKey")
        assert drive.respaldar("1", "COD", "documentos/a.pdf", s3=s3) is False
