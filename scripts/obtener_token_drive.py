"""
Genera UNA VEZ las credenciales OAuth para que la bandeja respalde en Google Drive
(modulo_documentos/drive.py). Lo corre en su PC la persona dueña de la cuenta de
Gmail / carpeta de Drive donde se guardarán los respaldos.

Antes, en Google Cloud Console (con esa misma cuenta):
  1. Crear un proyecto y habilitar la "Google Drive API".
  2. "Pantalla de consentimiento de OAuth": tipo Externo; agregar su correo como
     usuario de prueba. IMPORTANTE: luego pasar "Estado de publicación" a
     "En producción" -- en modo "Prueba" Google vence el refresh token a los 7 días
     y el respaldo dejaría de funcionar. Aparecerá la advertencia "app no
     verificada": es normal para uso propio, se acepta con "Avanzado".
  3. "Credenciales" > Crear credenciales > ID de cliente de OAuth > tipo
     "Aplicación de escritorio". Copiar el client_id y el client_secret.

Uso:
    python scripts/obtener_token_drive.py --client-id <ID> --client-secret <SECRET>

Imprime un JSON. Guardarlo como google-oauth.json y subirlo (NO al repo):
    aws s3 cp google-oauth.json s3://bomberos-documentos-<stage>/config/google-oauth.json
"""

import argparse
import http.server
import json
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive"
PUERTO = 8765
REDIRECT = f"http://127.0.0.1:{PUERTO}"


def url_autorizacion(client_id: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",  # necesario para recibir refresh_token
        "prompt": "consent",       # fuerza que Google lo entregue de nuevo
        "state": state,
    })


def canjear_codigo(client_id: str, client_secret: str, code: str) -> dict:
    datos = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=datos), timeout=20) as r:
        return json.loads(r.read())


def esperar_codigo(state: str) -> str:
    resultado = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state or "code" not in q:
                resultado["error"] = q.get("error", ["respuesta invalida"])[0]
            else:
                resultado["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h3>Listo. Puedes cerrar esta pestaña y volver a la terminal.</h3>".encode())

        def log_message(self, *a):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", PUERTO), Handler)
    hilo = threading.Thread(target=servidor.handle_request)
    hilo.start()
    hilo.join(timeout=300)
    servidor.server_close()
    if "code" not in resultado:
        raise SystemExit(f"No se recibió la autorización: {resultado.get('error', 'tiempo agotado')}")
    return resultado["code"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    args = parser.parse_args()

    state = secrets.token_urlsafe(16)
    url = url_autorizacion(args.client_id, state)
    print("Abriendo el navegador para autorizar. Si no se abre, entra a:\n" + url + "\n")
    webbrowser.open(url)

    tokens = canjear_codigo(args.client_id, args.client_secret, esperar_codigo(state))
    if "refresh_token" not in tokens:
        raise SystemExit("Google no devolvió refresh_token. Revoca el acceso previo en "
                         "https://myaccount.google.com/permissions y vuelve a correr el script.")
    print(json.dumps({
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "refresh_token": tokens["refresh_token"],
    }, indent=2))


if __name__ == "__main__":
    main()
