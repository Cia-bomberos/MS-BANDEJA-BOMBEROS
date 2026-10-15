import json
import os
import pathlib
import uuid
import io

import boto3
import psycopg2
import psycopg2.extras
import pytest
from moto import mock_aws
from pypdf import PdfWriter

from modulo_documentos import s3util

RAIZ = pathlib.Path(__file__).resolve().parents[2]
BUCKET = "bucket-integracion"
OWNER = "123456789012"


@pytest.fixture(scope="session")
def bd_config():
    host = os.environ.get("TEST_DB_HOST")
    if not host:
        pytest.skip("TEST_DB_HOST no definido: se omiten las pruebas de integracion")
    cfg = {
        "host": host,
        "port": int(os.environ.get("TEST_DB_PORT", "5432")),
        "dbname": os.environ.get("TEST_DB_NAME", "bandeja_test"),
        "user": os.environ.get("TEST_DB_USER", "postgres"),
        "password": os.environ.get("TEST_DB_PASSWORD", ""),
    }
    # Salvaguarda: estas pruebas borran el schema. Nunca contra la base real.
    if "test" not in cfg["dbname"]:
        pytest.fail(f"TEST_DB_NAME debe contener 'test' (recibido: {cfg['dbname']}).")
    return cfg


@pytest.fixture(scope="session")
def bd_migrada(bd_config):
    conn = psycopg2.connect(**bd_config, connect_timeout=10)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for archivo in sorted((RAIZ / "migrations").glob("*.sql")):
            cur.execute(archivo.read_text(encoding="utf-8"))
    conn.close()


@pytest.fixture
def bd(bd_config, bd_migrada, monkeypatch):
    """Conexion de apoyo para preparar y verificar datos; deja las tablas vacias
    y apunta los handlers (que leen DB_*) a la base de prueba."""
    monkeypatch.setenv("DB_HOST", bd_config["host"])
    monkeypatch.setenv("DB_PORT", str(bd_config["port"]))
    monkeypatch.setenv("DB_NAME", bd_config["dbname"])
    monkeypatch.setenv("DB_USER", bd_config["user"])
    monkeypatch.setenv("DB_PASSWORD", bd_config["password"])
    monkeypatch.setenv("DB_SSLMODE", "disable")  # ver nota sobre db.py

    conn = psycopg2.connect(**bd_config)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("""
            DO $$ DECLARE r record; BEGIN
              FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE format('TRUNCATE TABLE %I RESTART IDENTITY CASCADE', r.tablename);
              END LOOP;
            END $$;
        """)
    yield conn
    conn.close()


@pytest.fixture
def consulta(bd):
    def _q(sql, params=()):
        with bd.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []
    return _q


@pytest.fixture
def s3_falso(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("DOCUMENTS_BUCKET", BUCKET)
    monkeypatch.setenv("DOCUMENTS_BUCKET_OWNER", OWNER)
    with mock_aws():
        cliente = boto3.client("s3", region_name="us-east-1")
        cliente.create_bucket(Bucket=BUCKET)
        monkeypatch.setattr(s3util, "s3", cliente)  # el modulo crea su cliente al importarse
        yield cliente


def _pdf_real() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture
def subir(s3_falso):
    """Sube un archivo a pendientes/ como lo haria el cliente con la URL pre-firmada."""
    def _subir(contenido: bytes | None = None) -> str:
        key = f"pendientes/{uuid.uuid4()}.pdf"
        s3_falso.put_object(Bucket=BUCKET, Key=key, Body=contenido or _pdf_real())
        return key
    return _subir


@pytest.fixture
def llamar():
    """Invoca un handler como API Gateway y devuelve (status, body)."""
    def _llamar(funcion, grupo, *, id=None, body=None, query=None, sub="u-1"):
        evento = {"requestContext": {"authorizer": {"claims": {
            "cognito:groups": grupo, "sub": sub, "name": "Tester"}}}}
        if id:
            evento["pathParameters"] = {"id": id}
        if body is not None:
            evento["body"] = json.dumps(body)
        if query:
            evento["queryStringParameters"] = query
        r = funcion(evento, None)
        return r["statusCode"], json.loads(r["body"])
    return _llamar