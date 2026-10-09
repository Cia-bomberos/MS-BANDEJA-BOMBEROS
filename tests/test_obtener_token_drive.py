import importlib.util
import json
import pathlib
from urllib.parse import parse_qs, urlparse

_SPEC = importlib.util.spec_from_file_location(
    "obtener_token_drive", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "obtener_token_drive.py"
)
otd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(otd)


def test_url_pide_acceso_offline_y_scope_drive():
    q = parse_qs(urlparse(otd.url_autorizacion("cid", "estado-1")).query)
    assert q["client_id"] == ["cid"]
    assert q["access_type"] == ["offline"] 
    assert q["prompt"] == ["consent"]
    assert q["scope"] == ["https://www.googleapis.com/auth/drive"]
    assert q["state"] == ["estado-1"]
    assert q["redirect_uri"][0].startswith("http://127.0.0.1:")


def test_canjear_codigo_envia_los_datos_correctos(monkeypatch):
    visto = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"refresh_token": "r", "access_token": "a"}).encode()

    def _open(req, timeout=None):
        visto["url"], visto["body"] = req.full_url, parse_qs(req.data.decode())
        return _R()

    monkeypatch.setattr(otd.urllib.request, "urlopen", _open)
    assert otd.canjear_codigo("cid", "sec", "codigo-1")["refresh_token"] == "r"
    assert visto["url"] == otd.TOKEN_URL
    assert visto["body"]["grant_type"] == ["authorization_code"]
    assert visto["body"]["code"] == ["codigo-1"]
