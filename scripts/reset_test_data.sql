-- Reset de datos de prueba (2026-08-19, pedido explícito del cliente) --
-- borra TODO lo que se acumula probando (paquetes, residentes, personas,
-- fotos), preservando lo que es catálogo/configuración real:
--   - usuarios          (staff -- nadie pierde su cuenta)
--   - apartamentos      (catálogo de Torre+Apartamento del conjunto)
--   - configuracion_conjunto / plantillas_notificacion (configuración, no
--     datos de prueba)
--
-- Orden de DELETE respeta las FK reales (ver ForeignKeyConstraint en
-- src/app/domain/*.py): paquete_fotos y persona_preferencia_notificacion
-- referencian paquetes/personas, así que van primero; ocupantes y
-- paquetes referencian personas, así que van antes que personas.
--
-- Uso manual (si se necesita correr de nuevo más adelante, fuera del CI):
--   docker compose exec -T db psql -U paquetex -d paquetex -f - < scripts/reset_test_data.sql
--
-- Ya se corrió una vez contra test.papyrus.com.co (2026-08-19, vía un paso
-- temporal en ci.yml, retirado en el commit siguiente para que no vuelva a
-- correr solo). Validado antes contra la base de desarrollo local: sin
-- errores de FK, deja usuarios/apartamentos intactos y el resto en cero.

DELETE FROM paquete_fotos;
DELETE FROM persona_preferencia_notificacion;
DELETE FROM paquetes;
DELETE FROM ocupantes;
DELETE FROM personas;
DELETE FROM password_resets;
DELETE FROM otps_cliente;
