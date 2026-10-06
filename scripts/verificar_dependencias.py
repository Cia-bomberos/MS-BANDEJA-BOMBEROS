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

   NOTA (2026-10-03): el nombre tiene el prefijo del servicio porque los
   nombres de bucket S3 son unicos a nivel GLOBAL (entre cuentas de AWS, no
   solo dentro de este proyecto). Un nombre generico como
   "bomberos-config-<stage>" ya estaba tomado por una cuenta ajena al curso.
   Ademas, se confirmo que MS-SEGURIDAD-BOMBEROS (repo real:
   Cia-Bomberos---Backend) no tiene ningun mecanismo de config en S3 -- la
   idea original de "reusar su bucket" no aplicaba. Pendiente: confirmar
   con el equipo de frontend como lee hoy la URL del API.

Uso:
    python scripts/verificar_dependencias.py --stage dev
"""

import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

SECURITY_SERVICE_NAME = "bomberos-f3-backend"
CONFIG_BUCKET_PREFIX = "bomberos-f3-bandeja-config"
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


def asegurar_bucket_config(stage: str):
    bucket = f"{CONFIG_BUCKET_PREFIX}-{stage}"
    s3 = boto3.client("s3", region_name=REGION)
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"OK: el bucket de config '{bucket}' ya existe.")
        return
    except ClientError as e:
        codigo = e.response["Error"]["Code"]
        if codigo not in ("404", "NoSuchBucket"):
            raise

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["dev", "qa", "uat", "prod"])
    args = parser.parse_args()

    pool_arn = os.environ.get("USER_POOL_ARN", "").strip()
    if pool_arn:
        print(f"OK: se usara el User Pool externo {pool_arn} (se omite el check del stack local de seguridad).")
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
