"""Tests de modulo_documentos/docs.py (Swagger UI + OpenAPI)."""
import json
from unittest.mock import mock_open, patch

from modulo_documentos import docs


def test_servir_docs_devuelve_html(monkeypatch):
    monkeypatch.setattr("builtins.open", mock_open(read_data="<html>swagger</html>"))
    resp = docs.servir_docs({"path": "/docs"}, None)
    assert resp["statusCode"] == 200
    assert "text/html" in resp["headers"]["Content-Type"]
    assert "<html>swagger</html>" in resp["body"]


def test_servir_openapi_json(monkeypatch):
    spec = {"openapi": "3.0.0", "info": {"title": "Bandeja"}}
    monkeypatch.setattr("builtins.open", mock_open(read_data=json.dumps(spec)))
    resp = docs.servir_docs({"path": "/openapi.json"}, None)
    assert resp["statusCode"] == 200
    assert "application/json" in resp["headers"]["Content-Type"]
    assert json.loads(resp["body"]) == spec


def test_ruta_desconocida_da_404():
    resp = docs.servir_docs({"path": "/otra"}, None)
    assert resp["statusCode"] == 404