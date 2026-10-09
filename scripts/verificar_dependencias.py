"""
Corre ANTES del deploy de MS-BANDEJA-BOMBEROS para detectar temprano (con un
mensaje claro) los dos problemas de infraestructura compartida que, si no se
cumplen, hacen fallar el `serverless deploy` con un error críptico de
CloudFormation:

1. El stack de MS-SEGURIDAD-BOMBEROS para este mismo stage tiene que existir,
   porque serverless.yml referencia su UserPoolId vía ${cf:...} (cross-stack
   reference). Si no existe, CloudFormation falla al resolver el export.
2. El bucket de config propio de bandeja (bomberos-f3-bandeja-config-<stage>)
   tiene que existir para que scripts/publicar_config.py pueda escribir ahi
   al final del deploy. Este script lo crea si falta.

   NOTA: `bomberos-config-<stage>` es del PM (otra cuenta), por eso se usa un
   bucket propio con prefijo del servicio. Los nombres de bucket S3 son únicos
   a nivel global.

Uso:
    python scripts/verificar_dependencias.py --stage dev
"""

import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

from resolver_user_pool import resolver_arn

SECURITY_SERVICE_NAME = "bomberos-f3-backend"
CONFIG_BUCKET_PREFIX = "bomberos-f3-bandeja-cfg"
REGION = "us-east-1"


def verificar_stack_seguridad(stage: str):
    stack_name = f"{SECURITY_SERVICE_NAME}-{stage}"
    cf = boto3.client("cloudformation", region_name=REGION)
    try:
        resp = cf.describe_stacks(StackName=stack_name)
    except ClientError as e:
        if "does not exist" in str(e):
            raise SystemExit(
                f"\nFALTA UNA DEPENDENCIA: el stack '{stack_name}' (MS-SEGURIDAD-BOMBEROS) "
                f"no existe todavia.\n"
                f"serverless.yml de este modulo referencia su UserPoolId (cross-stack "
                f"reference), asi que bandeja NO SE PUEDE DESPLEGAR en el stage '{stage}' "
                f"hasta que seguridad este deployado ahi primero.\n"
                f"Coordinar con quien maneja MS-SEGURIDAD-BOMBEROS antes de reintentar.\n"
            )
        raise

    outputs = {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0].get("Outputs", [])}
    if "UserPoolId" not in outputs:
        raise SystemExit(
            f"\nFALTA UNA DEPENDENCIA: el stack '{stack_name}' existe pero no tiene un "
            f"output 'UserPoolId'. Revisar que MS-SEGURIDAD-BOMBEROS lo este exportando.\n"
        )
    print(f"OK: '{stack_name}' existe y expone UserPoolId.")


CONFIG_KEY = "bandeja-config.json"


def abrir_lectura_publica_config(s3, bucket: str):
    """El frontend lee bandeja-config.json desde el navegador sin credenciales
    (lib/config-remota.ts), igual que el config.json de seguridad. Deja publico
    SOLO esa key (el resto del bucket sigue privado) y habilita CORS de lectura.
    Es idempotente. No tiene secretos: solo apiUrl y el nombre del bucket de PDFs."""
    try:
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": False,
                "IgnorePublicAcls": False,
                "BlockPublicPolicy": False,
                "RestrictPublicBuckets": False,
            },
        )
        s3.put_bucket_policy(
            Bucket=bucket,
            Policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket}/{CONFIG_KEY}",
                }],
            }),
        )
        s3.put_bucket_cors(
            Bucket=bucket,
            CORSConfiguration={"CORSRules": [{
                "AllowedMethods": ["GET"],
                "AllowedOrigins": ["*"],
                "AllowedHeaders": ["*"],
            }]},
        )
        print(f"OK: '{CONFIG_KEY}' de '{bucket}' queda de lectura publica (resto privado).")
    except ClientError as e:
        # No frena el deploy: la API funciona igual; solo el front no podria leer la config.
        print(f"AVISO: no se pudo abrir la lectura publica de '{bucket}': {e}")


def asegurar_bucket_config(stage: str):
    bucket = f"{CONFIG_BUCKET_PREFIX}-{stage}"
    s3 = boto3.client("s3", region_name=REGION)
    existe = True
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"OK: el bucket de config '{bucket}' ya existe.")
    except ClientError as e:
        codigo = e.response["Error"]["Code"]
        if codigo in ("403", "AccessDenied", "Forbidden"):
            raise SystemExit(
                f"\nEl bucket '{bucket}' existe pero pertenece a otra cuenta de AWS "
            )
        if codigo not in ("404", "NoSuchBucket"):
            raise
        existe = False

    if not existe:
        print(f"El bucket de config '{bucket}' no existe, lo creo...")
        # us-east-1 es un caso especial en la API de S3: no acepta
        # CreateBucketConfiguration (a diferencia del resto de regiones).
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        print(f"OK: bucket '{bucket}' creado.")

    abrir_lectura_publica_config(s3, bucket)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["dev", "qa", "uat", "prod"])
    args = parser.parse_args()

    try:
        pool_arn = resolver_arn(args.stage)
    except Exception as e:
        pool_arn = None
        print(f"Aviso: no se pudo resolver el User Pool desde el config de seguridad ({e}). "
              f"Se intenta con el stack de seguridad de esta misma cuenta.")
    if pool_arn:
        print(f"OK: el authorizer usara el User Pool {pool_arn} (se omite el check del stack local).")
    else:
        verificar_stack_seguridad(args.stage)
    asegurar_bucket_config(args.stage)
    print("\nDependencias OK, se puede desplegar.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(1)
