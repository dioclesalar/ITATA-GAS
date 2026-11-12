"""ITATA GAS API MVP: ejecutar solo en desarrollo, no expuesta a Internet."""
from __future__ import annotations
import os, json, hashlib, unicodedata
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from argon2 import PasswordHasher
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from pathlib import Path
from models import Base, engine, DB_URL, get_db, Branch, User, Customer, Address, Product, Inventory, Order, OrderItem, Delivery, Vehicle, Payment, Audit, IdempotencyRecord, DriverArea, DiscountPolicy, PriceTariff, now

if DB_URL.startswith('sqlite'):
    Base.metadata.create_all(bind=engine)  # SOLO desarrollo; en PostgreSQL usar migraciones SQL.

app = FastAPI(title='ITATA GAS — API de desarrollo', version='0.1.0')
security = HTTPBearer()
ph = PasswordHasher()
SECRET = os.getenv('ITATA_TOKEN_SECRET', 'solo-desarrollo-cambiar-este-secreto')
if os.getenv('ITATA_ENV') == 'production' and SECRET == 'solo-desarrollo-cambiar-este-secreto':
    raise RuntimeError('ITATA_TOKEN_SECRET obligatorio en producción')
signer = URLSafeTimedSerializer(SECRET, salt='itata-auth-v1')

class Credentials(BaseModel):
    email: str
    password: str
class NewCustomer(BaseModel):
    name: str = Field(min_length=2)
    phone: str|None = None
    branch_id: str|None = None
class NewAddress(BaseModel):
    address: str = Field(min_length=4)
    commune: str = 'Quirihue'
    instructions: str|None = None
class NewDriver(BaseModel):
    branch_id: str
    full_name: str = Field(min_length=3)
    email: str = Field(min_length=5)
    password: str = Field(min_length=12)
    communes: list[str] = Field(default_factory=list, max_length=20)
class NewProduct(BaseModel):
    brand: str = Field(min_length=2)
    capacity_kg: float = Field(gt=0)
class StockInput(BaseModel):
    branch_id: str
    product_id: str
    status: Literal['lleno','vacio','danado','revision','fuera_servicio']
    quantity: int = Field(gt=0)
    reason: str = Field(min_length=5)
class NewItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_price_clp: int = Field(ge=0)
    sale_type: Literal['recarga','completo','vacio'] = 'recarga'
class TariffInput(BaseModel):
    branch_id: str
    product_id: str
    sale_type: Literal['recarga','completo','vacio']
    price_clp: int = Field(ge=0)

class NewOrder(BaseModel):
    branch_id: str
    customer_id: str
    address_id: str
    items: list[NewItem] = Field(min_length=1)
    discount_category: Literal['ninguno','adulto_mayor','comercio','discapacidad','especial'] = 'ninguno'
    discount_clp: int = Field(ge=0, default=0)
class Assignment(BaseModel):
    driver_id: str
    vehicle_id: str|None = None
class DiscountPolicyInput(BaseModel):
    branch_id: str
    category: Literal['adulto_mayor','comercio','discapacidad','especial']
    max_clp: int = Field(ge=0)
    enabled: bool = True

class DriverAvailability(BaseModel):
    commune: str = Field(min_length=2)
    available: bool

class NewPayment(BaseModel):
    amount_clp: int = Field(gt=0)
    method: Literal['efectivo','transferencia','tarjeta','otro']

def commune_key(commune: str) -> str:
    """Comparación de comunas sin diferencias por mayúsculas o tildes."""
    return ''.join(c for c in unicodedata.normalize('NFKD', commune.strip().casefold())
                   if not unicodedata.combining(c))

def order_total(order: Order) -> int:
    return sum(i.quantity*i.unit_price_clp for i in order.items) - (order.discount_clp or 0)

def pick_driver(db:Session, branch_id:str, commune:str) -> User|None:
    """Solo conductores habilitados expresamente para esa comuna; sin desvío a otras."""
    areas=db.scalars(select(DriverArea).join(User,User.id==DriverArea.driver_id).where(
        User.branch_id==branch_id,User.role=='conductor',User.active==True,
        DriverArea.available==True)).all()
    eligible=[a.driver_id for a in areas if commune_key(a.commune)==commune_key(commune)]
    if not eligible: return None
    active=db.execute(select(Delivery.driver_id,func.count()).join(Order,Order.id==Delivery.order_id)
        .where(Delivery.driver_id.in_(eligible),Order.status.in_(['asignado','en_ruta']))
        .group_by(Delivery.driver_id)).all()
    counts=dict(active)
    drivers=db.scalars(select(User).where(User.id.in_(eligible))).all()
    return min(drivers,key=lambda u:(counts.get(u.id,0),u.full_name,u.id)) if drivers else None

def promote_next(db:Session, driver_id:str, branch_id:str):
    # La fila del conductor serializa promociones simultáneas en PostgreSQL.
    db.scalar(select(User).where(User.id==driver_id).with_for_update())
    active=db.scalars(select(Order).join(Delivery,Delivery.order_id==Order.id)
        .where(Delivery.driver_id==driver_id,Order.branch_id==branch_id,
               Order.status.in_(['asignado','en_ruta']))
        .order_by(Order.created_at,Order.id)).all()
    if any(o.status=='en_ruta' for o in active):
        return
    if active:
        active[0].status='en_ruta'

def current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    try: uid = signer.loads(credentials.credentials, max_age=8*3600)
    except (BadSignature, SignatureExpired): raise HTTPException(401, 'Sesión inválida o vencida')
    user = db.get(User, uid)
    if not user or not user.active: raise HTTPException(401, 'Usuario inactivo o inexistente')
    return user

def allow(user: User, roles: tuple[str,...]):
    if user.role not in roles: raise HTTPException(403, 'Acceso denegado')

def branch_allowed(user: User, branch_id: str):
    if user.role != 'gerente_general' and user.branch_id != branch_id:
        raise HTTPException(403, 'No puedes operar en otra sucursal')

def customer_allowed(user: User, customer: Customer):
    # Viejos clientes sin sucursal quedan reservados al GG hasta clasificarlos.
    if user.role != 'gerente_general' and customer.branch_id != user.branch_id:
        raise HTTPException(403, 'Cliente de otra sucursal o sin sucursal asignada')


def log(db: Session, user: User, action: str, kind: str, target: str|None, branch: str|None, details: dict|None = None):
    db.add(Audit(actor_id=user.id, branch_id=branch, action=action, entity_type=kind, entity_id=target, details=details or {}))

def idempotency(db: Session, user: User, endpoint: str, key: str, payload: BaseModel):
    """Reserva la clave en la misma transacción que la operación.

    Ante una solicitud repetida devuelve la respuesta original; con distinto
    contenido para la misma clave, 409. El índice único arbitra carreras en PG.
    """
    fingerprint = hashlib.sha256(json.dumps(payload.model_dump(mode='json'),sort_keys=True,
                  separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    actor = user.id
    lookup = select(IdempotencyRecord).where(
        IdempotencyRecord.actor_id==actor,
        IdempotencyRecord.endpoint==endpoint,
        IdempotencyRecord.request_key==key)
    prior = db.scalar(lookup)
    if prior:
        if prior.payload_sha256!=fingerprint:
            raise HTTPException(409,'Clave de reintento utilizada con datos diferentes')
        if not prior.response_json:
            raise HTTPException(409,'La operación original todavía no se completa')
        return prior, json.loads(prior.response_json)
    record = IdempotencyRecord(actor_id=actor, endpoint=endpoint,
              request_key=key,payload_sha256=fingerprint)
    db.add(record)
    try:
        db.flush()  # reserva atómica: índice UNIQUE, no es suficiente un SELECT
    except IntegrityError:
        db.rollback()
        prior=db.scalar(lookup)
        if not prior or prior.payload_sha256!=fingerprint:
            raise HTTPException(409,'Clave de reintento utilizada con datos diferentes')
        if not prior.response_json:
            raise HTTPException(409,'La operación original todavía no se completa')
        return prior, json.loads(prior.response_json)
    return record, None

def idempotency_response(record: IdempotencyRecord, response: dict):
    record.response_json=json.dumps(response,ensure_ascii=False)
    return response

def required(db: Session, cls, id: str):
    obj = db.get(cls,id)
    if not obj: raise HTTPException(404, f'{cls.__name__} inexistente')
    return obj

def get_order_locked(db: Session, order_id: str) -> Order:
    order = db.scalar(select(Order).where(Order.id==order_id).with_for_update())
    if not order: raise HTTPException(404,'Pedido inexistente')
    return order

def assigned_driver(db: Session, order: Order, user: User):
    if user.role == 'conductor':
        d = db.scalar(select(Delivery).where(Delivery.order_id==order.id))
        if not d or d.driver_id != user.id: raise HTTPException(403, 'Pedido no asignado a ti')

@app.get('/health')
def health(): return {'ok':True,'service':'itata-gas','environment':'desarrollo' if DB_URL.startswith('sqlite') else 'postgresql'}

@app.post('/auth/login')
def login(payload: Credentials, db: Session=Depends(get_db)):
    u = db.scalar(select(User).where(User.email==payload.email.lower().strip()))
    try: valid = bool(u and ph.verify(u.password_hash,payload.password))
    except Exception: valid = False
    if not valid or not u.active: raise HTTPException(401,'Credenciales incorrectas')
    return {'access_token':signer.dumps(u.id),'token_type':'bearer','role':u.role}

@app.get('/auth/me')
def me(user:User=Depends(current_user)):
    return {'id':user.id,'full_name':user.full_name,'role':user.role,'branch_id':user.branch_id}

@app.get('/branches')
def list_branches(db:Session=Depends(get_db),user:User=Depends(current_user)):
    query=select(Branch).where(Branch.active==True).order_by(Branch.name)
    if user.role!='gerente_general': query=query.where(Branch.id==user.branch_id)
    return [{'id':b.id,'name':b.name} for b in db.scalars(query)]

@app.get('/products')
def list_products(db:Session=Depends(get_db),user:User=Depends(current_user)):
    rows=db.scalars(select(Product).order_by(Product.brand,Product.capacity_kg)).all()
    return [{'id':p.id,'brand':p.brand,'capacity_kg':float(p.capacity_kg)} for p in rows]

@app.get('/drivers')
def list_drivers(branch_id:str, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    branch_allowed(user,branch_id)
    rows=db.scalars(select(User).where(User.branch_id==branch_id,User.role=='conductor',User.active==True).order_by(User.full_name)).all()
    return [{'id':u.id,'full_name':u.full_name,'areas':[
      {'commune':a.commune,'available':a.available}
      for a in db.scalars(select(DriverArea).where(DriverArea.driver_id==u.id))]}
      for u in rows]

@app.post('/drivers',status_code=201)
def add_driver(p:NewDriver,db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo'))
    branch_allowed(user,p.branch_id); required(db,Branch,p.branch_id)
    email=p.email.lower().strip()
    if '@' not in email or email.startswith('@') or email.endswith('@'):
        raise HTTPException(422,'Correo inválido')
    if db.scalar(select(User).where(User.email==email)):
        raise HTTPException(409,'El correo ya está registrado')
    d=User(branch_id=p.branch_id,full_name=p.full_name,email=email,
           password_hash=ph.hash(p.password),role='conductor',active=True)
    db.add(d);db.flush()
    zones=list(dict.fromkeys(commune_key(s) for s in p.communes if s.strip()))
    for zone in zones: db.add(DriverArea(driver_id=d.id,commune=zone,available=True))
    log(db,user,'crear_conductor','app_users',d.id,p.branch_id)
    db.commit()
    return {'id':d.id,'full_name':d.full_name,'email':email,'branch_id':p.branch_id,'communes':zones}

@app.get('/discount-policies')
def list_discount_policies(branch_id:str, db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    branch_allowed(user,branch_id)
    rows=db.scalars(select(DiscountPolicy).where(DiscountPolicy.branch_id==branch_id)).all()
    return [{'category':x.category,'max_clp':x.max_clp,'enabled':x.enabled} for x in rows]

@app.put('/discount-policies')
def set_discount_policy(p:DiscountPolicyInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general',))
    required(db,Branch,p.branch_id)
    db.scalar(select(Branch).where(Branch.id==p.branch_id).with_for_update())
    policy=db.scalar(select(DiscountPolicy).where(
        DiscountPolicy.branch_id==p.branch_id,DiscountPolicy.category==p.category))
    if not policy:
        policy=DiscountPolicy(branch_id=p.branch_id,category=p.category)
        db.add(policy)
    policy.max_clp=p.max_clp;policy.enabled=p.enabled
    log(db,user,'configurar_descuento','discount_policies',None,p.branch_id,
        {'categoria':p.category,'max_clp':p.max_clp,'habilitado':p.enabled})
    db.commit()
    return {'category':p.category,'max_clp':p.max_clp,'enabled':p.enabled}

class DriverZoneInput(BaseModel):
    commune: str = Field(min_length=2)
    available: bool = True

@app.put('/drivers/{driver_id}/zones')
def configure_driver_zone(driver_id:str,p:DriverZoneInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    d=required(db,User,driver_id)
    branch_allowed(user,d.branch_id)
    if d.role!='conductor' or not d.active:
        raise HTTPException(422,'Conductor inválido')
    area=next((a for a in db.scalars(select(DriverArea).where(DriverArea.driver_id==driver_id))
               if commune_key(a.commune)==commune_key(p.commune)),None)
    if area is None:
        area=DriverArea(driver_id=driver_id,commune=p.commune,available=p.available)
        db.add(area)
    else:
        area.available=p.available
    log(db,user,'configurar_zona_conductor','driver_areas',area.id,d.branch_id,
        {'comuna':p.commune,'disponible':p.available})
    db.commit()
    return {'commune':p.commune,'available':p.available}

@app.put('/drivers/{driver_id}/availability')
def driver_availability(driver_id:str,p:DriverAvailability,db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    d=required(db,User,driver_id)
    branch_allowed(user,d.branch_id)
    area=next((a for a in db.scalars(select(DriverArea).where(DriverArea.driver_id==driver_id))
               if commune_key(a.commune)==commune_key(p.commune)),None)
    if not area: raise HTTPException(404,'Conductor no configurado en esa comuna')
    area.available=p.available
    log(db,user,'disponibilidad_conductor','driver_areas',area.id,d.branch_id,
        {'comuna':area.commune,'disponible':p.available})
    db.commit()
    return {'commune':area.commune,'available':area.available}

@app.get('/customers')
def list_customers(branch_id:str, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    branch_allowed(user,branch_id)
    rows=db.scalars(select(Customer).where(Customer.branch_id==branch_id).order_by(Customer.name)).all()
    return [{'id':c.id,'name':c.name,'phone':c.phone} for c in rows]

@app.get('/customers/{customer_id}/addresses')
def list_addresses(customer_id: str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    c=required(db,Customer,customer_id);customer_allowed(user,c)
    rows=db.scalars(select(Address).where(Address.customer_id==customer_id).order_by(Address.address)).all()
    return [{'id':a.id,'address':a.address,'commune':a.commune,'instructions':a.instructions} for a in rows]

@app.get('/ui.css',include_in_schema=False)
def ui_css(): return FileResponse(Path(__file__).with_name('ui.css'),media_type='text/css')

@app.get('/panel',include_in_schema=False)
def panel(): return FileResponse(Path(__file__).with_name('panel.html'),media_type='text/html')

@app.get('/reparto',include_in_schema=False)
def reparto(): return FileResponse(Path(__file__).with_name('reparto.html'),media_type='text/html')

@app.post('/customers',status_code=201)
def add_customer(p: NewCustomer, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    branch_id=p.branch_id or user.branch_id
    if not branch_id: raise HTTPException(422,'Selecciona una sucursal')
    branch_allowed(user,branch_id);required(db,Branch,branch_id)
    c=Customer(name=p.name,phone=p.phone,branch_id=branch_id)
    db.add(c); db.flush(); log(db,user,'crear_cliente','customers',c.id,user.branch_id); db.commit()
    return {'id':c.id,'name':c.name}

@app.post('/customers/{customer_id}/addresses',status_code=201)
def add_address(customer_id: str, p: NewAddress, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    customer=required(db,Customer,customer_id);customer_allowed(user,customer)
    a=Address(customer_id=customer_id,**p.model_dump()); db.add(a); db.flush(); log(db,user,'crear_domicilio','customer_addresses',a.id,user.branch_id); db.commit()
    return {'id':a.id,'customer_id':a.customer_id}

@app.post('/products',status_code=201)
def add_product(p: NewProduct, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general',))
    existing=db.scalar(select(Product).where(Product.brand==p.brand,Product.capacity_kg==p.capacity_kg))
    if existing: raise HTTPException(409,'Producto ya registrado')
    o=Product(**p.model_dump()); db.add(o); db.flush(); log(db,user,'crear_producto','products',o.id,user.branch_id); db.commit()
    return {'id':o.id,'brand':o.brand,'capacity_kg':float(o.capacity_kg)}

@app.post('/inventory/receive',status_code=201)
def receive_stock(p:StockInput, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general',))  # ingreso/ajuste sensible, solo GG en MVP
    required(db,Product,p.product_id)
    # Bloqueo de sucursal: también serializa el primer ingreso cuando aún no hay fila de stock.
    db.scalar(select(Branch).where(Branch.id==p.branch_id).with_for_update())
    required(db,Branch,p.branch_id)
    q=select(Inventory).where(Inventory.branch_id==p.branch_id, Inventory.product_id==p.product_id, Inventory.status==p.status).with_for_update()
    row=db.scalar(q)
    if row is None:
        row=Inventory(branch_id=p.branch_id, product_id=p.product_id,status=p.status,quantity=0); db.add(row)
    row.quantity+=p.quantity
    log(db,user,'ingreso_stock','inventory',None,p.branch_id,{'producto':p.product_id,'estado':p.status,'cantidad':p.quantity,'motivo':p.reason})
    db.commit(); return {'quantity':row.quantity}

@app.get('/inventory/{branch_id}')
def view_stock(branch_id:str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    branch_allowed(user,branch_id)
    rows=db.scalars(select(Inventory).where(Inventory.branch_id==branch_id)).all()
    return [{'product_id':v.product_id,'status':v.status,'quantity':v.quantity} for v in rows]

@app.put('/tariffs')
def set_tariff(p: TariffInput, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general',))
    required(db,Branch,p.branch_id)
    required(db,Product,p.product_id)
    tariff=db.scalar(select(PriceTariff).where(PriceTariff.branch_id==p.branch_id,
        PriceTariff.product_id==p.product_id,PriceTariff.sale_type==p.sale_type))
    if tariff is None:
        tariff=PriceTariff(**p.model_dump())
        db.add(tariff)
    else: tariff.price_clp=p.price_clp
    db.flush()
    log(db,user,'fijar_tarifa','price_tariffs',tariff.id,p.branch_id,{'precio_clp':p.price_clp})
    db.commit()
    return {'id':tariff.id,'price_clp':tariff.price_clp}

@app.get('/tariffs/{branch_id}')
def list_tariffs(branch_id:str, db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    branch_allowed(user,branch_id)
    rows=db.scalars(select(PriceTariff).where(PriceTariff.branch_id==branch_id)).all()
    return [{'product_id':r.product_id,'sale_type':r.sale_type,'price_clp':r.price_clp} for r in rows]

@app.post('/orders',status_code=201)
def create_order(p: NewOrder, idempotency_key: str = Header(min_length=8, max_length=128, alias='Idempotency-Key'), db:Session=Depends(get_db), user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    branch_allowed(user,p.branch_id)
    request, replay = idempotency(db,user,'POST /orders',idempotency_key,p)
    if replay is not None: return replay
    required(db,Branch,p.branch_id); customer=required(db,Customer,p.customer_id)
    if customer.branch_id != p.branch_id: raise HTTPException(422,'Cliente no pertenece a la sucursal del pedido')
    address=required(db,Address,p.address_id)
    if address.customer_id!=p.customer_id: raise HTTPException(422,'El domicilio no pertenece al cliente')
    if p.discount_category=='ninguno' and p.discount_clp:
        raise HTTPException(422,'Descuento sin motivo')
    if p.discount_category!='ninguno' and not p.discount_clp:
        raise HTTPException(422,'Indica monto de descuento o selecciona sin descuento')
    subtotal=sum(i.quantity*i.unit_price_clp for i in p.items)
    if p.discount_clp>subtotal: raise HTTPException(422,'Descuento supera subtotal')
    if p.discount_clp and user.role!='gerente_general':
        policy=db.scalar(select(DiscountPolicy).where(
            DiscountPolicy.branch_id==p.branch_id,DiscountPolicy.category==p.discount_category))
        if not policy or not policy.enabled or p.discount_clp>policy.max_clp:
            raise HTTPException(403,'Descuento fuera del límite aprobado por gerencia')
    for i in p.items:
        required(db,Product,i.product_id)
        tariff=db.scalar(select(PriceTariff).where(
            PriceTariff.branch_id==p.branch_id,PriceTariff.product_id==i.product_id,
            PriceTariff.sale_type==i.sale_type))
        if tariff is None: raise HTTPException(422,'Tarifa no configurada por Gerencia')
        if i.unit_price_clp != tariff.price_clp:
            raise HTTPException(403,'El precio del pedido debe coincidir con la tarifa de Gerencia')
    # Bloquea la sucursal para coordinar asignación por menor carga de trabajo en PostgreSQL.
    db.scalar(select(Branch).where(Branch.id==p.branch_id).with_for_update())
    o=Order(branch_id=p.branch_id,customer_id=p.customer_id,address_id=p.address_id,
       discount_category=p.discount_category if p.discount_clp else None,
       discount_clp=p.discount_clp,
       discount_authorized_by=user.id if user.role=='gerente_general' and p.discount_clp else None)
    db.add(o); db.flush()
    for i in p.items: db.add(OrderItem(order_id=o.id,**i.model_dump()))
    d=pick_driver(db,p.branch_id,address.commune or '')
    if d:
        db.add(Delivery(order_id=o.id,driver_id=d.id))
        o.status='asignado'
        promote_next(db,d.id,p.branch_id)
    log(db,user,'crear_pedido','orders',o.id,p.branch_id,
       {'comuna':address.commune,'conductor_auto':d.id if d else None,
        'descuento_clp':p.discount_clp,'categoria':p.discount_category})
    result=idempotency_response(request,{'id':o.id,'status':o.status,
           'driver_id':d.id if d else None,'total_clp':subtotal-p.discount_clp,
           'discount_clp':p.discount_clp})
    db.commit(); return result

@app.get('/orders')
def list_orders(branch_id:str, db:Session=Depends(get_db),user:User=Depends(current_user)):
    branch_allowed(user,branch_id)
    query=select(Order).where(Order.branch_id==branch_id).order_by(Order.created_at.desc()).limit(100)
    if user.role=='conductor':
        query=query.join(Delivery,Delivery.order_id==Order.id).where(Delivery.driver_id==user.id)
    orders=db.scalars(query).all()
    output=[]
    for o in orders:
        total=order_total(o)
        paid=db.scalar(select(func.coalesce(func.sum(Payment.amount_clp),0)).where(Payment.order_id==o.id)) or 0
        c=db.get(Customer,o.customer_id)
        d=db.get(Address,o.address_id)
        output.append({'id':o.id,'status':o.status,'total_clp':total,'paid_clp':paid,
                       'remaining_clp':total-paid,'discount_category':o.discount_category,'discount_clp':o.discount_clp,'customer':c.name,'phone':c.phone,
                       'address':d.address,'commune':d.commune})
    return output

@app.get('/orders/{order_id}')
def order_detail(order_id:str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    o=required(db,Order,order_id); branch_allowed(user,o.branch_id); assigned_driver(db,o,user)
    customer=required(db,Customer,o.customer_id); addr=required(db,Address,o.address_id)
    driver=db.scalar(select(Delivery).where(Delivery.order_id==o.id))
    paid=db.scalar(select(func.coalesce(func.sum(Payment.amount_clp),0)).where(Payment.order_id==o.id)) or 0
    total=order_total(o)
    products={p.id:p for p in db.scalars(select(Product).where(Product.id.in_([i.product_id for i in o.items])))}
    return {'id':o.id,'status':o.status,'branch_id':o.branch_id,
        'customer':customer.name,'phone':customer.phone,'address':addr.address,'commune':addr.commune,
        'instructions':addr.instructions,'total_clp':total,'paid_clp':paid,'remaining_clp':total-paid,'discount_category':o.discount_category,'discount_clp':o.discount_clp,
        'driver_id':driver.driver_id if driver else None,
        'items':[{'product_id':i.product_id,'product':f'{products[i.product_id].brand} {products[i.product_id].capacity_kg:g} kg',
                  'sale_type':i.sale_type,'quantity':i.quantity,'unit_price_clp':i.unit_price_clp} for i in o.items]}

@app.get('/dispatch/unassigned')
def unassigned_orders(branch_id:str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    branch_allowed(user,branch_id)
    orders=db.scalars(select(Order).where(Order.branch_id==branch_id,Order.status=='pendiente')
        .order_by(Order.created_at,Order.id)).all()
    return [{'order_id':o.id,'commune':db.get(Address,o.address_id).commune,
             'reason':'Sin conductor disponible para asignación'} for o in orders]

@app.get('/drivers/me/queue')
def my_delivery_queue(db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('conductor',))
    orders=db.scalars(select(Order).join(Delivery,Delivery.order_id==Order.id)
        .where(Delivery.driver_id==user.id,Order.status.in_(['asignado','en_ruta']))
        .order_by(Order.created_at,Order.id)).all()
    return [{'order_id':o.id,'status':o.status,'position':index+1,
             'commune':db.get(Address,o.address_id).commune}
            for index,o in enumerate(orders)]

@app.post('/orders/{order_id}/assign')
def assign(order_id:str,p:Assignment,db:Session=Depends(get_db),user:User=Depends(current_user)):
    allow(user,('gerente_general','administrativo','responsable_sucursal'))
    o=get_order_locked(db,order_id); branch_allowed(user,o.branch_id)
    if o.status!='pendiente': raise HTTPException(409,'Solo se asignan pedidos pendientes')
    driver=required(db,User,p.driver_id)
    if not driver.active or driver.role!='conductor' or driver.branch_id!=o.branch_id: raise HTTPException(422,'Conductor inválido para sucursal')
    address=required(db,Address,o.address_id)
    areas=db.scalars(select(DriverArea).where(
        DriverArea.driver_id==driver.id, DriverArea.available==True)).all()
    if not (address.commune or '').strip():
        raise HTTPException(422,'El pedido debe tener una comuna válida')
    if not any(commune_key(a.commune)==commune_key(address.commune) for a in areas):
        raise HTTPException(403,'Conductor no habilitado para la comuna del pedido')
    if p.vehicle_id:
        v=required(db,Vehicle,p.vehicle_id)
        if not v.active or v.branch_id!=o.branch_id: raise HTTPException(422,'Vehículo inválido')
    db.add(Delivery(order_id=o.id,driver_id=p.driver_id,vehicle_id=p.vehicle_id)); o.status='asignado'
    promote_next(db,p.driver_id,o.branch_id)
    log(db,user,'asignar_pedido','orders',o.id,o.branch_id,{'conductor':p.driver_id}); db.commit()
    return {'id':o.id,'status':o.status}

@app.post('/orders/{order_id}/en-route')
def en_route(order_id:str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    raise HTTPException(409,'En ruta se activa automáticamente según la cola')

@app.post('/orders/{order_id}/payments',status_code=201)
def pay(order_id:str,p:NewPayment,idempotency_key: str = Header(min_length=8, max_length=128, alias='Idempotency-Key'),db:Session=Depends(get_db),user:User=Depends(current_user)):
    o=get_order_locked(db,order_id); branch_allowed(user,o.branch_id); assigned_driver(db,o,user)
    request, replay = idempotency(db,user,f'POST /orders/{order_id}/payments',idempotency_key,p)
    if replay is not None: return replay
    if o.status not in ('asignado','en_ruta'): raise HTTPException(409,'No se admiten pagos en este estado')
    total=order_total(o)
    paid=db.scalar(select(func.coalesce(func.sum(Payment.amount_clp),0)).where(Payment.order_id==o.id)) or 0
    if paid+p.amount_clp>total: raise HTTPException(409,'Pago supera total del pedido')
    payment=Payment(order_id=o.id,amount_clp=p.amount_clp,method=p.method); db.add(payment); db.flush()
    log(db,user,'pago_registrado','payments',payment.id,o.branch_id,{'monto':p.amount_clp,'metodo':p.method})
    result=idempotency_response(request,{'id':payment.id,'paid_clp':paid+p.amount_clp,'remaining_clp':total-paid-p.amount_clp})
    db.commit(); return result

@app.post('/orders/{order_id}/deliver')
def deliver(order_id:str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    o=get_order_locked(db,order_id); branch_allowed(user,o.branch_id); assigned_driver(db,o,user)
    if o.status!='en_ruta': raise HTTPException(409,'Solo se puede entregar el primer pedido en ruta')
    total=order_total(o)
    paid=db.scalar(select(func.coalesce(func.sum(Payment.amount_clp),0)).where(Payment.order_id==o.id)) or 0
    if paid!=total: raise HTTPException(409,'Debe registrar pago completo antes de entregar')
    # Serializar cambios de stock por sucursal, incluso cuando no existe fila de vacíos.
    db.scalar(select(Branch).where(Branch.id==o.branch_id).with_for_update())
    needs:dict[tuple[str,str],int]={}
    for i in o.items:
        if i.sale_type!='vacio': needs[(i.product_id,'lleno')]=needs.get((i.product_id,'lleno'),0)+i.quantity
        else: needs[(i.product_id,'vacio')]=needs.get((i.product_id,'vacio'),0)+i.quantity
    for (product_id,status),qty in sorted(needs.items()):
        row=db.scalar(select(Inventory).where(Inventory.branch_id==o.branch_id, Inventory.product_id==product_id, Inventory.status==status).with_for_update())
        if not row or row.quantity<qty: raise HTTPException(409,f'Stock insuficiente de {status}: {product_id}')
        row.quantity-=qty
    for i in o.items:
        if i.sale_type=='recarga':
            row=db.scalar(select(Inventory).where(Inventory.branch_id==o.branch_id, Inventory.product_id==i.product_id, Inventory.status=='vacio').with_for_update())
            if row is None:
                row=Inventory(branch_id=o.branch_id,product_id=i.product_id,status='vacio',quantity=0);db.add(row)
            row.quantity+=i.quantity
    o.status='entregado'
    d=db.scalar(select(Delivery).where(Delivery.order_id==o.id)); d.delivered_at=now()
    promote_next(db,d.driver_id,o.branch_id)
    log(db,user,'entregar_pedido','orders',o.id,o.branch_id,{'total_clp':total})
    db.commit();return {'id':o.id,'status':'entregado','total_clp':total}
