"""
Sirve el Swagger UI (/docs) y el spec OpenAPI (/openapi.json) para la API.
El JSON se genera desde zap/openapi.yaml y se empaqueta en swagger-ui/openapi.json.
"""

import json
import os


def servir_docs(event, context):
    path = event.get("path", "")

    if path.endswith("/docs"):
        ruta_html = os.path.join(os.path.dirname(__file__), "..", "swagger-ui", "index.html")
        with open(ruta_html, "r", encoding="utf-8") as f:
            html = f.read()
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html,
        }

    if path.endswith("/openapi.json"):
        ruta_json = os.path.join(os.path.dirname(__file__), "..", "swagger-ui", "openapi.json")
        with open(ruta_json, "r", encoding="utf-8") as f:
            spec = json.load(f)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(spec, ensure_ascii=False),
        }

    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "Ruta no encontrada."}),
    }