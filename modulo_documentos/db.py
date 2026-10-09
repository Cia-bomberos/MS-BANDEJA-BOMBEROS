"""
Conexión a la BD (RDS propio del equipo).

Credenciales vía variables de entorno (mismo patrón acordado para el .env
que se le pasa a Nico/Jenkins): DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
"""

import os

import psycopg2
import psycopg2.extras


def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=5,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def siguiente_correlativo(conn, tipo: str, anio: int) -> int:
    """Asigna atómicamente el siguiente número correlativo para (tipo, año).

    RN-0014 / RN-0020: el correlativo se reinicia a 001 cada año y la
    operación debe ser indivisible para que dos registros simultáneos
    nunca obtengan el mismo número. El UPSERT de Postgres resuelve esto
    sin necesidad de un lock explícito.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documento_correlativos (tipo, anio, ultimo_numero)
            VALUES (%s, %s, 1)
            ON CONFLICT (tipo, anio)
            DO UPDATE SET ultimo_numero = documento_correlativos.ultimo_numero + 1
            RETURNING ultimo_numero
            """,
            (tipo, anio),
        )
        return cur.fetchone()["ultimo_numero"]
