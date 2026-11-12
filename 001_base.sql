-- ITATA GAS: esquema inicial PostgreSQL (MVP)
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE branches (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, address text, active boolean NOT NULL DEFAULT true);
CREATE TABLE app_users (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), branch_id uuid REFERENCES branches(id), full_name text NOT NULL, email text NOT NULL UNIQUE, password_hash text NOT NULL, role text NOT NULL CHECK(role IN ('gerente_general','administrativo','responsable_sucursal','conductor')), active boolean NOT NULL DEFAULT true);
CREATE TABLE customers (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, phone text, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE customer_addresses (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), customer_id uuid NOT NULL REFERENCES customers(id), address text NOT NULL, commune text, instructions text);
CREATE TABLE products (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), brand text NOT NULL, capacity_kg numeric(6,2) NOT NULL CHECK(capacity_kg>0), UNIQUE(brand,capacity_kg));
CREATE TABLE inventory (branch_id uuid NOT NULL REFERENCES branches(id), product_id uuid NOT NULL REFERENCES products(id), status text NOT NULL CHECK(status IN ('lleno','vacio','danado','revision','fuera_servicio')), quantity integer NOT NULL DEFAULT 0 CHECK(quantity>=0), PRIMARY KEY(branch_id,product_id,status));
CREATE TABLE orders (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), branch_id uuid NOT NULL REFERENCES branches(id), customer_id uuid NOT NULL REFERENCES customers(id), address_id uuid NOT NULL REFERENCES customer_addresses(id), status text NOT NULL DEFAULT 'pendiente' CHECK(status IN ('pendiente','asignado','en_ruta','entregado','cancelado')), scheduled_at timestamptz, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE order_items (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), order_id uuid NOT NULL REFERENCES orders(id), product_id uuid NOT NULL REFERENCES products(id), quantity integer NOT NULL CHECK(quantity>0), unit_price_clp integer NOT NULL CHECK(unit_price_clp>=0));
CREATE TABLE vehicles (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), branch_id uuid REFERENCES branches(id), plate text UNIQUE, description text, active boolean NOT NULL DEFAULT true);
CREATE TABLE deliveries (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), order_id uuid NOT NULL UNIQUE REFERENCES orders(id), driver_id uuid NOT NULL REFERENCES app_users(id), vehicle_id uuid REFERENCES vehicles(id), assigned_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz);
CREATE TABLE payments (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), order_id uuid NOT NULL REFERENCES orders(id), amount_clp integer NOT NULL CHECK(amount_clp>0), method text NOT NULL CHECK(method IN ('efectivo','transferencia','tarjeta','otro')), received_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE cash_sessions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), branch_id uuid NOT NULL REFERENCES branches(id), opened_by uuid NOT NULL REFERENCES app_users(id), opened_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz, opening_clp integer NOT NULL DEFAULT 0 CHECK(opening_clp>=0), closing_clp integer);
CREATE TABLE audit_log (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, actor_id uuid REFERENCES app_users(id), branch_id uuid REFERENCES branches(id), action text NOT NULL, entity_type text NOT NULL, entity_id uuid, details jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX orders_branch_status_idx ON orders(branch_id,status,created_at);
CREATE INDEX audit_log_created_idx ON audit_log(created_at);
COMMIT;
