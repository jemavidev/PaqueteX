-- Rol de SOLO LECTURA para el importador espejo v1 → v2
-- (`.scratch/importador-v1-espejo`, ticket 11).
--
-- Se corre UNA vez, a mano, contra la base de producción v1 (`paqueteria_v4`),
-- con el usuario administrador de esa base. Es el único cambio que el
-- importador hace en producción: crea un rol y le da SELECT, no toca datos.
--
-- Uso (desde el servidor v1, `ssh paquetex`, con la contraseña nueva en una
-- variable de psql para que no quede en el historial del shell):
--   psql "$DATABASE_URL_ADMIN" -v clave="'<contraseña-nueva>'" -f rol_solo_lectura.sql
--
-- Para retirarlo tras el corte (cuando se apague la base de la v1):
--   REVOKE ALL ON ALL TABLES IN SCHEMA public FROM paquetex_importador;
--   REVOKE USAGE ON SCHEMA public FROM paquetex_importador;
--   REVOKE CONNECT ON DATABASE paqueteria_v4 FROM paquetex_importador;
--   DROP ROLE paquetex_importador;

CREATE ROLE paquetex_importador WITH LOGIN PASSWORD :clave
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

-- Toda sesión del rol arranca en solo lectura (segunda barrera, además de
-- los permisos: el lector también abre la transacción como READ ONLY).
ALTER ROLE paquetex_importador SET default_transaction_read_only = on;

GRANT CONNECT ON DATABASE paqueteria_v4 TO paquetex_importador;
GRANT USAGE ON SCHEMA public TO paquetex_importador;

-- Solo las tablas que lee `app/domain/importador_v1_lector.py`.
GRANT SELECT ON
    customers,
    users,
    packages,
    package_history,
    package_announcements_new,
    customer_preferences,
    file_uploads
TO paquetex_importador;
