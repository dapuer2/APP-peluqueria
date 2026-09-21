"""
Motor de carga de datos para el dashboard de la peluqueria.

Lee los ficheros .xls que se exportan desde "TPV 123 peluqueros"
(Caja Diaria General, Clientes, Productos, Servicios) y los vuelca a una
base de datos SQLite.

Puntos clave del diseno:
  * Los exports de 123 usan celdas combinadas: cada columna logica ocupa
    varias columnas fisicas con huecos. El parser alinea las etiquetas de
    la cabecera con las columnas de datos que realmente tienen contenido.
  * VENTAS -> se hace "upsert" con un hash por linea, de modo que subir el
    mismo export dos veces NO duplica datos (solo entra lo nuevo).
  * CATALOGOS (clientes, productos, servicios) -> son fotos completas del
    estado actual, asi que se reemplaza la tabla entera en cada carga.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd


def _resolver_db_path() -> Path:
    """Decide donde vive peluqueria.db (para poder compartirlo por OneDrive).

    Orden de prioridad:
      1) Variable de entorno SALON_DB (ruta a la CARPETA, o al propio .db).
      2) Fichero 'db_config.txt' junto a este archivo con la ruta de la CARPETA
         (una linea; las que empiezan por '#' se ignoran).
      3) Por defecto: la carpeta 'data' local de siempre.
    El nombre del fichero siempre es 'peluqueria.db'.
    """
    def _desde_carpeta_o_fichero(txt: str) -> Path:
        p = Path(txt.strip().strip('"').strip("'")).expanduser()
        # Si apunta a un .db, se usa tal cual; si es una carpeta, se le anade el nombre.
        return p if p.suffix.lower() == ".db" else p / "peluqueria.db"

    env = os.environ.get("SALON_DB", "").strip()
    if env:
        return _desde_carpeta_o_fichero(env)

    cfg = Path(__file__).parent / "db_config.txt"
    if cfg.exists():
        for linea in cfg.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                return _desde_carpeta_o_fichero(linea)

    return Path(__file__).parent / "data" / "peluqueria.db"


DB_PATH = _resolver_db_path()

# Secciones (agrupaciones) que aparecen como cabeceras dentro de la Caja
SECCIONES_CAJA = {
    "Cobros de Deudas Pendientes",
    "Devoluciones / Modificaciones / Rectificativas de venta",
    "Gastos del Dia",
    "Ventas Generales",
    "Ventas no Cobradas",
}


# --------------------------------------------------------------------------- #
# Utilidades de parseo
# --------------------------------------------------------------------------- #
def _read_raw(src, sheet: int = 0) -> pd.DataFrame:
    """Lee un .xls sin cabecera (todo crudo) para poder localizar las filas."""
    return pd.read_excel(src, header=None, sheet_name=sheet)


def _find_header_row(df: pd.DataFrame, anchors: list[str], limit: int = 40) -> int | None:
    """Devuelve el indice de la primera fila que contiene alguna 'anchor'."""
    for i in range(min(limit, len(df))):
        rowvals = {str(v).strip() for v in df.iloc[i].tolist()}
        if any(a in rowvals for a in anchors):
            return i
    return None


def _header_labels(df: pd.DataFrame, header_row: int) -> list[str]:
    """Etiquetas no vacias de la fila de cabecera, en orden."""
    return [
        str(v).strip()
        for v in df.iloc[header_row].tolist()
        if isinstance(v, str) and v.strip()
    ]


def _align(data: pd.DataFrame, labels: list[str]) -> pd.DataFrame:
    """
    Alinea las columnas de datos con las etiquetas de la cabecera.

    Los exports dejan columnas 'basura' con un punado de valores sueltos
    (celdas combinadas mal cerradas). Por eso NO vale coger 'cualquier
    columna con datos': se cogen las N columnas MAS pobladas (N = numero de
    etiquetas), que son las columnas logicas reales, y luego se ordenan por
    posicion para que casen con las etiquetas de izquierda a derecha.
    """
    n = len(labels)
    counts = data.notna().sum()
    reales = counts[counts > 0].sort_values(ascending=False).head(n).index.tolist()
    cols = sorted(reales)  # orden de lectura, izquierda -> derecha
    m = min(len(cols), len(labels))
    out = data[cols[:m]].copy()
    out.columns = labels[:m]
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Normalizacion de nombres de empleado
# --------------------------------------------------------------------------- #
def normaliza_empleado(nombre) -> str | None:
    """
    Unifica las variantes del mismo empleado.
    'Ana G.', 'Ana  G.' y 'Ana Garcia Lopez'              -> 'Ana G'
    'marta l.' y 'marta lopez'                             -> 'Marta L'
    Regla: primer nombre + inicial del segundo token.
    """
    if not isinstance(nombre, str) or not nombre.strip():
        return None
    tokens = nombre.lower().split()
    if not tokens:
        return None
    key = tokens[0]
    if len(tokens) > 1:
        key += " " + tokens[1][0]
    return key.title()


# --------------------------------------------------------------------------- #
# Parsers por tipo de fichero
# --------------------------------------------------------------------------- #
_QTY_RE = re.compile(r"^\s*(\d+)\s*[xX]\s*(.*)$")


def _split_concepto(concepto):
    """'1 X Corte Femenino' -> (1, 'Corte Femenino'). Si no matchea -> (1, texto)."""
    if not isinstance(concepto, str):
        return 1, ""
    m = _QTY_RE.match(concepto)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 1, concepto.strip()


def parse_caja(src) -> pd.DataFrame:
    """Devuelve el detalle de ventas de la Caja Diaria General, ya limpio."""
    df = _read_raw(src)
    hr = _find_header_row(df, ["Fecha"])
    if hr is None:
        raise ValueError("No encuentro la cabecera (Fecha) en la Caja.")

    labels = _header_labels(df, hr)

    # Region de datos = a partir de la cabecera. La seccion esta en la col 0.
    region = df.iloc[hr + 1:].copy()
    col0 = region.columns[0]

    # Seccion vigente = ultimo valor de la col 0 arrastrado hacia abajo
    sec_series = region[col0].where(region[col0].isin(SECCIONES_CAJA)).ffill()

    # Filas de datos = las que NO son cabecera de seccion y tienen contenido
    is_section = region[col0].notna()
    n_valores = region.notna().sum(axis=1)
    mask = (~is_section) & (n_valores >= 6)

    data = region[mask]
    seccion = sec_series[mask]

    out = _align(data.drop(columns=[col0]), [l for l in labels])
    out["Seccion"] = seccion.values

    # Renombrado a nombres estables
    ren = {
        "Fecha": "fecha", "Numero": "numero", "Cliente": "cliente",
        "Concepto": "concepto", "Empleado": "empleado", "F. Pago": "forma_pago",
        "Importe": "importe", "Impuesto": "impuesto", "Total": "total",
        "Seccion": "seccion",
    }
    out = out.rename(columns=ren)

    # Tipado
    out["fecha"] = pd.to_datetime(out["fecha"], errors="coerce")
    for c in ["importe", "impuesto", "total"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["numero"] = pd.to_numeric(out["numero"], errors="coerce")

    out = out.dropna(subset=["fecha"])

    # Derivados
    out["empleado_norm"] = out["empleado"].apply(normaliza_empleado)
    qty_name = out["concepto"].apply(_split_concepto)
    out["cantidad"] = [q for q, _ in qty_name]
    out["concepto_limpio"] = [n for _, n in qty_name]
    out["anio"] = out["fecha"].dt.year
    out["mes"] = out["fecha"].dt.to_period("M").astype(str)

    # Hash de linea para deduplicar (incluye orden dentro de duplicados identicos)
    key_cols = ["fecha", "numero", "concepto", "empleado", "importe", "impuesto", "total", "seccion"]
    base = out[key_cols].astype(str).agg("|".join, axis=1)
    dup_idx = base.groupby(base).cumcount().astype(str)
    out["row_hash"] = (base + "|" + dup_idx).apply(
        lambda s: hashlib.md5(s.encode("utf-8")).hexdigest()
    )
    return out


def _parse_catalogo(src, anchor: str, rename: dict) -> pd.DataFrame:
    df = _read_raw(src)
    hr = _find_header_row(df, [anchor])
    if hr is None:
        raise ValueError(f"No encuentro la cabecera ({anchor}).")
    labels = _header_labels(df, hr)
    region = df.iloc[hr + 1:].copy()
    # filas con contenido real
    region = region[region.notna().sum(axis=1) >= max(2, len(labels) // 2)]
    out = _align(region, labels).rename(columns=rename)
    return out


def parse_clientes(src) -> pd.DataFrame:
    out = _parse_catalogo(src, "Nombre", {
        "cod.": "cod", "Nombre": "nombre", "Apellidos": "apellidos",
        "Dni/Cif": "dni", "Direccion": "direccion", "F. Alta": "fecha_alta",
        "Ult.Visita": "ultima_visita", "Movil": "movil", "E-Mail": "email",
        "Recomendado": "recomendado",
    })
    # la columna de cumpleanos puede variar de nombre por la codificacion
    for c in list(out.columns):
        if c.lower().startswith("cumplea"):
            out = out.rename(columns={c: "cumpleanos"})
    for c in ["fecha_alta", "ultima_visita", "cumpleanos"]:
        if c in out.columns:
            out[c] = pd.to_datetime(out[c], errors="coerce", dayfirst=True)
    return out


def parse_productos(src) -> pd.DataFrame:
    out = _parse_catalogo(src, "Nombre", {
        "Codigo": "codigo", "Codigo Barras": "codigo_barras", "Nombre": "nombre",
        "Coste": "coste", "Precio": "precio", "Impuesto": "impuesto",
        "Stock": "stock", "Min": "minimo", "Seccion": "familia",
        "Proveedor": "proveedor",
    })
    for c in ["coste", "precio", "stock"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def parse_servicios(src) -> pd.DataFrame:
    out = _parse_catalogo(src, "Nombre", {
        "Codigo": "codigo", "Nombre": "nombre", "Seccion": "familia",
        "Duracion": "duracion", "Precio": "precio", "Impuesto": "impuesto",
        "Posicion": "posicion", "Mostrar en Caja": "mostrar",
    })
    for c in ["precio", "duracion"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


# --------------------------------------------------------------------------- #
# Deteccion automatica del tipo de fichero
# --------------------------------------------------------------------------- #
def detectar_tipo(src) -> str | None:
    """Mira las primeras filas y decide que fichero es."""
    try:
        df = _read_raw(src)
    except Exception:
        return None
    texto = " ".join(
        str(v) for v in df.head(15).values.flatten() if isinstance(v, str)
    ).lower()
    if "caja diaria" in texto:
        return "caja"
    if "listado de productos" in texto:
        return "productos"
    if "listado de servicios" in texto:
        return "servicios"
    if "clientes" in texto and "ult.visita" in texto.replace(" ", ""):
        return "clientes"
    # fallback por columnas
    if "ult.visita" in texto:
        return "clientes"
    return None


# --------------------------------------------------------------------------- #
# Base de datos
# --------------------------------------------------------------------------- #
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS ventas (
                row_hash TEXT PRIMARY KEY,
                fecha TEXT, numero REAL, cliente TEXT, concepto TEXT,
                concepto_limpio TEXT, cantidad INTEGER,
                empleado TEXT, empleado_norm TEXT, forma_pago TEXT,
                importe REAL, impuesto REAL, total REAL,
                seccion TEXT, anio INTEGER, mes TEXT
            )
        """)
        con.commit()


def upsert_ventas(df: pd.DataFrame) -> int:
    """Inserta solo las lineas nuevas. Devuelve cuantas se anadieron."""
    init_db()
    cols = ["row_hash", "fecha", "numero", "cliente", "concepto", "concepto_limpio",
            "cantidad", "empleado", "empleado_norm", "forma_pago", "importe",
            "impuesto", "total", "seccion", "anio", "mes"]
    sub = df[cols].copy()
    sub["fecha"] = sub["fecha"].astype(str)
    with get_conn() as con:
        antes = con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        con.executemany(
            f"INSERT OR IGNORE INTO ventas ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            sub.itertuples(index=False, name=None),
        )
        con.commit()
        despues = con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    return despues - antes


def replace_tabla(df: pd.DataFrame, tabla: str):
    """Reemplaza por completo una tabla de catalogo (foto del estado actual)."""
    with get_conn() as con:
        df.to_sql(tabla, con, if_exists="replace", index=False)
        con.commit()


def cargar_fichero(src, nombre_original: str = "") -> dict:
    """
    Punto de entrada unico: detecta el tipo y lo carga en la BD.
    Devuelve un dict con el resultado para mostrar en la web.
    """
    tipo = detectar_tipo(src)
    if tipo == "caja":
        df = parse_caja(src)
        nuevas = upsert_ventas(df)
        return {"tipo": "Caja / Ventas", "filas_fichero": len(df),
                "filas_nuevas": nuevas, "ok": True}
    if tipo == "clientes":
        df = parse_clientes(src)
        replace_tabla(df, "clientes")
        return {"tipo": "Clientes", "filas_fichero": len(df),
                "filas_nuevas": len(df), "ok": True}
    if tipo == "productos":
        df = parse_productos(src)
        replace_tabla(df, "productos")
        return {"tipo": "Productos", "filas_fichero": len(df),
                "filas_nuevas": len(df), "ok": True}
    if tipo == "servicios":
        df = parse_servicios(src)
        replace_tabla(df, "servicios")
        return {"tipo": "Servicios", "filas_fichero": len(df),
                "filas_nuevas": len(df), "ok": True}
    return {"tipo": "Desconocido", "filas_fichero": 0, "filas_nuevas": 0,
            "ok": False, "nombre": nombre_original}


# --------------------------------------------------------------------------- #
# Lectura para los KPIs
# --------------------------------------------------------------------------- #
def tabla_existe(nombre: str) -> bool:
    with get_conn() as con:
        r = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (nombre,),
        ).fetchone()
    return r is not None


def leer_ventas() -> pd.DataFrame:
    if not tabla_existe("ventas"):
        return pd.DataFrame()
    with get_conn() as con:
        df = pd.read_sql("SELECT * FROM ventas", con)
    if not df.empty:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


def leer_tabla(nombre: str) -> pd.DataFrame:
    if not tabla_existe(nombre):
        return pd.DataFrame()
    with get_conn() as con:
        return pd.read_sql(f"SELECT * FROM {nombre}", con)


# --------------------------------------------------------------------------- #
# Campaña de recuperacion: registro de mensajes ya enviados
# --------------------------------------------------------------------------- #
def init_avisos():
    with get_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS avisos (
                cod TEXT,
                nombre TEXT,
                apellidos TEXT,
                movil TEXT,
                ultima_visita TEXT,   -- ultima visita del cliente cuando se aviso
                fecha_aviso TEXT,     -- dia en que se marco como avisado
                PRIMARY KEY (cod, fecha_aviso)
            )
        """)
        con.commit()


def registrar_avisos(df: pd.DataFrame, fecha_aviso: str) -> int:
    """Marca a los clientes de df como avisados en la fecha indicada."""
    init_avisos()
    rows = [
        (str(r.get("cod", "")), str(r.get("nombre", "")),
         str(r.get("apellidos", "")), str(r.get("movil", "")),
         str(r.get("ultima_visita", "")), fecha_aviso)
        for _, r in df.iterrows()
    ]
    with get_conn() as con:
        con.executemany(
            "INSERT OR IGNORE INTO avisos VALUES (?,?,?,?,?,?)", rows)
        con.commit()
    return len(rows)


def leer_avisos() -> pd.DataFrame:
    if not tabla_existe("avisos"):
        return pd.DataFrame(
            columns=["cod", "nombre", "apellidos", "movil",
                     "ultima_visita", "fecha_aviso"])
    with get_conn() as con:
        return pd.read_sql("SELECT * FROM avisos", con)


def init_cumple():
    with get_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cumple_felicitados (
                cod TEXT,
                anio INTEGER,
                fecha TEXT,
                PRIMARY KEY (cod, anio)
            )
        """)
        con.commit()


def registrar_felicitacion(cod, anio, fecha) -> None:
    """Marca a un cliente como felicitado en un año (para no repetir felicitacion)."""
    init_cumple()
    with get_conn() as con:
        con.execute("INSERT OR IGNORE INTO cumple_felicitados VALUES (?,?,?)",
                    (str(cod), int(anio), str(fecha)))
        con.commit()


def leer_felicitados(anio) -> set:
    """Codigos ya felicitados ese año."""
    if not tabla_existe("cumple_felicitados"):
        return set()
    with get_conn() as con:
        rows = con.execute(
            "SELECT cod FROM cumple_felicitados WHERE anio=?", (int(anio),)).fetchall()
    return {str(r[0]) for r in rows}


def borrar_felicitacion(cod, anio) -> None:
    """Quita la marca de felicitado (deshacer)."""
    if not tabla_existe("cumple_felicitados"):
        return
    with get_conn() as con:
        con.execute("DELETE FROM cumple_felicitados WHERE cod=? AND anio=?",
                    (str(cod), int(anio)))
        con.commit()


def borrar_avisos(cods=None) -> int:
    """Borra avisos. Si cods es None, borra TODOS; si es una lista, solo los de
    esos codigos de cliente. Devuelve cuantas filas se borraron."""
    if not tabla_existe("avisos"):
        return 0
    with get_conn() as con:
        if cods is None:
            n = con.execute("SELECT COUNT(*) FROM avisos").fetchone()[0]
            con.execute("DELETE FROM avisos")
        else:
            cods = [str(c) for c in cods]
            if not cods:
                return 0
            marcas = ",".join("?" * len(cods))
            n = con.execute(
                f"DELETE FROM avisos WHERE cod IN ({marcas})", cods).rowcount
        con.commit()
    return n


# --------------------------------------------------------------------------- #
# Configuracion editable (datos que NO vienen del TPV): sueldos, horas, ajustes
# --------------------------------------------------------------------------- #
def init_config():
    with get_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS plantilla (
                empleado TEXT PRIMARY KEY, sueldo REAL, horas_sem REAL)
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT)
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS empleados (
                nombre_tpv TEXT PRIMARY KEY, nombre_bonito TEXT, activo INTEGER,
                fecha_alta TEXT, fecha_baja TEXT)
        """)
        # Migracion: anadir fechas de alta/baja a tablas 'empleados' antiguas.
        _ce = [r[1] for r in con.execute("PRAGMA table_info(empleados)")]
        for _c in ("fecha_alta", "fecha_baja"):
            if _c not in _ce:
                con.execute(f"ALTER TABLE empleados ADD COLUMN {_c} TEXT")
        con.execute("""
            CREATE TABLE IF NOT EXISTS comisiones (
                empleado TEXT, mes TEXT,
                individual REAL DEFAULT 0, general REAL DEFAULT 0,
                productos REAL DEFAULT 0, extra REAL DEFAULT 0,
                horas_extra REAL DEFAULT 0,
                notas TEXT DEFAULT '', importe REAL DEFAULT 0,
                PRIMARY KEY (empleado, mes))
        """)
        # Migracion: anadir columnas de desglose a tablas 'comisiones' antiguas.
        _cols = [r[1] for r in con.execute("PRAGMA table_info(comisiones)")]
        for _c, _decl in [("individual", "REAL DEFAULT 0"),
                          ("general", "REAL DEFAULT 0"),
                          ("productos", "REAL DEFAULT 0"),
                          ("extra", "REAL DEFAULT 0"),
                          ("horas_extra", "REAL DEFAULT 0"),
                          ("notas", "TEXT DEFAULT ''")]:
            if _c not in _cols:
                con.execute(f"ALTER TABLE comisiones ADD COLUMN {_c} {_decl}")
        con.execute("""
            CREATE TABLE IF NOT EXISTS pesos (concepto TEXT PRIMARY KEY, peso REAL)
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS exclusivos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT,        -- dia de la visita (YYYY-MM-DD)
                nombre TEXT,       -- nombre de la persona
                importe REAL)      -- cobrado, SIN IVA (no llevan IVA)
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS horarios (
                desde TEXT PRIMARY KEY,   -- vigente DESDE esta fecha (YYYY-MM-DD)
                lun REAL, mar REAL, mie REAL, jue REAL, vie REAL, sab REAL, dom REAL)
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS plantilla_hist (
                empleado TEXT, desde TEXT,     -- sueldo/horas vigentes DESDE esta fecha
                sueldo REAL, horas_sem REAL,
                PRIMARY KEY (empleado, desde))
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS clientes_excluidos (
                cod TEXT PRIMARY KEY, nombre TEXT, motivo TEXT, fecha TEXT)
        """)
        con.commit()


# ---- Altas/bajas de empleados (mapa nombre_TPV -> nombre bonito + activo) ---- #
def _fnorm(x):
    """Fecha -> 'YYYY-MM-DD' o '' si vacia."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip()
    return "" if not s or s.lower() in ("none", "nat") else s[:10]


def guardar_empleados(lista: list):
    """lista de {'nombre_tpv','nombre_bonito','activo'(0/1),'fecha_alta','fecha_baja'}."""
    init_config()
    with get_conn() as con:
        for e in lista:
            con.execute(
                "INSERT OR REPLACE INTO empleados "
                "(nombre_tpv, nombre_bonito, activo, fecha_alta, fecha_baja) "
                "VALUES (?,?,?,?,?)",
                (e["nombre_tpv"], e["nombre_bonito"], int(e.get("activo", 0)),
                 _fnorm(e.get("fecha_alta")), _fnorm(e.get("fecha_baja"))))
        con.commit()


def leer_empleados(defecto_activos: dict) -> list:
    """Devuelve lista de empleados guardados. Siembra con defecto (todos activos)."""
    init_config()
    with get_conn() as con:
        rows = con.execute(
            "SELECT nombre_tpv, nombre_bonito, activo, fecha_alta, fecha_baja "
            "FROM empleados").fetchall()
    if not rows:
        semilla = [{"nombre_tpv": k, "nombre_bonito": v, "activo": 1,
                    "fecha_alta": "", "fecha_baja": ""}
                   for k, v in defecto_activos.items()]
        guardar_empleados(semilla)
        return semilla
    return [{"nombre_tpv": t, "nombre_bonito": b, "activo": bool(a),
             "fecha_alta": _fnorm(fa), "fecha_baja": _fnorm(fb)}
            for t, b, a, fa, fb in rows]


def empleados_activos_dict(defecto_activos: dict) -> dict:
    """{nombre_tpv: nombre_bonito} solo de los activos."""
    return {e["nombre_tpv"]: e["nombre_bonito"]
            for e in leer_empleados(defecto_activos) if e["activo"]}


# ---- Comisiones por empleado y mes ---- #
def _num(x):
    v = pd.to_numeric(x, errors="coerce")
    return 0.0 if pd.isna(v) else float(v)


def guardar_comisiones(mes: str, dic: dict):
    """dic = {nombre: {individual, general, productos, extra, horas_extra, notas}}.
    'extra' son los euros de las horas extra (ya calculados = horas_extra * precio).
    El total (importe) = individual + general + productos + extra."""
    init_config()
    with get_conn() as con:
        for emp, c in dic.items():
            ind, gen = _num(c.get("individual")), _num(c.get("general"))
            pro, ext = _num(c.get("productos")), _num(c.get("extra"))
            hext = _num(c.get("horas_extra"))
            notas = str(c.get("notas") or "").strip()
            total = ind + gen + pro + ext
            con.execute(
                "INSERT OR REPLACE INTO comisiones "
                "(empleado, mes, individual, general, productos, extra, horas_extra, "
                "notas, importe) VALUES (?,?,?,?,?,?,?,?,?)",
                (emp, mes, ind, gen, pro, ext, hext, notas, total))
        con.commit()


def leer_comisiones(mes: str) -> dict:
    """{empleado: importe_total} — lo que se resta en Rentabilidad."""
    init_config()
    with get_conn() as con:
        rows = con.execute(
            "SELECT empleado, importe FROM comisiones WHERE mes=?", (mes,)).fetchall()
    return {emp: imp for emp, imp in rows}


def leer_comisiones_detalle(mes: str) -> dict:
    """{empleado: {individual, general, productos, extra, horas_extra, notas}}."""
    init_config()
    with get_conn() as con:
        rows = con.execute(
            "SELECT empleado, individual, general, productos, extra, horas_extra, "
            "notas FROM comisiones WHERE mes=?", (mes,)).fetchall()
    return {r[0]: {"individual": r[1] or 0, "general": r[2] or 0,
                   "productos": r[3] or 0, "extra": r[4] or 0,
                   "horas_extra": r[5] or 0, "notas": r[6] or ""} for r in rows}


# ---- Pesos por concepto ---- #
def guardar_pesos(dic: dict):
    """Reemplaza por completo la tabla de pesos con dic = {concepto: peso}."""
    init_config()
    with get_conn() as con:
        con.execute("DELETE FROM pesos")
        for concepto, peso in dic.items():
            con.execute("INSERT OR REPLACE INTO pesos (concepto, peso) VALUES (?,?)",
                        (concepto, float(peso)))
        con.commit()


def leer_pesos(defecto: dict) -> dict:
    init_config()
    with get_conn() as con:
        rows = con.execute("SELECT concepto, peso FROM pesos").fetchall()
    if not rows:
        guardar_pesos(defecto)
        return dict(defecto)
    return {c: p for c, p in rows}


def guardar_plantilla(plantilla: dict):
    """plantilla = {nombre: {'sueldo': float, 'horas_sem': float|None}}."""
    init_config()
    with get_conn() as con:
        for emp, info in plantilla.items():
            con.execute(
                "INSERT OR REPLACE INTO plantilla (empleado, sueldo, horas_sem) "
                "VALUES (?,?,?)",
                (emp, info.get("sueldo"), info.get("horas_sem")))
        con.commit()


def leer_plantilla(defecto: dict) -> dict:
    """Lee sueldos/horas de la BD. Si esta vacia, la siembra con `defecto`."""
    init_config()
    with get_conn() as con:
        rows = con.execute(
            "SELECT empleado, sueldo, horas_sem FROM plantilla").fetchall()
    if not rows:
        guardar_plantilla(defecto)
        return {k: dict(v) for k, v in defecto.items()}
    return {emp: {"sueldo": sueldo, "horas_sem": horas}
            for emp, sueldo, horas in rows}


# ---- Ingresos de personas exclusivas (entrada manual, NO vienen del TPV) ---- #
def leer_exclusivos() -> pd.DataFrame:
    """Lista de visitas exclusivas (fecha, nombre, importe SIN IVA)."""
    init_config()
    with get_conn() as con:
        df = pd.read_sql("SELECT fecha, nombre, importe FROM exclusivos "
                         "ORDER BY fecha", con)
    return df


def guardar_exclusivos(df: pd.DataFrame) -> int:
    """Reemplaza la tabla entera con las filas de df (fecha, nombre, importe).
    Ignora filas sin importe valido. Devuelve cuantas se guardaron."""
    init_config()
    rows = []
    for _, r in df.iterrows():
        imp = pd.to_numeric(r.get("importe"), errors="coerce")
        fecha = r.get("fecha")
        nombre = str(r.get("nombre") or "").strip()
        if pd.isna(imp) or (not nombre and pd.isna(fecha)):
            continue
        f = "" if pd.isna(fecha) or fecha is None else str(fecha)[:10]
        rows.append((f, nombre, float(imp)))
    with get_conn() as con:
        con.execute("DELETE FROM exclusivos")
        con.executemany(
            "INSERT INTO exclusivos (fecha, nombre, importe) VALUES (?,?,?)", rows)
        con.commit()
    return len(rows)


# ---- Horario de apertura con vigencia (horas abiertas por dia de semana) ---- #
_HOR_COLS = ["desde", "lun", "mar", "mie", "jue", "vie", "sab", "dom"]


def leer_horarios(defecto_desde: str = "2000-01-01", defecto: dict | None = None):
    """Devuelve los horarios (uno por fecha de vigencia), ordenados por 'desde'.
    Si no hay ninguno y se pasa `defecto`, siembra una fila desde `defecto_desde`."""
    init_config()
    with get_conn() as con:
        df = pd.read_sql(
            "SELECT desde, lun, mar, mie, jue, vie, sab, dom "
            "FROM horarios ORDER BY desde", con)
    if df.empty and defecto is not None:
        guardar_horarios(pd.DataFrame([{"desde": defecto_desde, **defecto}]))
        with get_conn() as con:
            df = pd.read_sql(
                "SELECT desde, lun, mar, mie, jue, vie, sab, dom "
                "FROM horarios ORDER BY desde", con)
    return df


def guardar_horarios(df: pd.DataFrame) -> int:
    """Reemplaza toda la tabla con las filas de df (desde + horas por dia).
    Ignora filas sin fecha 'desde'. Devuelve cuantas se guardaron."""
    init_config()
    rows = []
    for _, r in df.iterrows():
        desde = r.get("desde")
        if pd.isna(desde) or not str(desde).strip():
            continue
        rows.append((str(desde)[:10],
                     _num(r.get("lun")), _num(r.get("mar")), _num(r.get("mie")),
                     _num(r.get("jue")), _num(r.get("vie")), _num(r.get("sab")),
                     _num(r.get("dom"))))
    with get_conn() as con:
        con.execute("DELETE FROM horarios")
        con.executemany(
            "INSERT OR REPLACE INTO horarios "
            "(desde, lun, mar, mie, jue, vie, sab, dom) VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
    return len(rows)


# ---- Sueldos y horas por empleado CON vigencia (fecha 'desde') ---- #
def leer_plantilla_hist(defecto_actual: dict | None = None,
                        defecto_desde: str = "2000-01-01") -> pd.DataFrame:
    """Historial de sueldo/horas por empleado (una fila por fecha de vigencia).
    Si esta vacio y se pasa `defecto_actual` ({nombre:{sueldo,horas_sem}}), lo
    siembra con esos valores vigentes desde `defecto_desde`."""
    init_config()
    with get_conn() as con:
        df = pd.read_sql(
            "SELECT empleado, desde, sueldo, horas_sem FROM plantilla_hist "
            "ORDER BY empleado, desde", con)
    if df.empty and defecto_actual:
        seed = pd.DataFrame([
            {"empleado": k, "desde": defecto_desde,
             "sueldo": v.get("sueldo"), "horas_sem": v.get("horas_sem")}
            for k, v in defecto_actual.items()])
        guardar_plantilla_hist(seed)
        with get_conn() as con:
            df = pd.read_sql(
                "SELECT empleado, desde, sueldo, horas_sem FROM plantilla_hist "
                "ORDER BY empleado, desde", con)
    return df


def guardar_plantilla_hist(df: pd.DataFrame) -> int:
    """Reemplaza toda la tabla. Ignora filas sin empleado o sin fecha. Las horas
    vacias se guardan como NULL (empleado sin horas conocidas)."""
    init_config()
    rows = []
    for _, r in df.iterrows():
        emp = str(r.get("empleado") or "").strip()
        desde = r.get("desde")
        if not emp or pd.isna(desde) or not str(desde).strip():
            continue
        s = pd.to_numeric(r.get("sueldo"), errors="coerce")
        h = pd.to_numeric(r.get("horas_sem"), errors="coerce")
        rows.append((emp, str(desde)[:10],
                     0.0 if pd.isna(s) else float(s),
                     None if pd.isna(h) else float(h)))
    with get_conn() as con:
        con.execute("DELETE FROM plantilla_hist")
        con.executemany(
            "INSERT OR REPLACE INTO plantilla_hist "
            "(empleado, desde, sueldo, horas_sem) VALUES (?,?,?,?)", rows)
        con.commit()
    return len(rows)


# ---- Clientes excluidos de la campana de recuperacion (fallecidos, fichas dobles) ---- #
def leer_excluidos() -> pd.DataFrame:
    init_config()
    with get_conn() as con:
        return pd.read_sql(
            "SELECT cod, nombre, motivo, fecha FROM clientes_excluidos "
            "ORDER BY nombre", con)


def leer_excluidos_cods() -> set:
    init_config()
    with get_conn() as con:
        rows = con.execute("SELECT cod FROM clientes_excluidos").fetchall()
    return {str(r[0]) for r in rows}


def excluir_clientes(rows: list, fecha: str | None = None) -> int:
    """rows = lista de {'cod','nombre','motivo'}. Los marca como excluidos."""
    init_config()
    f = fecha or pd.Timestamp.today().strftime("%Y-%m-%d")
    n = 0
    with get_conn() as con:
        for r in rows:
            cod = str(r.get("cod") or "").strip()
            if not cod:
                continue
            con.execute(
                "INSERT OR REPLACE INTO clientes_excluidos "
                "(cod, nombre, motivo, fecha) VALUES (?,?,?,?)",
                (cod, str(r.get("nombre") or ""), str(r.get("motivo") or ""), f))
            n += 1
        con.commit()
    return n


def restaurar_excluidos(cods: list) -> int:
    init_config()
    with get_conn() as con:
        for c in cods:
            con.execute("DELETE FROM clientes_excluidos WHERE cod=?", (str(c),))
        con.commit()
    return len(cods)


def leer_ajuste(clave: str, defecto=None):
    init_config()
    with get_conn() as con:
        r = con.execute(
            "SELECT valor FROM ajustes WHERE clave=?", (clave,)).fetchone()
    return r[0] if r else defecto


def guardar_ajuste(clave: str, valor):
    init_config()
    with get_conn() as con:
        con.execute("INSERT OR REPLACE INTO ajustes (clave, valor) VALUES (?,?)",
                    (clave, str(valor)))
        con.commit()
