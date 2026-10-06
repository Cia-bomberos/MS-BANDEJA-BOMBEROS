"""
Resuelve el ARN del User Pool de MS-SEGURIDAD-BOMBEROS para un stage, para que
el Authorizer de la bandeja valide los tokens del mismo pool con el que el
frontend hace login.

De donde sale cada dato:
- userPoolId: se lee del config.json publico que publica seguridad en su bucket
  de config (bomberos-config-<stage>, uno por dev/qa/uat). Asi cada stage toma
  su propio pool sin tener IDs escritos en el codigo.
- ID de cuenta: ese config.json NO lo trae y el ARN lo necesita. Es la cuenta de
  AWS donde vive seguridad (constante para todos los stages). Se puede cambiar
  con SECURITY_ACCOUNT_ID.

Prioridad:
1. Si USER_POOL_ARN ya esta definido, se usa tal cual.
2. Si no, se arma con el config.json del stage.

Variables opcionales:
    USER_POOL_ARN         ARN completo (gana sobre todo lo demas)
    SECURITY_ACCOUNT_ID   ID de cuenta (12 digitos) donde vive el pool
    SECURITY_CONFIG_URL   URL completa del config.json (si no es la estandar)

Uso:
    export USER_POOL_ARN=$(python3 scripts/resolver_user_pool.py --stage dev)
"""

import argparse
import json
import os
import sys
import urllib.request

REGION = "us-east-1"
DEFAULT_SECURITY_ACCOUNT_ID = "593470088348"


def url_config(stage: str) -> str:
    return os.environ.get("SECURITY_CONFIG_URL") or f"https://bomberos-config-{stage}.s3.amazonaws.com/config.json"


def leer_config(stage: str) -> dict:
    with urllib.request.urlopen(url_config(stage), timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolver_arn(stage: str) -> str:
    arn = os.environ.get("USER_POOL_ARN", "").strip()
    if arn:
        return arn

    config = leer_config(stage)
    pool_id = config.get("userPoolId")
    if not pool_id:
        raise ValueError(f"El config.json de '{stage}' no trae 'userPoolId'.")
    cuenta = os.environ.get("SECURITY_ACCOUNT_ID", DEFAULT_SECURITY_ACCOUNT_ID).strip()
    return f"arn:aws:cognito-idp:{REGION}:{cuenta}:userpool/{pool_id}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["dev", "qa", "uat", "prod"])
    args = parser.parse_args()
    try:
        print(resolver_arn(args.stage))
    except Exception as e:
        print(f"No se pudo resolver el User Pool de seguridad para '{args.stage}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
