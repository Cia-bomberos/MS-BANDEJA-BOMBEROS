"""Tests de modulo_documentos/drive.py: sin red ni AWS reales."""

import io
import json
from datetime import datetime, timezone
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

    def test_none_si_no_hay_carpeta(self, monkeypatch):
        monkeypatch.delenv("DRIVE_FOLDER_ID")
        assert drive.respaldar("1", "COD", "documentos/a.pdf", s3=self._s3()) is None

    def test_devuelve_ids_solo_cuando_drive_confirma(self, monkeypatch):
        monkeypatch.setattr(drive, "_token", lambda s3=None: "tok")
        monkeypatch.setattr(drive, "carpeta_destino", lambda fecha, raiz, token: "mes-1")
        monkeypatch.setattr(drive, "subir_pdf", lambda nombre, contenido, token, carpeta=None: "drive-1")
        r = drive.respaldar("1", "COD", "documentos/a.pdf", s3=self._s3())
        assert r == {"file_id": "drive-1", "folder_id": "mes-1"}

    def test_sube_a_la_carpeta_de_anio_y_mes_bajo_la_raiz_indicada(self, monkeypatch):
        visto = {}
        monkeypatch.setattr(drive, "_token", lambda s3=None: "tok")
        monkeypatch.setattr(drive, "asegurar_carpeta", lambda nombre, padre, token: f"{padre}/{nombre}")

        def _sube(nombre, contenido, token, carpeta=None):
            visto["carpeta"] = carpeta
            return "f-1"

        monkeypatch.setattr(drive, "subir_pdf", _sube)
        fecha = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        r = drive.respaldar("1", "COD", "documentos/a.pdf", s3=self._s3(), fecha=fecha, raiz="RAIZ")
        assert visto["carpeta"] == "RAIZ/2026/10 - Octubre"
        assert r["folder_id"] == "RAIZ/2026/10 - Octubre"

    def test_none_y_sin_lanzar_si_drive_falla(self, monkeypatch):
        monkeypatch.setattr(drive, "_token", lambda s3=None: "tok")

        def _falla(*a, **k):
            raise RuntimeError("Drive caido")

        monkeypatch.setattr(drive, "carpeta_destino", _falla)
        assert drive.respaldar("1", "COD", "documentos/a.pdf", s3=self._s3()) is None

    def test_none_si_no_se_puede_leer_de_s3(self):
        s3 = MagicMock()
        s3.get_object.side_effect = RuntimeError("NoSuchKey")
        assert drive.respaldar("1", "COD", "documentos/a.pdf", s3=s3) is None


class TestNombresRuta:
    def test_usa_el_mes_en_hora_de_lima(self):
        # 2026-11-01 02:00 UTC sigue siendo 31 de octubre a las 21:00 en Lima
        fecha = datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)
        assert drive.nombres_ruta(fecha) == ("2026", "10 - Octubre")

    def test_mes_de_un_digito_lleva_cero(self):
        assert drive.nombres_ruta(datetime(2026, 3, 15, 15, 0, tzinfo=timezone.utc)) == ("2026", "03 - Marzo")

    def test_fecha_sin_zona_se_toma_como_de_lima(self):
        assert drive.nombres_ruta(datetime(2026, 12, 31, 23, 30)) == ("2026", "12 - Diciembre")

    def test_sin_fecha_usa_ahora(self):
        anio, mes = drive.nombres_ruta(None)
        assert anio.isdigit()
        assert mes[:2].isdigit()


class TestCarpetas:
    def test_reutiliza_la_carpeta_si_ya_existe(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: _resp({"files": [{"id": "ya-esta"}]}))
        monkeypatch.setattr(drive.requests, "post", lambda *a, **k: pytest.fail("no debe crear otra"))
        assert drive.asegurar_carpeta("2026", "raiz", "tok") == "ya-esta"

    def test_crea_la_carpeta_si_no_existe(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: _resp({"files": []}))
        visto = {}

        def _post(url, params=None, headers=None, json=None, timeout=None):
            visto.update(url=url, json=json)
            return _resp({"id": "nueva"})

        monkeypatch.setattr(drive.requests, "post", _post)
        assert drive.asegurar_carpeta("2026", "raiz", "tok") == "nueva"
        assert visto["json"] == {"name": "2026", "mimeType": drive.MIME_CARPETA, "parents": ["raiz"]}

    def test_carpeta_destino_encadena_anio_y_mes(self, monkeypatch):
        llamadas = []

        def _asegurar(nombre, padre, token):
            llamadas.append((nombre, padre))
            return f"id-{nombre}"

        monkeypatch.setattr(drive, "asegurar_carpeta", _asegurar)
        fecha = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        assert drive.carpeta_destino(fecha, "raiz", "tok") == "id-10 - Octubre"
        assert llamadas == [("2026", "raiz"), ("10 - Octubre", "id-2026")]

    def test_el_nombre_con_comilla_se_escapa_en_la_consulta(self, monkeypatch):
        visto = {}

        def _get(url, params=None, **k):
            visto["q"] = params["q"]
            return _resp({"files": []})

        monkeypatch.setattr(drive.requests, "get", _get)
        drive._buscar_carpeta("O'Hara", "raiz", "tok")
        assert "O\\'Hara" in visto["q"]


ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_-0123"


class TestExtraerFolderId:
    @pytest.mark.parametrize("texto", [
        f"https://drive.google.com/drive/folders/{ID}",
        f"https://drive.google.com/drive/folders/{ID}?usp=sharing",
        f"https://drive.google.com/drive/u/0/folders/{ID}",
        f"https://drive.google.com/drive/folders/{ID}?resourcekey=abc",
        f"https://drive.google.com/open?id={ID}",
        f"  {ID}  ",
    ])
    def test_formatos_validos(self, texto):
        assert drive.extraer_folder_id(texto) == ID

    @pytest.mark.parametrize("texto", [
        "", None, "   ", "hola", "https://evil.com/drive/folders/" + ID,
        "http://drive.google.com/drive/folders/" + ID, "https://drive.google.com/drive/my-drive",
        "https://drive.google.com/file/d/" + ID + "/view", "corto",
    ])
    def test_rechaza_lo_que_no_es_una_carpeta(self, texto):
        with pytest.raises(ValueError):
            drive.extraer_folder_id(texto)

    def test_enlace_carpeta(self):
        assert drive.enlace_carpeta(ID) == f"https://drive.google.com/drive/folders/{ID}"


class TestVerificarCarpeta:
    def _get(self, monkeypatch, status=200, datos=None):
        r = _resp(datos or {}, status)
        r.status_code = status
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: r)

    def test_ok(self, monkeypatch):
        self._get(monkeypatch, datos={"id": ID, "name": "SOFTWARE_BOMBEROS", "mimeType": drive.MIME_CARPETA,
                                      "capabilities": {"canAddChildren": True}})
        assert drive.verificar_carpeta(ID, "tok") == {"id": ID, "nombre": "SOFTWARE_BOMBEROS"}

    @pytest.mark.parametrize("status", [403, 404])
    def test_no_existe_o_no_compartida(self, monkeypatch, status):
        self._get(monkeypatch, status=status)
        with pytest.raises(drive.ErrorCarpeta, match="no está compartida"):
            drive.verificar_carpeta(ID, "tok")

    def test_un_archivo_no_es_carpeta(self, monkeypatch):
        self._get(monkeypatch, datos={"id": ID, "mimeType": "application/pdf", "capabilities": {"canAddChildren": False}})
        with pytest.raises(drive.ErrorCarpeta, match="no corresponde a una carpeta"):
            drive.verificar_carpeta(ID, "tok")

    def test_solo_lectura_pide_compartir_como_editor(self, monkeypatch):
        self._get(monkeypatch, datos={"id": ID, "mimeType": drive.MIME_CARPETA, "capabilities": {"canAddChildren": False}})
        with pytest.raises(drive.ErrorCarpeta, match="Editor"):
            drive.verificar_carpeta(ID, "tok")

    def test_otro_error_http_se_propaga(self, monkeypatch):
        self._get(monkeypatch, status=500)
        with pytest.raises(RuntimeError):
            drive.verificar_carpeta(ID, "tok")


class TestCuentaRespaldo:
    def test_devuelve_el_correo(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: _resp({"user": {"emailAddress": "a@b.com"}}))
        assert drive.cuenta_respaldo("tok") == "a@b.com"

    def test_none_si_falla(self, monkeypatch):
        monkeypatch.setattr(drive.requests, "get", lambda *a, **k: _resp(status=401))
        assert drive.cuenta_respaldo("tok") is None
