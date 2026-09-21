# -*- coding: utf-8 -*-
"""
Genera una base de datos de DEMOSTRACION con datos 100% inventados
(clientes, empleados, servicios, productos y tickets) para poder probar la app
sin datos reales. Ejecutar UNA vez:

    python generar_datos_demo.py

Crea data/peluqueria.db. Después:  streamlit run app.py
"""
import datetime as dt
import hashlib
import random

import pandas as pd

import ingest

random.seed(42)

# --- Empleados (nombre completo de "TPV"; la app lo normaliza a Ana G, etc.) ---
EMPLEADOS = ["Ana Garcia", "Marta Lopez", "Lucia Ruiz",
             "Sara Perez", "Carlos Martin", "Elena Navarro"]

# --- Catálogo de servicios (nombre, precio, familia) ---
SERVICIOS = [
    ("Corte Femenino", 25, "Corte"), ("Corte Masculino", 15, "Corte"),
    ("Color", 45, "Color"), ("Mechas", 70, "Color"),
    ("Peinado", 20, "Peinado"), ("Recogido", 40, "Peinado"),
    ("Tratamiento Hidratante", 30, "Tratamiento"),
    ("Manicura", 18, "Estetica"),
]
# --- Catálogo de productos (nombre, coste, precio, familia) ---
PRODUCTOS = [
    ("Champu Reparador 250ml", 8.5, 15.9, "Cuidado"),
    ("Mascarilla Nutritiva 200ml", 10.0, 19.9, "Cuidado"),
    ("Aceite Capilar 100ml", 7.0, 14.5, "Cuidado"),
    ("Serum Anti-frizz 90ml", 9.0, 17.5, "Cuidado"),
    ("Laca Fijadora 300ml", 4.0, 9.9, "Styling"),
    ("Espuma Volumen 200ml", 5.0, 11.9, "Styling"),
]

# --- Nombres/apellidos ficticios y comunes (nada real) ---
NOMBRES = ["Lucia", "Maria", "Carmen", "Laura", "Paula", "Nuria", "Alba",
           "Irene", "Rocio", "Clara", "Marta", "Sara", "Elena", "Ines",
           "Pablo", "Miguel", "David", "Sergio", "Jorge", "Raul", "Ivan",
           "Alberto", "Hugo", "Adrian", "Marcos"]
APELLIDOS = ["Garcia", "Lopez", "Martinez", "Perez", "Gomez", "Ruiz", "Diaz",
             "Moreno", "Alvarez", "Romero", "Navarro", "Torres", "Ramirez",
             "Gil", "Serrano", "Blanco", "Molina", "Castro", "Ortiz", "Rubio"]

HOY = dt.date(2025, 6, 30)
INICIO = dt.date(2023, 1, 2)


def _fecha(d):
    return d.strftime("%Y-%m-%d")


# ---------- Clientes ----------
clientes = []
for i in range(1, 61):
    nom = random.choice(NOMBRES)
    ape = random.choice(APELLIDOS) + " " + random.choice(APELLIDOS)
    cumple = dt.date(1990, random.randint(1, 12), random.randint(1, 28))
    alta = INICIO + dt.timedelta(days=random.randint(0, 400))
    clientes.append({
        "cod": f"C{i:04d}", "nombre": nom, "apellidos": ape, "dni": "",
        "direccion": "", "fecha_alta": _fecha(alta),
        "cumpleanos": _fecha(cumple), "ultima_visita": _fecha(alta),
        "movil": "6" + "".join(str(random.randint(0, 9)) for _ in range(8)),
        "email": f"{nom.lower()}.{i}@example.com", "recomendado": ""})
clientes_df = pd.DataFrame(clientes)

# ---------- Servicios / Productos ----------
servicios_df = pd.DataFrame([
    {"codigo": f"S{i:03d}", "nombre": n, "familia": fam, "duracion": 30,
     "precio": p, "impuesto": 21, "posicion": i, "mostrar": 1}
    for i, (n, p, fam) in enumerate(SERVICIOS, 1)])
productos_df = pd.DataFrame([
    {"codigo": f"P{i:03d}", "codigo_barras": f"84000000{i:03d}", "nombre": n,
     "coste": c, "precio": p, "impuesto": 21, "stock": random.randint(2, 20),
     "minimo": 2, "familia": fam, "proveedor": "Proveedor Demo"}
    for i, (n, c, p, fam) in enumerate(PRODUCTOS, 1)])

# ---------- Tickets (ventas) ----------
filas = []
numero = 1000
dia = INICIO
IVA = 0.21
while dia <= HOY:
    # cerrado domingos
    if dia.weekday() != 6:
        # cada cliente tiene su propia frecuencia (para que "ritmo" tenga sentido)
        n_tickets = random.randint(6, 16)
        for _ in range(n_tickets):
            numero += 1
            cl = random.choice(clientes)
            nombre_cli = f"{cl['nombre']} {cl['apellidos']}"
            if random.random() < 0.12:
                nombre_cli = "Generico"
            emp = random.choice(EMPLEADOS)
            emp_norm = ingest.normaliza_empleado(emp)
            pago = random.choice(["Contado", "Tarjeta", "Tarjeta"])
            # 1-2 servicios
            items = random.sample(SERVICIOS, k=random.randint(1, 2))
            # a veces un producto
            productos_venta = ([random.choice(PRODUCTOS)]
                               if random.random() < 0.22 else [])
            for (nom_s, precio, _fam) in items:
                base = round(precio / (1 + IVA), 2)
                imp = round(precio - base, 2)
                filas.append(dict(
                    fecha=_fecha(dia), numero=numero, cliente=nombre_cli,
                    concepto=f"1 X {nom_s}", concepto_limpio=nom_s, cantidad=1,
                    empleado=emp, empleado_norm=emp_norm, forma_pago=pago,
                    importe=base, impuesto=imp, total=precio,
                    seccion="Ventas Generales"))
            for (nom_p, _c, precio, _fam) in productos_venta:
                base = round(precio / (1 + IVA), 2)
                imp = round(precio - base, 2)
                filas.append(dict(
                    fecha=_fecha(dia), numero=numero, cliente=nombre_cli,
                    concepto=f"1 X {nom_p}", concepto_limpio=nom_p, cantidad=1,
                    empleado=emp, empleado_norm=emp_norm, forma_pago=pago,
                    importe=base, impuesto=imp, total=precio,
                    seccion="Ventas Generales"))
    dia += dt.timedelta(days=1)

ventas_df = pd.DataFrame(filas)
ventas_df["anio"] = pd.to_datetime(ventas_df.fecha).dt.year
ventas_df["mes"] = pd.to_datetime(ventas_df.fecha).dt.to_period("M").astype(str)
ventas_df["row_hash"] = [
    hashlib.md5(f"{r.numero}|{r.concepto}|{r.fecha}|{i}".encode()).hexdigest()
    for i, r in enumerate(ventas_df.itertuples())]

# ---------- Escritura ----------
ingest.init_db()
ingest.init_config()
ingest.replace_tabla(clientes_df, "clientes")
ingest.replace_tabla(servicios_df, "servicios")
ingest.replace_tabla(productos_df, "productos")
n = ingest.upsert_ventas(ventas_df)

print(f"Base de datos demo creada en: {ingest.DB_PATH}")
print(f"  {len(clientes_df)} clientes, {len(servicios_df)} servicios, "
      f"{len(productos_df)} productos")
print(f"  {n} lineas de ticket ({ventas_df.fecha.min()} a {ventas_df.fecha.max()})")
print("Ahora ejecuta:  streamlit run app.py")
