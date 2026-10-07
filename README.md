# MS-BANDEJA-BOMBEROS
## Módulo de Bandeja Documental

Implementa el ciclo de vida de documentos descrito en el Documento de Análisis
y Diseño (RN-0004 a RN-0028, RF-0001 a RF-0009, RNF-0006/0007/0009). Serverless
(Lambda + API Gateway + Postgres en el RDS propio del equipo), reutilizando el
mismo Cognito User Pool que `MS-SEGURIDAD-BOMBEROS` para autenticación y roles.

### Infraestructura

- **13 Lambdas** expuestos vía API Gateway, protegidos por el mismo Authorizer
  de Cognito que seguridad (cross-stack reference a su User Pool).
- **RDS propio del equipo**: 2 tablas nuevas (`documentos`, `historial_acciones`)
  más una tabla auxiliar (`documento_correlativos`) para la numeración atómica.
  Ver `migrations/001_init.sql` — correr una vez por entorno (dev/qa/uat/prod)
  antes del primer deploy.
- **Bucket S3 propio** (`bomberos-documentos-<stage>`) para los PDFs, privado,
  acceso solo vía URLs pre-firmadas (subida y descarga).
- **2 jobs programados** (cada 1h): reclasificación de prioridades (RN-0017) y
  archivado automático tras 3 días en "Atendido" (RN-0026).

### Dependencia con `MS-SEGURIDAD-BOMBEROS`

`serverless.yml` referencia el `UserPoolId` del stack de seguridad vía
cross-stack reference (`${cf:bomberos-f3-backend-<stage>.UserPoolId}`). Esto
significa que **seguridad tiene que estar deployado en un stage antes de
poder desplegar bandeja en ese mismo stage** — si no, el deploy falla.
`scripts/verificar_dependencias.py` corre automáticamente antes de cada
deploy (ver Jenkinsfile) y lo detecta temprano, con un mensaje claro en vez
de un error críptico de CloudFormation. Ese mismo script también crea el
bucket de config (`bomberos-config-<stage>`) si todavía no existe.

**User Pool en otra cuenta de AWS.** El pool de seguridad vive en la cuenta del
PM (cada integrante de AWS Academy tiene su propio sandbox). El ARN que necesita
el authorizer se arma con `scripts/resolver_user_pool.py`:

- `userPoolId`: se lee del `config.json` publico de seguridad
  (`https://bomberos-config-<stage>.s3.amazonaws.com/config.json`), asi cada
  stage (dev/qa/uat) toma su pool sin IDs escritos en el codigo.
- ID de cuenta: el config no lo trae. Es una constante en el script
  (`DEFAULT_SECURITY_ACCOUNT_ID`), sobrescribible con `SECURITY_ACCOUNT_ID`.
- `USER_POOL_ARN` explicito gana sobre todo lo anterior.

Deploy manual (CloudShell):

```
export USER_POOL_ARN=$(python3 scripts/resolver_user_pool.py --stage dev)
npx serverless@3 deploy --stage dev
```

El Jenkinsfile hace lo mismo antes de cada deploy. Si no se puede resolver, el
deploy cae al pool del stack de seguridad de la misma cuenta.

### `psycopg2` en Lambda

`psycopg2` tiene una extensión en C y no es portable tal cual al runtime de
Lambda (necesita compilarse para Amazon Linux). Se resolvió con el plugin
`serverless-python-requirements` (ver `serverless.yml` y `requirements.txt`,
separado de `requirements-dev.txt` que es solo para tests). No usa
`dockerizePip` porque el agente de Jenkins que corre el deploy ya es Linux
x86_64 — la misma plataforma del runtime de Lambda — así que el wheel
manylinux que descarga pip ahi mismo ya es compatible, sin necesitar
Docker-in-Docker.

### Resiliencia a la rotación de credenciales de AWS Academy

Después de cada deploy, `scripts/publicar_config.py` lee el `ServiceEndpoint`
del stack y lo sube a un bucket de config propio de bandeja
(`bomberos-f3-bandeja-config-<stage>`), bajo la key `bandeja-config.json`.

**Aclaración sobre el bucket de config.** El bucket `bomberos-config-<stage>`
es del PM y vive en SU cuenta de AWS (ahí seguridad publica su `config.json`).
Desde una cuenta distinta recibe 403 al escribir, así que bandeja publica en un
bucket propio (`bomberos-f3-bandeja-config-<stage>`) con lectura pública solo
para `bandeja-config.json`. Si se quiere unificar, el PM puede dar permiso de
`s3:PutObject` sobre esa key a la cuenta que despliega, o el deploy puede correr
desde su cuenta (Jenkins) y escribir ahí directamente.

### Endpoints

El diseño de endpoints no está fijado en el documento de análisis; esta es la
superficie elegida:

| Método | Ruta | RN/RF relevantes |
|---|---|---|
| POST | `/documentos/upload-url` | RNF-0006 (URL pre-firmada para subir el PDF) |
| POST | `/documentos` | RN-0007, 0013, 0014, 0020, 0024, 0025 / RF-0004, 0008 |
| GET | `/documentos` | RN-0004, 0005, 0006, 0019 / RF-0002 |
| GET | `/documentos/{id}` | RN-0006, 0012 / RF-0003 |
| PATCH | `/documentos/{id}/derivar` | RN-0009, 0015 / RF-0004 |
| PATCH | `/documentos/{id}/archivo` | RN-0010 |
| PATCH | `/documentos/{id}/atender` | RN-0008, 0011 |
| PATCH | `/documentos/{id}/prioridad` | RN-0018 / RF-0005 |
| POST | `/documentos/{id}/envio-externo` | RN-0021, 0022 / RF-0007 |
| GET | `/documentos/{id}/descargar` | RN-0023 |
| DELETE | `/documentos/{id}` | RN-0028 / RF-0009 |

Autorización (`modulo_documentos/auth.py`): Jefatura lee/escribe todas las
secciones; Jefe_Administracion lee todas pero solo escribe Administración (y
es el único que puede eliminar documentos archivados); cada otro Jefe de
Sección solo lee/escribe su propia sección.

### Respaldo en Google Drive (RN-0027 / RF-0009)

Al archivarse (3 días después de "Atendido"), el PDF se copia a una carpeta de
Google Drive de la Compañía (`modulo_documentos/drive.py`, job `archivarDocumentos`).
Solo cuando Drive confirma el archivo se marca `confirmado_drive = true`, que es
la condición de RN-0028 para poder eliminar un documento.

- Si Drive falla o no está configurado, el documento se archiva igual (RN-0026) con
  `confirmado_drive = false`, y **cada corrida del job lo reintenta** hasta que Drive
  lo confirme. Un reintento no duplica archivos (se busca por nombre antes de subir).
- Nombre en Drive: el código único (`OFICIO N° 001-2026-...pdf`) o el id del documento.
- Timeout de 20 s por llamada a Google (RNF-0009); el job no se cuelga por Drive.

**Por qué OAuth y no cuenta de servicio.** La carpeta de respaldo está en un Gmail
personal. Una cuenta de servicio no tiene almacenamiento propio y en "Mi unidad"
falla con `storageQuotaExceeded`; con OAuth los archivos quedan a nombre de la
persona que autorizó y cuentan para su espacio.

**Configuración (una vez; la hace el dueño de la cuenta de Gmail)**

1. Crear una carpeta en su Drive para los respaldos. Su ID es el último tramo de la
   URL (`drive.google.com/drive/folders/<ID>`).
2. En Google Cloud Console (misma cuenta): crear un proyecto, habilitar la
   *Google Drive API*, configurar la pantalla de consentimiento (tipo Externo) y
   crear un ID de cliente OAuth de tipo *Aplicación de escritorio*.
   **Pasar el estado de publicación a "En producción"**: en modo "Prueba" Google
   vence el token a los 7 días y el respaldo dejaría de funcionar sin avisar.
   (La advertencia de "app no verificada" es normal en uso propio.)
3. Correr `python scripts/obtener_token_drive.py --client-id <ID> --client-secret <SECRET>`
   en su PC, autorizar en el navegador y guardar el JSON que imprime como
   `google-oauth.json`.
4. Subirlo al bucket privado (no al repo):
   `aws s3 cp google-oauth.json s3://bomberos-documentos-<stage>/config/google-oauth.json`
5. Desplegar con `DRIVE_FOLDER_ID=<ID de la carpeta>` (variable de entorno, o el
   parámetro del Jenkinsfile del mismo nombre).

Si el token se revoca o vence, el job registra el error en CloudWatch, no confirma
nada y reintenta en cada corrida; basta repetir los pasos 3 y 4. Credenciales en S3
y no en variables de entorno porque Lambda limita todas las variables a 4 KB.
**Nunca commitear `google-oauth.json`.**

### Tests

111 tests, 95% de cobertura sobre `modulo_documentos/` (100% en `auth`, `codigo`,
`prioridad`, `s3util`, `drive` y `scheduled`; ~92% en `handler`). La BD, S3 y Google Drive se
mockean: los tests no tocan servicios reales. Ejecutar con
`pytest tests/ --cov=modulo_documentos`.

### Pendiente

1. **Credenciales de Google Drive.** El código está implementado y probado con mocks,
   pero falta que el dueño del Gmail genere las credenciales OAuth y entregue el ID de
   carpeta (ver arriba); sin eso no se respalda nada y ningún archivado puede eliminarse (RN-0028). La subida
   real contra Google no se ha podido probar de punta a punta.
2. **No hay tabla `usuarios` en el RDS.** El historial guarda `usuario_sub` /
   `usuario_nombre` / `seccion` tal como vienen del token de Cognito
   (denormalizado), porque no existe ningún mecanismo que sincronice usuarios
   de Cognito hacia RDS. Si se necesita hacer JOIN contra usuarios reales, hay
   que decidir primero cómo sincronizarlos.
