import importlib.util
import io
import json
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "resolver_user_pool", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "resolver_user_pool.py"
)
resolver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(resolver)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(config, visto=None):
    def _open(url, timeout=10):
        if visto is not None:
            visto.append(url)
        return _Resp(json.dumps(config).encode())
    return _open


@pytest.fixture(autouse=True)
def _limpiar_env(monkeypatch):
    for k in ("USER_POOL_ARN", "SECURITY_ACCOUNT_ID", "SECURITY_CONFIG_URL"):
        monkeypatch.delenv(k, raising=False)


def test_arma_arn_con_pool_del_config_y_cuenta_por_defecto(monkeypatch):
    visto = []
    monkeypatch.setattr(resolver.urllib.request, "urlopen", _fake_urlopen({"userPoolId": "us-east-1_ABC"}, visto))
    arn = resolver.resolver_arn("qa")
    assert arn == "arn:aws:cognito-idp:us-east-1:593470088348:userpool/us-east-1_ABC"
    assert visto == ["https://bomberos-config-qa.s3.amazonaws.com/config.json"]


def test_cuenta_se_puede_cambiar(monkeypatch):
    monkeypatch.setenv("SECURITY_ACCOUNT_ID", "111122223333")
    monkeypatch.setattr(resolver.urllib.request, "urlopen", _fake_urlopen({"userPoolId": "us-east-1_X"}))
    assert resolver.resolver_arn("dev").startswith("arn:aws:cognito-idp:us-east-1:111122223333:")


def test_user_pool_arn_explicito_tiene_prioridad(monkeypatch):
    monkeypatch.setenv("USER_POOL_ARN", "arn:aws:cognito-idp:us-east-1:1:userpool/x")
    monkeypatch.setattr(resolver.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no debe leer S3"))
    assert resolver.resolver_arn("dev") == "arn:aws:cognito-idp:us-east-1:1:userpool/x"


def test_config_sin_user_pool_id_falla(monkeypatch):
    monkeypatch.setattr(resolver.urllib.request, "urlopen", _fake_urlopen({"stage": "dev"}))
    with pytest.raises(ValueError):
        resolver.resolver_arn("dev")
