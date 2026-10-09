-- Esquema inicial del módulo de Bandeja Documental.
-- Correr una vez por entorno (dev/qa/uat/prod) contra la BD correspondiente
-- dentro del RDS propio del equipo. Idempotente (IF NOT EXISTS) para poder
-- re-correrlo sin romper nada si ya existe.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- para gen_random_uuid()

CREATE TABLE IF NOT EXISTS documentos (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo_unico         VARCHAR(100) UNIQUE,                 -- NULL en modalidad simplificada (RN-0024)
    tipo                 VARCHAR(100),                        -- NULL en modalidad simplificada
    modalidad            VARCHAR(20) NOT NULL
                             CHECK (modalidad IN ('completo', 'simplificado')),
    estado               VARCHAR(20) NOT NULL DEFAULT 'Pendiente'
                             CHECK (estado IN ('Pendiente', 'En proceso', 'Atendido', 'Archivado')),
    prioridad            VARCHAR(10) NOT NULL
                             CHECK (prioridad IN ('Alta', 'Media', 'Baja')),
    prioridad_manual     BOOLEAN NOT NULL DEFAULT false,       -- RN-0018: asignación manual vigente
    seccion_origen       VARCHAR(30) NOT NULL
                             CHECK (seccion_origen IN ('Administracion', 'ServicioGeneral', 'Maquinas', 'Sanidad')),
    seccion_responsable  VARCHAR(30) NOT NULL
                             CHECK (seccion_responsable IN ('Administracion', 'ServicioGeneral', 'Maquinas', 'Sanidad')),
    fecha_limite         DATE NOT NULL,
    archivo_s3_key       VARCHAR(500) NOT NULL,
    confirmado_drive     BOOLEAN NOT NULL DEFAULT false,       -- RN-0027/RNF-0009
    atendido_en          TIMESTAMPTZ,                          -- usado para calcular los 3 días de RN-0026
    fecha_creacion       TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_actualizacion  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS historial_acciones (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_id   UUID NOT NULL REFERENCES documentos(id),
    usuario_sub    VARCHAR(100) NOT NULL,   -- Cognito sub del usuario autenticado
    usuario_nombre VARCHAR(150),
    seccion        VARCHAR(30),
    accion         VARCHAR(50) NOT NULL,
    detalle        TEXT,
    fecha_hora     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Contador atómico de correlativos por tipo de documento + año (RN-0014, RN-0020).
CREATE TABLE IF NOT EXISTS documento_correlativos (
    tipo          VARCHAR(100) NOT NULL,
    anio          INT NOT NULL,
    ultimo_numero INT NOT NULL DEFAULT 0,
    PRIMARY KEY (tipo, anio)
);

CREATE INDEX IF NOT EXISTS idx_documentos_seccion_estado ON documentos(seccion_responsable, estado);
CREATE INDEX IF NOT EXISTS idx_documentos_estado_prioridad ON documentos(estado, prioridad) WHERE prioridad_manual = false;
CREATE INDEX IF NOT EXISTS idx_historial_documento ON historial_acciones(documento_id);

-- Nota de diseño: no existe una tabla local `usuarios` replicada desde Cognito.
-- El historial guarda usuario_sub/usuario_nombre/seccion tal como vienen en los
-- claims del token (denormalizado), ya que MS-SEGURIDAD-BOMBEROS no sincroniza
-- usuarios hacia ningún RDS -- Cognito es la única fuente de verdad de usuarios.
