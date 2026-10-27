-- Respaldo en Google Drive por año/mes y carpeta configurable desde el front.
-- Idempotente: scripts/aplicar_migraciones.py corre todas las migraciones en cada deploy.

-- Dónde quedó cada documento en Drive (se llena al confirmarse el respaldo, RN-0027).
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS drive_file_id   VARCHAR(100);
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS drive_folder_id VARCHAR(100);
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS respaldado_en   TIMESTAMPTZ;

-- Configuración clave/valor editable por el Jefe de Administración.
-- Clave 'drive': {"folder_id": "...", "nombre": "..."} = carpeta raíz del respaldo.
CREATE TABLE IF NOT EXISTS configuracion (
    clave              VARCHAR(50) PRIMARY KEY,
    valor              JSONB NOT NULL,
    actualizado_por    VARCHAR(100),   -- Cognito sub de quien la cambió
    actualizado_nombre VARCHAR(150),
    actualizado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);
