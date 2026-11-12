-- Migración sobre el esquema 001_base.sql para diferenciar recarga/completo/vacío.
BEGIN;
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS sale_type text NOT NULL DEFAULT 'recarga';
ALTER TABLE order_items DROP CONSTRAINT IF EXISTS order_items_sale_type_check;
ALTER TABLE order_items ADD CONSTRAINT order_items_sale_type_check CHECK (sale_type IN ('recarga','completo','vacio'));
COMMIT;
