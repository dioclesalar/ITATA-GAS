-- Clasificación de clientes por sucursal (sin asignación automática de registros históricos).
BEGIN;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS branch_id uuid REFERENCES branches(id);
CREATE INDEX IF NOT EXISTS customers_branch_name_idx ON customers(branch_id, name);
COMMIT;
-- Los clientes previos con branch_id NULL requieren clasificación manual por el GG.
