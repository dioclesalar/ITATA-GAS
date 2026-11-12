# ITATA GAS — Backend técnico inicial, versión 0.1.1 (desarrollo)

**Qué incluye:** API REST FastAPI, inicio de sesión con contraseñas Argon2, tokens temporales, permisos básicos, clientes y domicilios, productos, inventario agrupado por marca/capacidad/estado, pedidos, asignación a conductores, pagos y entrega con control de stock y auditoría. La recarga descuenta llenos y registra los vacíos recibidos (supuesto de canje 1:1 para esta prueba).

**Límites:** Esto es un prototipo local de backend; no es todavía la aplicación final, no incorpora APK, facturación electrónica, caja y rendiciones completas, reversas, manejo de devolución de cilindros distinta de 1:1, control real de carga por vehículo, ni despliegue seguro. No ingresar datos personales, pagos ni inventarios reales. Las operaciones de pagos aquí son *registros de pago*, no procesan cobros bancarios.

## Ejecutar localmente

Requiere Python 3.11 o superior. Desde esta carpeta:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

**Para Windows PowerShell (base SQLite local, solo pruebas):**
```powershell
$env:ITATA_BOOTSTRAP_EMAIL="gerencia@ejemplo.cl"
$env:ITATA_BOOTSTRAP_PASSWORD="CAMBIAR-por-clave-larga-y-unica"
$env:ITATA_TOKEN_SECRET="CAMBIAR-por-secreto-aleatorio-largo"
python bootstrap.py
uvicorn main:app --host 127.0.0.1 --port 8000
```

Visita `http://127.0.0.1:8000/docs` para acceder al catálogo de endpoints y probar con el token de `/auth/login` (botón `Authorize`). No expongas el puerto a Internet.

## Pruebas
```bash
pytest -q
```

Se incorporaron once casos automáticos locales (incluidos pagos parciales, reintentos sin duplicaciones y permisos entre sucursales). Consulta `RESULTADOS_PRUEBAS.md` para el alcance y los límites de la comprobación.

## PostgreSQL

El SQL recibido como punto de partida se conserva en `001_base.sql` y se añade `002_venta_tipo.sql`. Para adaptar este MVP al servidor PostgreSQL hacen falta pruebas y ajustes de integración entre el ORM y los tipos UUID nativos, seguridad por sucursal, control transaccional y migraciones. **No se declara desplegable directamente sobre PostgreSQL** ni listo para producción. SQLite se utiliza solo para demostrar y probar los procesos en local.


## Informe verificado de las pruebas (12-11-2026)

En este entorno se ejecutaron `pytest -q -ra --disable-warnings`: **8 pruebas locales aprobadas, 5 casos reales PostgreSQL omitidos por no existir un servidor disponible**. Los casos omitidos no son exitosos. Para ejecutar el banco real en Windows con Docker Desktop, consulta `INSTRUCCIONES_POSTGRESQL.md`, `compose.postgres-test.yml` y `ejecutar_pruebas_postgresql.ps1`.

## Decisiones de ITATA GAS mantenidas

- No hay app para clientes ni evaluaciones, encuestas o calificaciones de clientes.
- Inventario por marca, capacidad y estado, sin seguimiento QR por cilindro.
- Domicilios sin GPS obligatorio.
- Precios históricos guardados por línea de pedido.
- GG autoriza ingresos/ajustes sensibles de inventario en esta primera versión.
- Conductores tendrán una aplicación Android instalable en una etapa posterior.

## Pantallas incorporadas

- Con el backend ejecutándose, abrir `http://127.0.0.1:8000/panel` para la oficina y `http://127.0.0.1:8000/reparto` para el conductor. Estas pantallas operan sobre el backend en modo de desarrollo.
- Abrir `vista_previa_pedidos.html` directamente en el navegador para revisar una **demostración ficticia sin servidor**, con datos de muestra que no se guardan. Esta vista previa NO es el sistema conectado y no representa operaciones reales.
- Validación actual al 12-11-2026: **11 pruebas locales aprobadas y 5 pruebas PostgreSQL omitidas**.
