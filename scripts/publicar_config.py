"""
Publica la URL del API de este stage en un bucket de config propio de
bandeja, para que el frontend tenga siempre el dato actualizado sin
importar cuándo rotaron las credenciales de AWS Academy.

NOTA: `bomberos-config-<stage>` es del PM y vive en su cuenta (ahí seguridad
publica su config.json); desde otra cuenta da 403. Por eso este script publica
en un bucket propio de bandeja, con lectura pública solo para esta key. Si se
desplegara desde la cuenta del PM (Jenkins), podría escribir en el suyo.

Se corre después de `serverless deploy` (ver Jenkinsfile).

Uso:
    python scripts/publicar_config.py --stage dev
"""

import argparse
import json

import boto3

from verificar_dependencias import CONFIG_BUCKETS_PREFIX

SERVICE_NAME = "bomberos-f3-bandeja"
CONFIG_KEY = "bandeja-config.json"


def obtener_outputs(stage: str) -> dict:
    stack_name = f"{SERVICE_NAME}-{stage}"
    cf = boto3.client("cloudformation")
    resp = cf.describe_stacks(StackName=stack_name)
    outputs = resp["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["dev", "qa", "uat", "prod"])
    args = parser.parse_args()

    outputs = obtener_outputs(args.stage)
    api_url = outputs.get("ServiceEndpoint")
    documents_bucket = outputs.get("DocumentsBucketName")

    if not api_url:
        raise SystemExit(f"No se encontró el output 'ServiceEndpoint' en el stack {SERVICE_NAME}-{args.stage}.")

    config = {
        "stage": args.stage,
        "apiUrl": api_url,
        "documentsBucket": documents_bucket,
    }

    config_bucket = f"{CONFIG_BUCKETS_PREFIX}-{args.stage}"
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=config_bucket,
        Key=CONFIG_KEY,
        Body=json.dumps(config).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"Publicado s3://{config_bucket}/{CONFIG_KEY}: {config}")


if __name__ == "__main__":
    main()
