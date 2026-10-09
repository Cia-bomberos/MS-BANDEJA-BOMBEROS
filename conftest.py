import os

# Variables de entorno requeridas por modulo_documentos/db.py y s3util.py al
# importarse. Se fijan aquí para que existan antes de que cualquier test
# importe esos módulos.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "bandeja_test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DOCUMENTS_BUCKET", "bomberos-documentos-test")
os.environ.setdefault("DOCUMENTS_BUCKET_OWNER", "123456789012")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
