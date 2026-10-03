"""
Publica la URL del API de este stage en el mismo bucket de config que ya
usa MS-SEGURIDAD-BOMBEROS (bomberos-config-<stage>), para que el frontend
tenga siempre el dato actualizado sin importar cuándo rotaron las
credenciales de AWS Academy -- mismo mecanismo que resolvió el PM para
seguridad, reutilizado acá para no duplicar infraestructura.

Se corre después de `serverless deploy` (ver Jenkinsfile).

Uso:
    python scripts/publicar_config.py --stage dev
"""

import argparse
import json

import boto3

SERVICE_NAME = "bomberos-f3-bandeja"
CONFIG_KEY = "bandeja-config.json"  # key separada de config.json (seguridad), mismo bucket


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

    config_bucket = f"bomberos-config-{args.stage}"
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
