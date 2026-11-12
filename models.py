from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid, JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
import os

DB_URL = os.getenv('DATABASE_URL', 'sqlite:///./itata_dev.db')
engine = create_engine(DB_URL, connect_args={'check_same_thread': False} if DB_URL.startswith('sqlite') else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def uid(): return str(uuid4())
def now(): return datetime.now(timezone.utc)

class Base(DeclarativeBase): pass

class Branch(Base):
    __tablename__ = 'branches'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str|None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class User(Base):
    __tablename__ = 'app_users'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    branch_id: Mapped[str|None] = mapped_column(ForeignKey('branches.id'))
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Customer(Base):
    __tablename__ = 'customers'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    # Clientes vinculados a sucursal para no exponer domicilios entre sedes.
    branch_id: Mapped[str|None] = mapped_column(ForeignKey('branches.id'))
    name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str|None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Address(Base):
    __tablename__ = 'customer_addresses'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'))
    address: Mapped[str] = mapped_column(Text)
    commune: Mapped[str|None] = mapped_column(Text)
    instructions: Mapped[str|None] = mapped_column(Text)

class Product(Base):
    __tablename__ = 'products'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    brand: Mapped[str] = mapped_column(Text)
    capacity_kg: Mapped[float] = mapped_column(Numeric(6,2))
    __table_args__ = (UniqueConstraint('brand', 'capacity_kg'),)

class PriceTariff(Base):
    __tablename__ = 'price_tariffs'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    branch_id: Mapped[str] = mapped_column(ForeignKey('branches.id'), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey('products.id'), nullable=False)
    sale_type: Mapped[str] = mapped_column(String(16), nullable=False)
    price_clp: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('branch_id','product_id','sale_type'), CheckConstraint('price_clp >= 0'),)

class Inventory(Base):
    __tablename__ = 'inventory'
    branch_id: Mapped[str] = mapped_column(ForeignKey('branches.id'), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey('products.id'), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint('quantity >= 0'),)

class Order(Base):
    __tablename__ = 'orders'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    branch_id: Mapped[str] = mapped_column(ForeignKey('branches.id'))
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.id'))
    address_id: Mapped[str] = mapped_column(ForeignKey('customer_addresses.id'))
    status: Mapped[str] = mapped_column(String(32), default='pendiente')
    discount_category: Mapped[str|None] = mapped_column(String(32))
    discount_clp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_authorized_by: Mapped[str|None] = mapped_column(ForeignKey('app_users.id'))
    scheduled_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    items: Mapped[list['OrderItem']] = relationship(back_populates='order', lazy='selectin')

class OrderItem(Base):
    __tablename__ = 'order_items'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'))
    product_id: Mapped[str] = mapped_column(ForeignKey('products.id'))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_clp: Mapped[int] = mapped_column(Integer)
    sale_type: Mapped[str] = mapped_column(String(16), default='recarga')
    order: Mapped['Order'] = relationship(back_populates='items')

class DriverArea(Base):
    __tablename__ = 'driver_areas'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    driver_id: Mapped[str] = mapped_column(ForeignKey('app_users.id'), nullable=False)
    commune: Mapped[str] = mapped_column(Text, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint('driver_id','commune',name='uq_driver_commune'),)

class DiscountPolicy(Base):
    __tablename__ = 'discount_policies'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    branch_id: Mapped[str] = mapped_column(ForeignKey('branches.id'), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    max_clp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint('branch_id','category',name='uq_discount_policy'), CheckConstraint('max_clp >= 0'))

class Vehicle(Base):
    __tablename__ = 'vehicles'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    branch_id: Mapped[str|None] = mapped_column(ForeignKey('branches.id'))
    plate: Mapped[str|None] = mapped_column(Text, unique=True)
    description: Mapped[str|None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Delivery(Base):
    __tablename__ = 'deliveries'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), unique=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey('app_users.id'))
    vehicle_id: Mapped[str|None] = mapped_column(ForeignKey('vehicles.id'))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    delivered_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))

class Payment(Base):
    __tablename__ = 'payments'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'))
    amount_clp: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class IdempotencyRecord(Base):
    """Una operación registrada por usuario, ruta y clave de reintento."""
    __tablename__ = 'idempotency_records'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('app_users.id'), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(120), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str|None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('actor_id','endpoint','request_key',name='uq_idempotency_actor_endpoint_key'),)

class Audit(Base):
    __tablename__ = 'audit_log'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str|None] = mapped_column(ForeignKey('app_users.id'))
    branch_id: Mapped[str|None] = mapped_column(ForeignKey('branches.id'))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str|None] = mapped_column(Uuid(as_uuid=False))
    details: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), 'postgresql'), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

def get_db():
    with SessionLocal() as db:
        yield db
