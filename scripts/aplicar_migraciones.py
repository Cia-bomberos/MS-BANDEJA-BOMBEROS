"""
Aplica migrations/*.sql (en orden) contra la base del stage que se está
desplegando. Pensado para correr dentro del pipeline de Jenkins, reusando
las mismas variables de entorno que ya se usan para el deploy (DB_HOST,
DB_PORT, DB_NAME, DB_USER, DB_PASSWORD) -- así no hay que pegar la password
en ningún script local ni en texto plano en ningún lado.

Idempotente: 001_init.sql usa CREATE ... IF NOT EXISTS, así que re-correrlo
en un deploy posterior no rompe nada.

Uso (local, para probar):
    DB_HOST=... DB_PORT=5432 DB_NAME=bomberos_dev DB_USER=bomberosadmin \
    DB_PASSWORD=... python scripts/aplicar_migraciones.py

En Jenkins ya corre así (ver Jenkinsfile, stage "Aplicar Migraciones").
"""

import os
import pathlib
import sys

import psycopg2

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def main():
    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        print("DB_HOST vacío: no hay RDS configurado para este build, se omite.")
        return

    port = int(os.environ.get("DB_PORT", "5432"))
    dbname = os.environ["DB_NAME"]
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]

    archivos = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not archivos:
        print(f"No hay archivos .sql en {MIGRATIONS_DIR}")
        return

    conn = psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password,
        sslmode="require", connect_timeout=10,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for archivo in archivos:
                print(f"Aplicando {archivo.name} en '{dbname}'...")
                cur.execute(archivo.read_text(encoding="utf-8"))
    finally:
        conn.close()

    print(f"Migraciones aplicadas correctamente en '{dbname}'.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR aplicando migraciones: {e}", file=sys.stderr)
        sys.exit(1)