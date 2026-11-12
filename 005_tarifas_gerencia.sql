BEGIN;
CREATE TABLE IF NOT EXISTS price_tariffs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 branch_id uuid NOT NULL REFERENCES branches(id),
 product_id uuid NOT NULL REFERENCES products(id),
 sale_type varchar(16) NOT NULL CHECK(sale_type IN ('recarga','completo','vacio')),
 price_clp integer NOT NULL CHECK(price_clp>=0),
 UNIQUE(branch_id,product_id,sale_type)
);
COMMIT;
