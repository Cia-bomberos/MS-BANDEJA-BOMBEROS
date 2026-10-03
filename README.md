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

### Resiliencia a la rotación de credenciales de AWS Academy

Igual que hizo el PM en `MS-SEGURIDAD-BOMBEROS`: después de cada deploy,
`scripts/publicar_config.py` lee el `ServiceEndpoint` del stack y lo sube al
**mismo bucket de config** que ya usa seguridad (`bomberos-config-<stage>`),
bajo la key `bandeja-config.json` (no pisa el `config.json` de seguridad). El
frontend lee esa key para tener siempre la URL del API actualizada, sin
depender de que nadie le avise cuándo rotaron las credenciales del Learner Lab.

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

### Pendiente — no incluido en esta primera versión

1. **Integración real con Google Drive (RN-0027/RF-0009).** No existe todavía
   una cuenta de servicio de Google Cloud ni el ID de la carpeta de la
   Compañía. `modulo_documentos/scheduled.py::_subir_a_drive` es un stub que
   **nunca marca `confirmado_drive = true`** (por diseño: mejor que el job se
   reintente solo en la próxima corrida a que falsee una subida que no pasó).
   Hasta que esto se implemente, ningún documento "Archivado" va a poder
   eliminarse (RN-0028 lo exige explícitamente).
2. **Lambda Layer para `psycopg2`.** El `requirements-dev.txt` trae
   `psycopg2-binary` para correr los tests localmente, pero ese paquete no es
   portable tal cual a Lambda (necesita compilarse para Amazon Linux). Antes
   del primer deploy real hay que agregar el plugin
   `serverless-python-requirements` con `dockerizePip: true`, o un Lambda
   Layer con `psycopg2` precompilado — si no, los Lambdas van a fallar en
   runtime con un error de import, no en el deploy.
3. **No hay tabla `usuarios` en el RDS.** El historial guarda `usuario_sub` /
   `usuario_nombre` / `seccion` tal como vienen del token de Cognito
   (denormalizado), porque no existe ningún mecanismo que sincronice usuarios
   de Cognito hacia RDS (seguridad tampoco lo tiene). Si en algún momento se
   necesita hacer JOIN contra una tabla de usuarios real, hay que decidir
   cómo sincronizarla primero.
4. **Tests cubren el flujo principal, no cada combinación.** 42 tests, 100%
   en `auth.py`/`codigo.py`/`prioridad.py` (la lógica pura), ~45% en
   `handler.py` (los casos más importantes de cada endpoint). `scheduled.py`
   no tiene tests todavía (requiere mockear más de la BD con fechas/intervalos).
