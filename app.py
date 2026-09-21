"""
Dashboard de KPIs para una peluqueria (proyecto de demostracion).

Capa de analitica que se alimenta de los Excel exportados desde
"TPV 123 peluqueros". No sustituye al TPV: solo lee sus datos y calcula
las estadisticas que el programa no ofrece.

Ejecutar con:   streamlit run app.py
"""

import datetime as dt
import hashlib
import hmac
import math
import secrets
from pathlib import Path
from urllib.parse import quote

import matplotlib.colors as mcolors
import pandas as pd
import plotly.express as px
import streamlit as st

import ingest
import genero

# Modelo de fuga (churn) OPCIONAL: solo si scikit-learn esta instalado. Si no,
# la app funciona igual y el "modo IA" cae al modo por ritmo.
try:
    import numpy as _np
    from sklearn.linear_model import LogisticRegression as _LogReg
    from sklearn.preprocessing import StandardScaler as _Scaler
    from sklearn.pipeline import make_pipeline as _make_pipeline
    from sklearn.metrics import (precision_score as _prec, recall_score as _rec,
                                 f1_score as _f1)
    _SKLEARN_OK = True
except Exception:
    _SKLEARN_OK = False

_FEATS_CHURN = ["recencia", "ritmo", "ritmo_iqr", "retraso_x", "antiguedad",
                "n_visitas", "freq_anual", "gasto_total", "gasto_medio",
                "visitas_6m", "tendencia"]

# --------------------------------------------------------------------------- #
# Paleta / estilo (tonos beige)
# --------------------------------------------------------------------------- #
BG_MAIN = "#F4EDDE"
BG_CARD = "#EAE0CC"
TEXT = "#3D372E"
ACCENT = "#A75D3B"
ACCENT_SOFT = "#C98A63"
BORDER = "#D9CDB2"
# Imagen de cabecera (si existe). Guarda tu banner aqui: assets/cabecera.png (o .jpg)
CAB_DIR = Path(__file__).parent / "assets"
CAB_IMG = next((p for p in [CAB_DIR / "cabecera.png", CAB_DIR / "cabecera.jpg",
                            CAB_DIR / "cabecera.jpeg"] if p.exists()), None)

# Colores bien distinguibles para cada año: marrón, amarillo, verde, rojo, azul...
PALETA_ANIOS = ["#7A4A25", "#E0A500", "#2E8B4A", "#C0392B", "#2C6E9B", "#7D3C98",
                "#E67E22"]
# Mapa de calor: blanco (0) -> granate muy intenso (alto)
CMAP_CALOR = mcolors.LinearSegmentedColormap.from_list(
    "calor_granate", ["#ffffff", "#e9b7b0", "#c56a5c", "#8c2f2a", "#5c0d13"])

# Plantilla INICIAL (solo siembra la BD la primera vez). Despues se gestiona desde
# la pestaña "Ajustes". clave = nombre normalizado en la caja, valor = nombre bonito.
EMPLEADOS_DEFECTO = {
    "Ana G": "Ana",
    "Marta L": "Marta",
    "Lucia R": "Lucía",
    "Sara P": "Sara",
    "Carlos M": "Carlos",
    "Elena N": "Elena",
}

# Cliente "cajon de sastre" del TPV (quien paga sin ficha). Se excluye de las
# estadisticas de clientes porque no es una persona real y distorsiona medias.
GENERICO = "Generico"

# Valores INICIALES de sueldo mensual "en sucio" y horas semanales. Solo se usan
# la primera vez para sembrar la base de datos; a partir de ahi se editan desde la
# pestaña "Ajustes" y se guardan. horas_sem=None si no se sabe (p.ej. un socio sin horario fijo).
PLANTILLA_DEFECTO = {
    "Ana":    {"sueldo": 1400, "horas_sem": 34},
    "Marta":  {"sueldo": 1600, "horas_sem": 40},
    "Lucía":  {"sueldo": 1500, "horas_sem": 36},
    "Sara":   {"sueldo": 1500, "horas_sem": 36},
    "Carlos": {"sueldo": 1800, "horas_sem": None},
    "Elena":  {"sueldo": 1450, "horas_sem": 29},
}
SEMANAS_MES = 52 / 12  # ~4,33 semanas por mes, para pasar horas/semana a horas/mes

# Horario de apertura INICIAL (horas abiertas por dia). Se siembra en la BD con
# vigencia desde siempre; luego se edita en "Ajustes" con fecha de vigencia.
# Lun/Sab medio dia (10-14 = 4h); Mar-Vie 10-19 = 9h; Dom cerrado.
HORARIO_DEFECTO = {"lun": 4.0, "mar": 9.0, "mie": 9.0, "jue": 9.0, "vie": 9.0,
                   "sab": 4.0, "dom": 0.0}
DIAS_KEYS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]

# Pesos INICIALES (siembran la BD; luego se editan en "Ajustes"). Un corte de
# 30 min = 1. Lo que no este en la lista pesa 1. Claves = concepto en minusculas.
PESOS_DEFECTO = {
    "corte femenino": 1.0, "color": 1.0, "corte masculino": 1.0,
    "color plus": 1.0, "penado medio": 1.0, "peinado medio": 1.0,
    "luminosite": 0.67, "peinado corto": 0.67, "hidratacion": 0.33,
    "pack mampo color": 1.0, "peinado largo": 1.33, "pack metal detox color": 1.5,
    "balayage": 1.0, "balayage brosse": 2.0, "corte niño": 0.67, "corte niña": 0.67,
    "baño kerastase": 0.3, "hidratacion kerastase": 0.3, "foundant kerastase": 0.3,
    "shampo": 0.2,
}
PESO_DEFECTO = 1.0

st.set_page_config(page_title="Salón BI", page_icon=None, layout="wide")

# Todas las graficas SIN la barra de herramientas negra y con tooltips bonitos.
_st_plotly_chart_orig = st.plotly_chart


def _plotly_limpio(fig, *args, **kwargs):
    try:
        fig.update_layout(hoverlabel=dict(
            bgcolor="white", bordercolor=ACCENT,
            font=dict(color=TEXT, size=13, family="sans-serif")))
    except Exception:
        pass
    kwargs.setdefault("config", {"displayModeBar": False})
    return _st_plotly_chart_orig(fig, *args, **kwargs)


st.plotly_chart = _plotly_limpio


def _sparkle(cx, cy, r, k=0.16):
    """Devuelve el 'd' de una estrella de 4 puntas (destello IA) centrada en cx,cy."""
    c = r * k
    return (f"M{cx},{cy - r} C{cx + c},{cy - c} {cx + c},{cy - c} {cx + r},{cy} "
            f"C{cx + c},{cy + c} {cx + c},{cy + c} {cx},{cy + r} "
            f"C{cx - c},{cy + c} {cx - c},{cy + c} {cx - r},{cy} "
            f"C{cx - c},{cy - c} {cx - c},{cy - c} {cx},{cy - r} Z")


# Icono de "estrellas de IA" en blanco para el boton flotante (SVG en data-URI).
_estrellas = [(40, 58, 27), (66, 32, 17), (79, 61, 11)]
_svg_ia = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
           + "".join(f"<path d='{_sparkle(cx, cy, r)}' fill='white'/>"
                     for cx, cy, r in _estrellas) + "</svg>")
SPARKLE_URI = "data:image/svg+xml," + quote(_svg_ia)

st.markdown(f"""
<style>
/* ---- Texto general un poco mas grande en toda la app ---- */
html {{ font-size: 18.5px; }}
/* ---- Fondo general beige ---- */
.stApp {{ background-color: {BG_MAIN}; color: {TEXT}; }}
section[data-testid="stSidebar"] {{ display: none; }}
div[data-testid="collapsedControl"] {{ display: none; }}
.block-container {{ padding-top: 3.6rem; padding-bottom: 3rem; max-width: 1200px; }}

/* ---- Cabecera propia ---- */
.cab-titulo {{
    font-size: 1.6rem; font-weight: 700; color: {TEXT};
    letter-spacing: .3px; margin: 0 0 .1rem 0;
}}
.cab-sub {{ color: #7d7261; font-size: .9rem; margin: 0 0 .2rem 0; }}

/* ---- Titulo pequeno encima de una grafica ---- */
.mini-tit {{
    font-size: 1.08rem; font-weight: 700; color: {TEXT};
    margin: .2rem 0 .1rem 0; line-height: 1.25;
}}
.mini-tit span {{ font-weight: 500; color: #8a7d68; font-size: .9rem; }}

/* ---- Botones de accion en rojo (AVISADO/A y FELICITADO/A) ---- */
div[class*="st-key-avisar_"] button, div[class*="st-key-felic_"] button {{
    background: #C0392B; color: #fff; border: 1px solid #C0392B;
    font-weight: 700; letter-spacing: .04em;
}}
div[class*="st-key-avisar_"] button:hover,
div[class*="st-key-felic_"] button:hover {{
    background: #A93226; border-color: #A93226; color: #fff;
}}
div[class*="st-key-avisar_"] button p,
div[class*="st-key-felic_"] button p {{ color: #fff; }}

/* ---- Boton de WhatsApp en verde ---- */
[data-testid="stLinkButton"] a {{
    background-color: #25D366 !important; border-color: #25D366 !important;
    color: #fff !important;
}}
[data-testid="stLinkButton"] a:hover {{
    background-color: #1EBE57 !important; border-color: #1EBE57 !important;
    color: #fff !important;
}}
[data-testid="stLinkButton"] a p,
[data-testid="stLinkButton"] a div {{ color: #fff !important; }}

/* ---- Asistente flotante (boton redondo + panel de chat) ---- */
div[class*="st-key-ai_launcher"] {{
    position: fixed; bottom: 22px; right: 22px; z-index: 1000; width: 62px;
}}
div[class*="st-key-ai_launcher"] button {{
    width: 62px; height: 62px; border-radius: 50%; padding: 0;
    border: none; color: transparent; font-size: 0;
    background: {ACCENT} url("{SPARKLE_URI}") center / 34px 34px no-repeat;
    box-shadow: 0 6px 20px rgba(0,0,0,.28);
}}
div[class*="st-key-ai_launcher"] button:hover {{
    background: {ACCENT_SOFT} url("{SPARKLE_URI}") center / 34px 34px no-repeat;
}}
div[class*="st-key-ai_launcher"] button p {{ font-size: 0; }}
div[class*="st-key-ai_panel"] {{
    position: fixed; bottom: 96px; right: 22px; width: 370px;
    max-width: calc(100vw - 44px); max-height: 74vh; overflow-y: auto;
    z-index: 1000; background: {BG_CARD}; border: 1px solid {BORDER};
    border-radius: 16px; padding: 16px 16px 8px 16px;
    box-shadow: 0 12px 44px rgba(0,0,0,.24);
}}

/* ---- Pestanas horizontales arriba (estilo subrayado) ---- */
.stTabs [data-baseweb="tab-list"] {{
    gap: 30px;
    border-bottom: 1px solid {BORDER};
    background: transparent;
    margin-bottom: 1.2rem;
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent;
    padding: 10px 2px;
    font-size: 1rem;
    color: #8a7d68;
    font-weight: 500;
}}
.stTabs [data-baseweb="tab"]:hover {{ color: {TEXT}; }}
.stTabs [aria-selected="true"] {{ color: {TEXT}; font-weight: 700; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {ACCENT}; height: 3px; }}
.stTabs [data-baseweb="tab-border"] {{ background-color: transparent; }}

/* ---- Metricas como tarjetas ---- */
div[data-testid="stMetric"] {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 14px 18px;
}}
div[data-testid="stMetricValue"] {{ color: {ACCENT}; }}

/* ---- Botones ---- */
.stButton > button, .stDownloadButton > button {{
    background: {ACCENT}; color: #fff; border: none; border-radius: 8px;
    font-weight: 600;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    background: #8f4c2e; color: #fff;
}}

/* ---- Tablas / dataframes ---- */
div[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 10px; }}

h3 {{ color: {TEXT}; margin-top: .4rem; }}

/* ---- Marca / logo ---- */
.brand {{ text-align: center; margin: .6rem 0 .3rem 0; }}
.brand-title {{
    font-family: "Arial Narrow", "Segoe UI", Helvetica, sans-serif;
    font-size: 2.6rem; font-weight: 800; color: {TEXT};
    letter-spacing: .55em; text-indent: .55em;
    line-height: 1.35; padding-top: .25rem;
}}
.brand-sub {{
    display: flex; align-items: center; justify-content: center;
    gap: 14px; margin-top: 8px;
    color: {TEXT}; font-size: .82rem; font-weight: 600; letter-spacing: .32em;
}}
.brand-sub::before, .brand-sub::after {{
    content: ""; height: 2px; width: 64px; background: {TEXT};
}}
.brand-line {{
    height: 1px; background: {BORDER}; margin: 1rem 0 .2rem 0;
}}

/* ---- Portada de bienvenida ---- */
.portada {{ text-align: center; margin: 8vh 0 1.5rem 0; }}
.portada .brand-title {{ font-size: 3.6rem; }}
.portada-bien {{
    margin-top: 1.6rem; font-size: 1.25rem; color: {TEXT}; font-weight: 600;
}}
.portada-sub {{ margin-top: .3rem; color: #8a7d68; font-size: .95rem; }}

/* ---- Indice lateral de secciones dentro de una pestaña ---- */
.sec-anchor {{ display: block; height: 0; scroll-margin-top: 75px; }}
.indice {{ position: sticky; top: 70px; }}
.indice-tit {{
    font-weight: 700; font-size: .72rem; letter-spacing: .06em;
    text-transform: uppercase; color: #8a7d68; margin: .2rem 0 .5rem 4px;
}}
.indice a {{
    display: block; padding: 6px 10px; margin-bottom: 2px;
    color: #6b6155; text-decoration: none; font-size: .9rem;
    border-left: 2px solid transparent; border-radius: 0 6px 6px 0;
}}
.indice a:hover {{
    color: {TEXT}; background: {BG_CARD}; border-left-color: {ACCENT};
}}
/* Indice FIJO a la izquierda, disponible en todas las pestañas */
.indice-fix {{
    position: fixed; top: 116px; left: 24px; width: 158px;
    max-height: 78vh; overflow-y: auto; z-index: 50;
    padding: 6px 4px; background: #F4EDDE; border-radius: 10px;
}}
.block-container, [data-testid="stMainBlockContainer"] {{
    max-width: 100% !important; margin-left: 0 !important; margin-right: 0 !important;
    padding-left: 200px !important; padding-right: 3rem !important;
}}
@media (max-width: 640px) {{
    .indice-fix {{ display: none; }}
    .block-container, [data-testid="stMainBlockContainer"] {{
        padding-left: 1rem !important;
    }}
}}

/* ---- Subidor de archivos: mas grande y en español ---- */
[data-testid="stFileUploaderDropzone"] {{
    min-height: 165px;
    padding: 26px;
    border: 2px dashed {ACCENT};
    background: {BG_CARD};
    border-radius: 14px;
    flex-direction: column;
    gap: 12px;
    text-align: center;
}}
[data-testid="stFileUploaderDropzoneInstructions"] span,
[data-testid="stFileUploaderDropzoneInstructions"] small {{ display: none; }}
[data-testid="stFileUploaderDropzoneInstructions"]::after {{
    content: "Arrastra aquí tus archivos Excel";
    display: block; font-size: 1.1rem; font-weight: 700; color: {TEXT};
}}
[data-testid="stFileUploaderDropzone"] button {{
    color: transparent !important; position: relative;
    min-width: 200px; background: {ACCENT} !important; border: none !important;
}}
[data-testid="stFileUploaderDropzone"] button::after {{
    content: "Seleccionar archivos"; color: #fff; font-weight: 600;
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
}}

/* ---- Banner de aviso de campaña ---- */
.aviso {{
    background: {ACCENT}; color: #fff;
    padding: 14px 18px; border-radius: 10px;
    font-weight: 700; font-size: 1.05rem;
    margin: .4rem 0 1.1rem 0;
}}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Portada de bienvenida + acceso opcional con contraseña
# --------------------------------------------------------------------------- #
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False


def _hash_password(pwd, salt=None, iters=200_000):
    """Devuelve la contraseña en formato seguro 'pbkdf2$iters$salt$hash'.
    Nunca se guarda la contraseña en texto plano."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def _verify_password(pwd, stored):
    """Comprueba la contraseña contra lo guardado (hash nuevo o texto plano
    antiguo, para no romper instalaciones ya existentes)."""
    if not stored:
        return True                              # sin contraseña configurada
    if stored.startswith("pbkdf2$"):
        try:
            _, it, salt_hex, hash_hex = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(it))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False
    return hmac.compare_digest(pwd, stored)      # legado en texto plano


_PASSWORD = str(ingest.leer_ajuste("password", "") or "")

if not st.session_state.autenticado:
    _deco = (
        "<svg viewBox='0 0 300 100' width='150' "
        "style='max-width:45%;height:auto;margin-bottom:.4rem'>"
        "<g stroke='#3D372E' stroke-width='2.4' fill='none' stroke-linecap='round'>"
        "<circle cx='141' cy='80' r='7'/><circle cx='159' cy='80' r='7'/>"
        "<path d='M146,75 L172,34'/><path d='M154,75 L128,34'/>"
        "<circle cx='150' cy='64' r='2.2' fill='#3D372E' stroke='none'/></g></svg>")
    st.markdown(
        "<div class='portada'>"
        f"{_deco}"
        "<div class='brand-title'>SALÓN BI</div>"
        "<div class='brand-sub'>DASHBOARD DE PELUQUERÍA</div>"
        "<div class='portada-bien'>Bienvenido/a a Salón BI</div>"
        "<div class='portada-sub'>Peluquería demo</div>"
        "</div>", unsafe_allow_html=True)
    _c = st.columns([1, 1.2, 1])[1]
    with _c:
        _pwd = ""
        if _PASSWORD:
            _pwd = st.text_input("Contraseña", type="password", key="login_pwd")
        if st.button("Entrar", type="primary", width="stretch"):
            if _verify_password(_pwd, _PASSWORD):
                st.session_state.autenticado = True
                # Si estaba guardada en texto plano (instalación antigua), la
                # migramos a hash de forma transparente al primer acceso correcto.
                if _PASSWORD and not _PASSWORD.startswith("pbkdf2$"):
                    ingest.guardar_ajuste("password", _hash_password(_pwd))
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
    st.stop()


def eur(x, dec=0):
    """Formato europeo: miles con punto, decimales con coma. 1234.5 -> '1.234,5'."""
    try:
        s = f"{float(x):,.{dec}f}"           # 1,234.50  (estilo ingles)
        return s.replace(",", "§").replace(".", ",").replace("§", ".")
    except (ValueError, TypeError):
        return str(x)


_MESES_ABBR = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago",
               "Sep", "Oct", "Nov", "Dic"]


def _svg_vbars(pares, color=ACCENT, w=560, h=210):
    """Grafico de barras verticales (para incrustar en el informe HTML)."""
    if not pares:
        return ""
    mx = max((v for _, v in pares), default=0) or 1
    n = len(pares); pad = 30; base = h - 26
    gap = (w - 2 * pad) / n; bw = min(46, gap * 0.6)
    out = [f'<line x1="{pad}" y1="{base}" x2="{w - pad}" y2="{base}" '
           f'stroke="#D9CDB2"/>']
    for i, (lab, v) in enumerate(pares):
        bh = (base - 18) * (max(0, v) / mx)
        x = pad + i * gap + (gap - bw) / 2; y = base - bh
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                   f'height="{bh:.1f}" fill="{color}" rx="2"/>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 13:.0f}" font-size="9" '
                   f'fill="#6b6155" text-anchor="middle">{lab}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{y - 3:.1f}" font-size="8" '
                   f'fill="#3D372E" text-anchor="middle">{eur(v)}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
            f'style="max-width:{w}px;height:auto">{"".join(out)}</svg>')


def _svg_hbars(pares, color=ACCENT, w=560):
    """Grafico de barras horizontales."""
    if not pares:
        return ""
    n = len(pares); rh = 26; h = n * rh + 6
    mx = max((v for _, v in pares), default=0) or 1
    labw = 150; barw = w - labw - 70
    out = []
    for i, (lab, v) in enumerate(pares):
        y = i * rh + 4; bw = barw * (max(0, v) / mx)
        lab2 = (lab[:22] + "…") if len(str(lab)) > 23 else lab
        out.append(f'<text x="0" y="{y + 15:.0f}" font-size="10" '
                   f'fill="#3D372E">{lab2}</text>')
        out.append(f'<rect x="{labw}" y="{y:.0f}" width="{bw:.1f}" '
                   f'height="{rh - 9}" fill="{color}" rx="2"/>')
        out.append(f'<text x="{labw + bw + 5:.1f}" y="{y + 14:.0f}" font-size="10" '
                   f'fill="#6b6155">{eur(v)}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
            f'style="max-width:{w}px;height:auto">{"".join(out)}</svg>')


def _svg_grupo(labels, serie_a, serie_b, col_a, col_b, w=560, h=220):
    """Barras agrupadas de 2 series (para el año vs año)."""
    if not labels:
        return ""
    mx = max(max(serie_a, default=0), max(serie_b, default=0)) or 1
    n = len(labels); pad = 30; base = h - 26
    gap = (w - 2 * pad) / n; bw = min(18, gap * 0.32)
    out = [f'<line x1="{pad}" y1="{base}" x2="{w - pad}" y2="{base}" '
           f'stroke="#D9CDB2"/>']
    for i, lab in enumerate(labels):
        x0 = pad + i * gap + (gap - 2 * bw - 4) / 2
        for j, (v, c) in enumerate([(serie_a[i], col_a), (serie_b[i], col_b)]):
            bh = (base - 18) * (max(0, v) / mx); x = x0 + j * (bw + 4); y = base - bh
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                       f'height="{bh:.1f}" fill="{c}" rx="2"/>')
        out.append(f'<text x="{x0 + bw + 2:.1f}" y="{base + 13:.0f}" font-size="9" '
                   f'fill="#6b6155" text-anchor="middle">{lab}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
            f'style="max-width:{w}px;height:auto">{"".join(out)}</svg>')


def _svg_donut(segments, center="", size=140):
    """Rosco (donut) para el informe HTML, con leyenda. segments = [(label, valor,
    color), ...]. Calcula los porcentajes solo."""
    segs = [(str(lbl), float(v), col) for lbl, v, col in segments if float(v) > 0]
    total = sum(v for _, v, _ in segs)
    if total <= 0:
        return "<p class='per'>Sin datos en el periodo.</p>"
    cx = cy = size / 2
    r = size * 0.36
    circ = 2 * math.pi * r
    sw = r * 0.55
    offset = 0.0
    arcs, legend = [], []
    for lbl, v, col in segs:
        frac = v / total
        seg = circ * frac
        arcs.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" '
            f'stroke="{col}" stroke-width="{sw:.1f}" '
            f'stroke-dasharray="{seg:.2f} {circ - seg:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" '
            f'transform="rotate(-90 {cx:.1f} {cy:.1f})"/>')
        offset += seg
        legend.append(
            f"<div class='leg'><span class='sq' style='background:{col}'></span>"
            f"{lbl} — <b>{eur(frac * 100, 0)}%</b></div>")
    ctxt = (f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" font-size="13" '
            f'fill="#3D372E" font-weight="700">{center}</text>' if center else "")
    svg = (f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
           f'{"".join(arcs)}{ctxt}</svg>')
    return (f"<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap;"
            f"margin:.4rem 0 1rem 0'>{svg}<div>{''.join(legend)}</div></div>")


_XLSX_MIME = ("application/vnd.openxmlformats-officedocument."
              "spreadsheetml.sheet")


def _to_xlsx(df, sheet="Datos"):
    """Convierte un DataFrame a bytes .xlsx (Excel) para st.download_button."""
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet)
    return buf.getvalue()


def _feat_churn(vg, cutoff, fut_months=6, con_label=True):
    """Construye la matriz de variables por cliente en una fecha de corte.
    vg debe tener columnas fecha, k (nombre normalizado) e importe_kpi."""
    hist = vg[vg.fecha < cutoff]
    fut = set()
    if con_label:
        fut = set(vg[(vg.fecha >= cutoff)
                     & (vg.fecha < cutoff + pd.DateOffset(months=fut_months))]
                  .k.unique())
    rows = []
    for k, d in hist.groupby("k"):
        ds = pd.Series(pd.to_datetime(sorted(d.fecha.dt.normalize().unique())))
        n = len(ds)
        if n < 3:                                   # sin 3 visitas no evaluamos
            continue
        difs = ds.diff().dropna().dt.days
        difs = difs[difs > 0]
        if difs.empty:
            continue
        ritmo = float(difs.median())
        if ritmo < 7 or ritmo > 400:
            continue
        last, first = ds.iloc[-1], ds.iloc[0]
        rec = (cutoff - last).days
        if rec < 0:
            continue
        ant = max((cutoff - first).days, 1)
        iqr = float(difs.quantile(.75) - difs.quantile(.25)) if len(difs) > 1 else 0.0
        v6 = int((d.fecha >= cutoff - pd.DateOffset(months=6)).sum())
        vprev = int(((d.fecha >= cutoff - pd.DateOffset(months=12))
                     & (d.fecha < cutoff - pd.DateOffset(months=6))).sum())
        gasto = float(pd.to_numeric(d.importe_kpi, errors="coerce").fillna(0).sum())
        row = dict(k=k, recencia=rec, ritmo=ritmo, ritmo_iqr=iqr,
                   retraso_x=rec / ritmo, antiguedad=ant, n_visitas=n,
                   freq_anual=n / (ant / 365.0), gasto_total=gasto,
                   gasto_medio=gasto / n, visitas_6m=v6, tendencia=v6 - vprev)
        if con_label:
            row["churn"] = int(k not in fut)
        rows.append(row)
    return pd.DataFrame(rows)


@st.cache_resource(show_spinner="Entrenando el modelo de fuga…")
def entrenar_churn(data_version):
    """Entrena una regresion logistica de fuga con validacion TEMPORAL y devuelve
    {scores: {nombre_norm: prob}, met: (P,R,F1,n), n_train}. None si no se puede."""
    if not _SKLEARN_OK:
        return None
    try:
        vg = ventas_g[ventas_g.cliente.notna()].copy()
        vg = vg[vg.cliente.astype(str).str.strip().str.lower() != GENERICO.lower()]
        vg["k"] = vg.cliente.astype(str).map(
            lambda s: " ".join(genero._norm(s).split()))
        vg = vg[vg.k != ""]
        hoy = pd.Timestamp(dt.date.today())
        train = pd.concat(
            [_feat_churn(vg, hoy - pd.DateOffset(months=m)) for m in (24, 22, 20, 18)],
            ignore_index=True)
        valid = _feat_churn(vg, hoy - pd.DateOffset(months=12))
        hoy_f = _feat_churn(vg, hoy, con_label=False)
        if len(train) < 150 or hoy_f.empty or train["churn"].nunique() < 2:
            return None
        mdl = _make_pipeline(_Scaler(), _LogReg(max_iter=1000))
        mdl.fit(train[_FEATS_CHURN].values, train["churn"].values)
        met = None
        if not valid.empty and valid["churn"].nunique() > 1:
            vp = (mdl.predict_proba(valid[_FEATS_CHURN].values)[:, 1] >= .5).astype(int)
            met = (float(_prec(valid.churn, vp, zero_division=0)),
                   float(_rec(valid.churn, vp, zero_division=0)),
                   float(_f1(valid.churn, vp, zero_division=0)), int(len(valid)))
        proba = mdl.predict_proba(hoy_f[_FEATS_CHURN].values)[:, 1]
        return {"scores": dict(zip(hoy_f.k, proba)), "met": met,
                "n_train": int(len(train))}
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def mapa_genero(data_version):
    """Sexo estimado por nombre para cada cliente. Cacheado por versión de datos:
    solo se recalcula cuando subes datos nuevos, no en cada clic."""
    return {c: genero.inferir_genero(c)
            for c in ventas_g.cliente.dropna().unique()}


@st.cache_data(show_spinner=False)
def agg_cliente_ticket(data_version):
    """Por cliente (nombre normalizado): fecha del ÚLTIMO ticket y RITMO (mediana
    del hueco en días entre visitas, válido 7-400). Cacheado por versión de datos:
    es el cálculo más pesado de Recuperar clientes y así no se repite en cada clic."""
    v = ventas_g[ventas_g.cliente.notna()]
    v = v[v.cliente.astype(str).str.strip().str.lower() != GENERICO.lower()]
    if v.empty:
        return pd.DataFrame(columns=["ult_ticket", "ritmo"])
    tmp = pd.DataFrame({
        "_k": v.cliente.astype(str).map(
            lambda s: " ".join(genero._norm(s).split())).values,
        "_d": v.fecha.dt.normalize().values})
    ult = tmp.groupby("_k")._d.max().rename("ult_ticket")

    def _ritmo(s):
        ds = pd.Series(sorted(set(s)))
        if len(ds) < 3:
            return pd.NA
        difs = ds.diff().dropna().dt.days
        difs = difs[difs > 0]
        if difs.empty:
            return pd.NA
        rr = int(difs.median())
        return rr if 7 <= rr <= 400 else pd.NA

    rit = tmp.groupby("_k")._d.agg(_ritmo).rename("ritmo")
    return pd.concat([ult, rit], axis=1)


def estilo_fig(fig, height=340):
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, size=14),
        separators=",.",   # decimales con coma, miles con punto (formato europeo)
    )
    fig.update_xaxes(gridcolor=BORDER, zerolinecolor=BORDER)
    fig.update_yaxes(gridcolor=BORDER, zerolinecolor=BORDER)
    return fig


_MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
             "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def mes_es(periodo) -> str:
    """'2022-01' -> 'Enero 2022'. Si no tiene ese formato, lo deja igual."""
    try:
        anio, mes = str(periodo).split("-")[:2]
        return f"{_MESES_ES[int(mes) - 1]} {anio}"
    except (ValueError, IndexError):
        return str(periodo)


def eje_meses(fig, orden_labels):
    """Fuerza el eje X a mostrar los meses en orden cronologico (no alfabetico)."""
    fig.update_xaxes(type="category", categoryorder="array",
                     categoryarray=list(orden_labels))
    return fig


def leyenda_calor():
    """Barrita de color explicando el mapa de calor (mas granate = mas)."""
    st.markdown(
        "<div style='display:flex;align-items:center;gap:8px;font-size:.85rem;"
        "color:#6b6155;margin:.1rem 0 .5rem 0'>"
        "<span>menos</span>"
        f"<span style='flex:0 0 130px;height:12px;border-radius:6px;border:1px solid "
        f"{BORDER};background:linear-gradient(90deg,#ffffff,#e9b7b0,#c56a5c,#8c2f2a,"
        f"#5c0d13)'></span>"
        "<span>más</span>"
        "<span style='margin-left:6px'>· cuanto más <b style='color:#8c2f2a'>granate"
        "</b>, más alto</span></div>", unsafe_allow_html=True)


def por_mes_vista(dfl, vista, cell_fmt, y_label, line_hover, normalizar=False):
    """Muestra un cuadro emp x mes como mapa de calor o grafico de lineas.

    dfl: dataframe largo con columnas emp, mes, valor.
    normalizar: si True, escala los valores a 0..1 (valor / maximo) para que se
    lean facil aunque los numeros originales sean muy grandes.
    """
    if dfl.empty:
        st.info("Sin datos en el rango elegido.")
        return
    if normalizar:
        dfl = dfl.copy()
        _mx = dfl.valor.max()
        if _mx and _mx > 0:
            dfl["valor"] = dfl.valor / _mx
    if vista.startswith("Gráfico"):
        d = dfl.copy()
        d["mes_lbl"] = d.mes.map(mes_es)
        orden = [mes_es(m) for m in sorted(dfl.mes.unique())]
        fig = px.line(d, x="mes_lbl", y="valor", color="emp", markers=True,
                      color_discrete_sequence=PALETA_ANIOS,
                      labels={"mes_lbl": "", "valor": y_label, "emp": ""})
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=orden)
        fig.update_traces(hovertemplate=line_hover + "<extra>%{fullData.name}</extra>")
        st.plotly_chart(estilo_fig(fig, 380), width="stretch")
    else:
        piv = dfl.pivot(index="emp", columns="mes", values="valor").fillna(0)
        piv.columns = [mes_es(c) for c in piv.columns]
        leyenda_calor()
        st.dataframe(piv.style.format(cell_fmt)
                     .background_gradient(cmap=CMAP_CALOR, axis=None),
                     width="stretch")


def ancla(anchor_id):
    """Punto de destino invisible para los enlaces del indice lateral."""
    st.markdown(f'<div id="{anchor_id}" class="sec-anchor"></div>',
                unsafe_allow_html=True)


def indice_lateral(pares):
    """Renderiza el indice de secciones (lista de (id, etiqueta)) pegajoso."""
    links = "".join(f'<a href="#{i}">{lbl}</a>' for i, lbl in pares)
    st.markdown(f"<div class='indice'><div class='indice-tit'>En esta pestaña</div>"
                f"{links}</div>", unsafe_allow_html=True)


def menu_lateral(pares):
    """Indice FIJO a la izquierda (mismo look), para cualquier pestaña. No necesita
    columna: flota en el margen izquierdo y solo se ve en la pestaña activa."""
    links = "".join(f'<a href="#{i}">{lbl}</a>' for i, lbl in pares)
    st.markdown(f"<div class='indice indice-fix'><div class='indice-tit'>En esta "
                f"pestaña</div>{links}</div>", unsafe_allow_html=True)


def wa_url(movil, texto="", web=False):
    """Devuelve un enlace para escribir por WhatsApp a ese movil (o None si no hay un
    numero valido). Toma el primer numero, se queda con los digitos y le pone el
    prefijo 34 (España). Si web=True usa WhatsApp Web (web.whatsapp.com), que pinta
    bien los emojis; si no, wa.me (que puede abrir la app de escritorio)."""
    tok = str(movil or "").strip().split()
    if not tok:
        return None
    tel = "".join(c for c in tok[0] if c.isdigit())
    if len(tel) < 9:
        return None
    if not tel.startswith("34"):
        tel = "34" + tel[-9:]
    if web:
        base = f"https://web.whatsapp.com/send?phone={tel}"
        return base + (f"&text={quote(texto)}" if texto else "")
    return f"https://wa.me/{tel}?text={quote(texto)}" if texto else f"https://wa.me/{tel}"


# --------------------------------------------------------------------------- #
# Carga de datos (cacheada)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def cargar_datos(_version: int):
    ventas = ingest.leer_ventas()
    clientes = ingest.leer_tabla("clientes")
    productos = ingest.leer_tabla("productos")
    servicios = ingest.leer_tabla("servicios")
    for col in ["ultima_visita", "fecha_alta"]:
        if not clientes.empty and col in clientes.columns:
            clientes[col] = pd.to_datetime(clientes[col], errors="coerce")
    return ventas, clientes, productos, servicios


if "data_version" not in st.session_state:
    st.session_state.data_version = 0


def refrescar():
    st.session_state.data_version += 1
    cargar_datos.clear()


ventas, clientes, productos, servicios = cargar_datos(st.session_state.data_version)
hay_datos = not ventas.empty
# Facturacion = Ventas Generales + Devoluciones/Rectificativas (importes negativos),
# para que cuadre con el TPV (facturacion neta). Las devoluciones nuevas que se
# suban en meses futuros entran solas porque van en su propia seccion.
if hay_datos:
    _secc = ventas.seccion.astype(str)
    ventas_g = ventas[(_secc == "Ventas Generales")
                      | _secc.str.startswith("Devoluci")].copy()
else:
    ventas_g = ventas
set_serv = set(servicios.nombre.str.strip().str.lower()) if not servicios.empty else set()
set_prod = set(productos.nombre.str.strip().str.lower()) if not productos.empty else set()

# Config editable leida de la BD (empleados activos y pesos). Se gestiona en Ajustes.
_emp_cfg = ingest.leer_empleados(EMPLEADOS_DEFECTO)
_HOY = dt.date.today()


def _activo_ahora(e):
    """Activo hoy = flag activo Y dentro de su ventana alta/baja (si tiene)."""
    if not e.get("activo"):
        return False
    fa, fb = e.get("fecha_alta") or "", e.get("fecha_baja") or ""
    try:
        if fa and dt.date.fromisoformat(fa) > _HOY:
            return False
    except ValueError:
        pass
    try:
        if fb and dt.date.fromisoformat(fb) < _HOY:
            return False
    except ValueError:
        pass
    return True


# Activos HOY (para plantilla, selectores y recuento actual).
EMPLEADOS_ACTIVOS = {e["nombre_tpv"]: e["nombre_bonito"]
                     for e in _emp_cfg if _activo_ahora(e)}
# TODOS los configurados (activos o no): para que un ex-empleado siga saliendo en
# los periodos del histórico en que sí trabajó.
EMPLEADOS_TODOS = {e["nombre_tpv"]: e["nombre_bonito"] for e in _emp_cfg}
PESOS = ingest.leer_pesos(PESOS_DEFECTO)

# --- Correccion de IVA en servicios ---
# Los servicios en Espana siempre llevan 21%. Si una linea de servicio viene con
# el impuesto a 0 (se les olvido ponerlo en el TPV), el total ya es el precio
# final que pago el cliente (IVA incluido), asi que recalculamos la base = total
# / 1,21 y el IVA = total − base. El total NO cambia. Solo servicios (no productos).
IVA_SERVICIOS = 0.21
if hay_datos:
    _es_prod = ventas_g.concepto_limpio.str.strip().str.lower().isin(set_prod)
    _falta_iva = (~_es_prod) & (ventas_g.impuesto.fillna(0).abs() < 0.005) \
        & (ventas_g.total.abs() > 0.005)   # tambien devoluciones (total negativo)
    _n_corr = int(_falta_iva.sum())
    if _n_corr:
        ventas_g.loc[_falta_iva, "importe"] = \
            ventas_g.loc[_falta_iva, "total"] / (1 + IVA_SERVICIOS)
        ventas_g.loc[_falta_iva, "impuesto"] = \
            ventas_g.loc[_falta_iva, "total"] - ventas_g.loc[_falta_iva, "importe"]
    # Las devoluciones restan dinero pero NO cuentan como items de trabajo hecho.
    _es_devol = ventas_g.seccion.astype(str).str.startswith("Devoluci")
    ventas_g.loc[_es_devol, "cantidad"] = 0
else:
    _n_corr = 0

# Peso por linea (items ponderados = cantidad * peso del concepto).
if hay_datos:
    _peso = (ventas_g.concepto_limpio.str.strip().str.lower()
             .map(PESOS).fillna(PESO_DEFECTO))
    ventas_g["items_pond"] = ventas_g.cantidad * _peso
    # Sexo del cliente (estimado por nombre) para poder filtrar el carro medio.
    _gmap = mapa_genero(st.session_state.data_version)
    ventas_g["genero"] = ventas_g.cliente.map(_gmap).fillna("Desconocido")

# Subconjunto con TODOS los empleados configurados (incluye ex-empleados para que
# aparezcan en los periodos que trabajaron; los inactivos no tienen ventas recientes).
if hay_datos:
    ventas_emp = ventas_g[ventas_g.empleado_norm.isin(EMPLEADOS_TODOS)].copy()
    ventas_emp["emp"] = ventas_emp.empleado_norm.map(EMPLEADOS_TODOS)
else:
    ventas_emp = ventas_g.copy()


def carro_medio(df):
    """Carro medio = facturacion / numero de tickets distintos."""
    tk = df.numero.nunique()
    return df.importe_kpi.sum() / tk if tk else 0.0


def aviso_sin_datos():
    st.info("Todavia no hay datos cargados. Ve a la pestaña **Actualizar datos** "
            "y sube los Excel exportados de 123 (Caja, Clientes, Productos, Servicios).")


# Config editable (sueldos/horas + ajustes) leida de la BD en cada recarga.
_PLANTILLA_DB = ingest.leer_plantilla(PLANTILLA_DEFECTO)
PLANTILLA = {
    nombre: _PLANTILLA_DB.get(nombre,
                              PLANTILLA_DEFECTO.get(nombre,
                                                    {"sueldo": 0.0, "horas_sem": None}))
    for nombre in EMPLEADOS_ACTIVOS.values()
}
DESC_CAMPANA = int(float(ingest.leer_ajuste("descuento", "10")))
DESC_CUMPLE = int(float(ingest.leer_ajuste("descuento_cumple", "10")))

# Historial de sueldos/horas con vigencia (se siembra con la plantilla actual).
plantilla_hist = ingest.leer_plantilla_hist(PLANTILLA, "2000-01-01")
plantilla_hist["desde_dt"] = pd.to_datetime(plantilla_hist.desde, errors="coerce")
plantilla_hist = (plantilla_hist.dropna(subset=["desde_dt"])
                  .sort_values(["empleado", "desde_dt"]))


def plantilla_en(emp, fecha_ts):
    """Sueldo y horas de `emp` vigentes en la fecha dada (o el actual si no hay)."""
    h = plantilla_hist[(plantilla_hist.empleado == emp)
                       & (plantilla_hist.desde_dt <= fecha_ts)]
    if h.empty:
        return PLANTILLA.get(emp, {"sueldo": 0.0, "horas_sem": None})
    r = h.iloc[-1]
    return {"sueldo": float(r.sueldo or 0),
            "horas_sem": (None if pd.isna(r.horas_sem) else float(r.horas_sem))}


def horas_periodo_emp(emp, d1, d2):
    """Horas trabajadas por `emp` en [d1, d2] segun sus horas_sem vigentes cada
    dia (horas_sem/7 por dia natural). None si algun tramo no tiene horas."""
    if d1 is None:
        return None
    h = plantilla_hist[plantilla_hist.empleado == emp].sort_values("desde_dt")
    if h.empty:
        hs = PLANTILLA.get(emp, {}).get("horas_sem")
        return hs * (((d2 - d1).days + 1) / 7) if hs else None
    dias = pd.DataFrame({"d": pd.date_range(d1, d2).astype("datetime64[ns]")})
    _h = h[["desde_dt", "horas_sem"]].copy()
    _h["desde_dt"] = _h["desde_dt"].astype("datetime64[ns]")  # misma resolucion
    m = pd.merge_asof(dias, _h,
                      left_on="d", right_on="desde_dt", direction="backward")
    if m.horas_sem.isna().any():
        return None
    return m.horas_sem.sum() / 7


# --------------------------------------------------------------------------- #
# Cabecera + pestañas arriba
# --------------------------------------------------------------------------- #
if CAB_IMG is not None:
    st.image(str(CAB_IMG), use_container_width=True)
else:
    st.markdown(
        f"<div class='brand'>"
        f"<div class='brand-title'>SALÓN BI</div>"
        f"<div class='brand-sub'>DASHBOARD DE PELUQUERÍA</div>"
        f"</div>"
        f"<div class='brand-line'></div>",
        unsafe_allow_html=True,
    )

# --- Selector global Con IVA / Sin IVA ---
ci1, ci2, ci3 = st.columns([2.2, 1, 1])
modo_iva = ci1.radio(
    "Importes", ["Sin IVA (recomendado)", "Con IVA"],
    horizontal=True, index=0,
    help="Sin IVA = base imponible, el dinero real del negocio (el IVA va a "
         "Hacienda). Con IVA = lo que entra en caja.")
AMT = "importe" if modo_iva.startswith("Sin") else "total"
IVA_LBL = "sin IVA" if AMT == "importe" else "con IVA"

# Filtro de fechas GLOBAL: manda en Resumen, Servicios, Productos y las tablas
# de periodo de Empleados. (Clientes usa año; los mapas de calor por mes y los
# desgloses individuales tienen su propio control a proposito.)
if hay_datos:
    ventas_g["importe_kpi"] = ventas_g[AMT]
    ventas_emp["importe_kpi"] = ventas_emp[AMT]
    _fmin = ventas_g.fecha.min().date()
    _fmax = ventas_g.fecha.max().date()
    # El tope llega hasta HOY (o hasta la ultima fecha con datos, lo que sea mas
    # tarde): asi el mes en curso SIEMPRE se puede seleccionar aunque el mes este
    # a medias.
    _tope = max(_fmax, dt.date.today())
    # Por defecto: desde el 1 de enero del año en curso hasta la ultima fecha con datos
    _ini_anio = dt.date(dt.date.today().year, 1, 1)
    _def_desde = min(max(_ini_anio, _fmin), _fmax)
    # La clave del widget incluye la ultima fecha con datos: cuando subes un mes
    # nuevo, esa fecha cambia, la clave cambia y el rango se REINICIA solo para
    # incluir el mes recien subido (si no, Streamlit conservaria el rango antiguo
    # y nunca verias el mes nuevo).
    G_D1 = ci2.date_input("Desde", value=_def_desde, min_value=_fmin, max_value=_tope,
                          format="DD/MM/YYYY", key=f"g_desde_{_fmax}")
    G_D2 = ci3.date_input("Hasta", value=_fmax, min_value=_fmin, max_value=_tope,
                          format="DD/MM/YYYY", key=f"g_hasta_{_fmax}")
    if G_D1 > G_D2:
        G_D1, G_D2 = G_D2, G_D1
else:
    G_D1 = G_D2 = None


def en_rango(df):
    """Filtra un dataframe de ventas al periodo global elegido arriba."""
    if not hay_datos or df.empty or G_D1 is None or "fecha" not in df.columns:
        return df
    return df[(df.fecha.dt.date >= G_D1) & (df.fecha.dt.date <= G_D2)]


# Ingresos de personas exclusivas (entrada manual, SIN IVA). Alteran el total
# de facturacion, pero no salen del TPV ni tienen empleado/servicio asociado.
exclusivos = ingest.leer_exclusivos()
if not exclusivos.empty:
    exclusivos["fecha_dt"] = pd.to_datetime(exclusivos.fecha, errors="coerce")

# Horario de apertura (con vigencia). Cada fila vale DESDE su fecha 'desde'.
horarios_cfg = ingest.leer_horarios("2000-01-01", HORARIO_DEFECTO)
horarios_cfg["desde_dt"] = pd.to_datetime(horarios_cfg.desde, errors="coerce")
horarios_cfg = horarios_cfg.dropna(subset=["desde_dt"]).sort_values("desde_dt")


def excl_en_rango():
    """(suma, filas) de ingresos exclusivos dentro del filtro global de fechas."""
    if exclusivos.empty or G_D1 is None:
        return 0.0, exclusivos
    m = (exclusivos.fecha_dt.notna()
         & (exclusivos.fecha_dt.dt.date >= G_D1)
         & (exclusivos.fecha_dt.dt.date <= G_D2))
    sub = exclusivos[m]
    return float(sub.importe.sum()), sub

(tab_resumen, tab_emple, tab_serv, tab_prod, tab_cli, tab_recup, tab_cumple,
 tab_comp, tab_excl, tab_datos, tab_ajustes) = st.tabs(
    ["Resumen", "Empleados", "Servicios", "Productos", "Clientes",
     "Recuperar clientes", "Cumpleaños", "Comparativas", "Exclusivos",
     "Actualizar datos", "Ajustes"]
)


# =========================================================================== #
# RESUMEN
# =========================================================================== #
with tab_resumen:
    st.subheader("Resumen general")
    if not hay_datos:
        aviso_sin_datos()
    else:
        menu_lateral([
            ("res-kpi", "Indicadores"),
            ("res-desglose", "Desglose de facturación"),
            ("res-factmes", "Facturación por mes"),
            ("res-prevision", "Previsión próximos meses"),
            ("res-factemp", "Facturación por empleado"),
            ("res-dia", "Días de la semana"),
        ])
        vr = en_rango(ventas_g)
        vr_emp = en_rango(ventas_emp)
        excl_sum, excl_sub = excl_en_rango()   # ingresos exclusivos del periodo

        ancla("res-kpi")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"Facturacion ({IVA_LBL})",
                  f"{eur(vr.importe_kpi.sum() + excl_sum)} €",
                  help=(f"Incluye {eur(excl_sum)} € de personas exclusivas "
                        f"(sin IVA)." if excl_sum else None))
        c2.metric("Tickets", eur(vr.numero.nunique()))
        c3.metric("Clientes distintos",
                  eur(vr[vr.cliente != GENERICO].cliente.nunique()))
        c4.metric("Empleados actuales", len(EMPLEADOS_ACTIVOS),
                  help="Plantilla actual de la peluqueria: "
                       + ", ".join(EMPLEADOS_ACTIVOS.values()))
        st.caption(f"Periodo: {G_D1.strftime('%d/%m/%Y')} — {G_D2.strftime('%d/%m/%Y')}"
                   f" (filtro global de arriba). Importes {IVA_LBL}. \"Empleados "
                   f"actuales\" son los que trabajan hoy en la peluqueria.")

        ancla("res-desglose")
        st.markdown("### Desglose de facturacion")
        st.caption("Cuanto viene de los servicios (cortes, color, peinados, packs…) "
                   "y cuanto de la venta de producto de tienda (champus, mascarillas…).")
        vr_n = vr.assign(_n=vr.concepto_limpio.str.strip().str.lower())
        total_f = vr_n.importe_kpi.sum()
        es_prod_r = vr_n._n.isin(set_prod)
        fact_prod = vr_n[es_prod_r].importe_kpi.sum()
        fact_serv = total_f - fact_prod   # servicios = todo lo que no es producto
        pct = (lambda x: 100 * x / total_f if total_f else 0)

        g1, g2, g3 = st.columns(3)
        g1.metric(f"Facturacion TPV ({IVA_LBL})", f"{eur(total_f)} €")
        if excl_sum:
            g1.caption(f"+ {eur(excl_sum)} € de exclusivos = "
                       f"**{eur(total_f + excl_sum)} €** en total")
        g2.metric("Servicios", f"{eur(fact_serv)} €")
        g2.caption(f"{pct(fact_serv):.0f}% del total")
        g3.metric("Productos", f"{eur(fact_prod)} €")
        g3.caption(f"{pct(fact_prod):.0f}% del total")

        # Carro medio (importe medio por ticket)
        n_tk = vr.numero.nunique()
        tk_prod = vr_n[es_prod_r].numero.nunique()
        carro_tot = total_f / n_tk if n_tk else 0
        carro_prod = fact_prod / tk_prod if tk_prod else 0
        h1, h2 = st.columns(2)
        h1.metric("Carro medio por ticket", f"{eur(carro_tot, 2)} €",
                  help="Facturacion total dividida entre el numero de tickets.")
        h2.metric("Carro medio de producto", f"{eur(carro_prod, 2)} €",
                  help="Euros de producto por cada ticket en el que se vende algun "
                       "producto de tienda.")

        don1, don2, don3 = st.columns(3)
        with don1:
            st.caption("Servicios vs. Productos (€)")
            mix = pd.DataFrame({"Tipo": ["Servicios", "Productos"],
                                "euros": [fact_serv, fact_prod]})
            figm = px.pie(mix, names="Tipo", values="euros", hole=.55,
                          color_discrete_sequence=[ACCENT, ACCENT_SOFT])
            figm.update_traces(textinfo="label+percent",
                               hovertemplate="%{label}<br>%{value:,.2f} € "
                                             "(%{percent})<extra></extra>")
            figm.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                               paper_bgcolor="rgba(0,0,0,0)", font_color=TEXT,
                               showlegend=False, separators=",.")
            st.plotly_chart(figm, width="stretch")

        with don2:
            st.caption("Hombres vs. Mujeres (clientes, estimado por el nombre)")
            personas = vr[vr.cliente != GENERICO].cliente.dropna().unique()
            gser = pd.Series([genero.inferir_genero(c) for c in personas])
            n_muj = int((gser == "Mujer").sum())
            n_hom = int((gser == "Hombre").sum())
            n_desc = int((gser == "Desconocido").sum())
            if n_muj + n_hom > 0:
                gmix = pd.DataFrame({"Sexo": ["Mujeres", "Hombres"],
                                     "clientes": [n_muj, n_hom]})
                figg = px.pie(gmix, names="Sexo", values="clientes", hole=.55,
                              color_discrete_sequence=[ACCENT, ACCENT_SOFT])
                figg.update_traces(textinfo="label+percent",
                                   hovertemplate="%{label}<br>%{value} clientes "
                                                 "(%{percent})<extra></extra>")
                figg.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                                   paper_bgcolor="rgba(0,0,0,0)", font_color=TEXT,
                                   showlegend=False, separators=",.")
                st.plotly_chart(figg, width="stretch")
                st.caption(f"{n_desc} clientes sin determinar por el nombre "
                           f"(no cuentan en el %).")
            else:
                st.info("No hay datos suficientes para estimar el sexo.")

        with don3:
            st.caption("Efectivo vs. Tarjeta (tickets)")

            def _cat_pago(fp):
                fp = str(fp).strip().lower()
                if "contado" in fp:
                    return "Efectivo"
                if "tarjeta" in fp:
                    return "Tarjeta"
                return "Otros"

            orden_pago = ["Efectivo", "Tarjeta", "Otros"]
            pago_cat = (vr.groupby("numero").forma_pago.first().map(_cat_pago)
                        .value_counts().reindex(orden_pago).dropna())
            if pago_cat.sum() > 0:
                pmix = pago_cat.reset_index()
                pmix.columns = ["Pago", "tickets"]
                figp = px.pie(pmix, names="Pago", values="tickets", hole=.55,
                              color_discrete_sequence=[ACCENT, ACCENT_SOFT, "#B9A886"])
                figp.update_traces(textinfo="label+percent",
                                   hovertemplate="%{label}<br>%{value} tickets "
                                                 "(%{percent})<extra></extra>")
                figp.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                                   paper_bgcolor="rgba(0,0,0,0)", font_color=TEXT,
                                   showlegend=False, separators=",.")
                st.plotly_chart(figp, width="stretch")
                st.caption("«Otros» = transferencia, mixto, etc.")
            else:
                st.info("Sin datos de forma de pago.")

        ancla("res-factmes")
        st.markdown("### Facturacion por mes")
        porm = (vr.groupby("mes", as_index=False).importe_kpi.sum()
                .sort_values("mes"))
        porm["mes_lbl"] = porm.mes.map(mes_es)
        fig = px.line(porm, x="mes_lbl", y="importe_kpi", markers=True,
                      text="importe_kpi", labels={"mes_lbl": "", "importe_kpi": "€"})
        fig.update_traces(line_color=ACCENT, marker_color=ACCENT,
                          texttemplate="%{text:,.0f} €", textposition="top center",
                          textfont_size=11, cliponaxis=False,
                          hovertemplate="%{x}<br>%{y:,.2f} €<extra></extra>")
        fig.update_yaxes(range=[0, porm.importe_kpi.max() * 1.15])
        eje_meses(fig, porm.mes_lbl)
        st.plotly_chart(estilo_fig(fig, 360), width="stretch")

        # ---- Prevision de los proximos meses ----
        ancla("res-prevision")
        st.markdown("### Previsión de los próximos meses")
        st.caption("Estimación de lo que se facturará (importe TPV) en los próximos "
                   "meses, a partir de lo que sueles facturar ESE mismo mes en años "
                   "anteriores y de la tendencia del último año.")
        _s = (ventas_g.assign(_p=pd.PeriodIndex(ventas_g.mes.astype(str), freq="M"))
              .groupby("_p").importe_kpi.sum().sort_index())
        _cur = pd.Period(dt.date.today(), "M")
        _hist = _s[_s.index < _cur]                      # solo meses completos
        if len(_hist) < 4:
            st.info("Aún no hay suficientes meses cerrados para hacer una previsión "
                    "fiable (hacen falta unos meses de histórico).")
        else:
            def _pred(target):
                _same = _hist[[p.month == target.month for p in _hist.index]]
                _base = (_same.tail(3).mean() if len(_same)
                         else _hist.tail(3).mean())
                _u = _hist.tail(12).mean()
                _p12 = _hist.iloc[-24:-12].mean() if len(_hist) >= 24 else _u
                _tend = (_u / _p12) if _p12 else 1
                return float(_base * _tend)

            _targets = [_cur, _cur + 1]
            _rows = [(mes_es(str(p)), float(v), "Real")
                     for p, v in _hist.tail(12).items()]
            for _t in _targets:
                _rows.append((mes_es(str(_t)), _pred(_t), "Previsión"))
            dfp = pd.DataFrame(_rows, columns=["mes", "eur", "tipo"])
            figpr = px.bar(dfp, x="mes", y="eur", color="tipo",
                           text="eur", labels={"mes": "", "eur": "€"},
                           color_discrete_map={"Real": ACCENT,
                                               "Previsión": ACCENT_SOFT})
            figpr.update_traces(texttemplate="%{text:,.0f} €",
                                textposition="outside", cliponaxis=False,
                                textfont_size=11,
                                hovertemplate="%{x}<br>%{y:,.2f} €<extra></extra>")
            # A la prevision se le pone trama de rayas para que se distinga a simple
            # vista de los meses reales (rellenos).
            for _tr in figpr.data:
                if _tr.name == "Previsión":
                    _tr.marker.pattern.shape = "/"
                    _tr.marker.pattern.fgcolor = ACCENT
                    _tr.marker.pattern.bgcolor = "#FFFFFF"
                    _tr.marker.pattern.size = 9
                    _tr.marker.line.color = ACCENT
                    _tr.marker.line.width = 1.2
            figpr.update_yaxes(range=[0, dfp.eur.max() * 1.18])
            figpr.update_layout(legend_title_text="")
            eje_meses(figpr, dfp.mes)
            st.plotly_chart(estilo_fig(figpr, 360), width="stretch")
            _prev_txt = " · ".join(
                f"**{mes_es(str(_t))}**: ≈ {eur(_pred(_t))} €" for _t in _targets)
            st.caption("Previsión (importe TPV): " + _prev_txt + ". Es una "
                       "estimación orientativa, no una cifra exacta.")

        ancla("res-factemp")
        st.markdown("### Facturacion por empleado")
        pore = (vr_emp.groupby("emp", as_index=False).importe_kpi.sum()
                .sort_values("importe_kpi", ascending=False))
        fig2 = px.bar(pore, x="importe_kpi", y="emp", orientation="h",
                      labels={"importe_kpi": "€", "emp": ""},
                      text="importe_kpi")
        fig2.update_traces(marker_color=ACCENT, texttemplate="%{text:,.0f} €",
                           textposition="outside", cliponaxis=False,
                           hovertemplate="%{y}<br>%{x:,.2f} €<extra></extra>")
        fig2.update_layout(yaxis={"categoryorder": "total ascending"})
        fig2.update_xaxes(range=[0, pore.importe_kpi.max() * 1.18])
        st.plotly_chart(estilo_fig(fig2, 380), width="stretch")

        # ---- Dias de la semana mas fuertes ----
        ancla("res-dia")
        st.markdown("### Días de la semana más fuertes")
        st.caption("Facturación media por día y, teniendo en cuenta el horario, por "
                   "HORA abierta (comparación justa). Las horas de cada día salen del "
                   "horario que tengas en Ajustes, respetando la fecha de vigencia "
                   "(si cambió el horario, cada periodo usa el suyo).")
        DIAS_SEM = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes",
                    "Sábado", "Domingo"]
        _vd = vr.assign(_f=vr.fecha.dt.normalize(), _dow=vr.fecha.dt.dayofweek)
        _pdia = (_vd.groupby(["_dow", "_f"])
                 .agg(fac=("importe_kpi", "sum"))
                 .reset_index().sort_values("_f"))
        # Horas abiertas de cada fecha segun el horario vigente ese dia (merge_asof).
        _pdia["_f"] = _pdia["_f"].astype("datetime64[ns]")   # misma resolucion
        _hor_m = horarios_cfg[["desde_dt"] + DIAS_KEYS].copy()
        _hor_m["desde_dt"] = _hor_m["desde_dt"].astype("datetime64[ns]")
        _pdia = pd.merge_asof(
            _pdia, _hor_m,
            left_on="_f", right_on="desde_dt", direction="backward")
        _pdia["horas"] = _pdia.apply(
            lambda r: r[DIAS_KEYS[int(r._dow)]] if pd.notna(r[DIAS_KEYS[int(r._dow)]])
            else 0, axis=1)
        _gd = (_pdia.groupby("_dow").agg(
                   fac_media=("fac", "mean"), fac_total=("fac", "sum"),
                   horas_total=("horas", "sum"))
               .reindex(range(7)).reset_index())
        _gd["dia"] = [DIAS_SEM[i] for i in _gd._dow]
        _gd["fac_hora"] = _gd.fac_total / _gd.horas_total.replace(0, pd.NA)
        _gd = _gd.dropna(subset=["fac_media"])
        if _gd.empty:
            st.info("No hay ventas en el periodo elegido arriba.")
        else:
            cda, cdb = st.columns(2)
            cda.markdown("<div class='mini-tit'>Facturación media de cada día "
                         "<span>(día entero)</span></div>", unsafe_allow_html=True)
            f_fac = px.bar(_gd, x="dia", y="fac_media",
                           text=_gd.fac_media.map(lambda v: f"{eur(v)} €"),
                           labels={"dia": "", "fac_media": "€ de media"})
            f_fac.update_traces(marker_color=ACCENT, textposition="outside",
                                cliponaxis=False)
            f_fac.update_xaxes(categoryorder="array", categoryarray=DIAS_SEM)
            f_fac.update_yaxes(range=[0, _gd.fac_media.max() * 1.18])
            cda.plotly_chart(estilo_fig(f_fac, 320), width="stretch")
            cdb.markdown("<div class='mini-tit'>Facturación media por hora abierta "
                         "<span>(descuenta el medio día de lunes y sábado)</span></div>",
                         unsafe_allow_html=True)
            f_h = px.bar(_gd, x="dia", y="fac_hora",
                         text=_gd.fac_hora.map(lambda v: f"{eur(v)} €"),
                         labels={"dia": "", "fac_hora": "€ por hora abierta"})
            f_h.update_traces(marker_color=ACCENT_SOFT, textposition="outside",
                              cliponaxis=False)
            f_h.update_xaxes(categoryorder="array", categoryarray=DIAS_SEM)
            f_h.update_yaxes(range=[0, _gd.fac_hora.max() * 1.2])
            cdb.plotly_chart(estilo_fig(f_h, 320), width="stretch")
            _best = _gd.loc[_gd.fac_media.idxmax()]
            _besth = _gd.loc[_gd.fac_hora.idxmax()]
            st.caption(f"En total, el día más fuerte es **{_best.dia}** "
                       f"({eur(_best.fac_media)} € de media). Pero **por hora abierta** "
                       f"el más rentable es **{_besth.dia}** "
                       f"({eur(_besth.fac_hora)} €/h), que es la comparación justa "
                       "teniendo en cuenta el horario.")


# =========================================================================== #
# ASISTENTE FLOTANTE (icono de IA abajo a la derecha) — respuestas EXACTAS
# Es un boton redondo fijo en la esquina; al pulsarlo se despliega el chat.
# Cada respuesta sigue siendo un calculo exacto de pandas (sin IA generativa).
# =========================================================================== #
if hay_datos:
    if "chat_open" not in st.session_state:
        st.session_state.chat_open = False
    with st.container(key="ai_launcher"):
        if st.button(" ", key="ai_toggle", help="Asistente de Salón BI"):
            st.session_state.chat_open = not st.session_state.chat_open
            st.rerun()

if hay_datos and st.session_state.get("chat_open"):
    with st.container(key="ai_panel"):
        _ph1, _ph2 = st.columns([4, 1])
        _ph1.markdown("**Asistente de Salón BI** · pregúntame sobre un mes")
        if _ph2.button("✕", key="ai_close", help="Cerrar"):
            st.session_state.chat_open = False
            st.rerun()
        meses_p = sorted(ventas_g.mes.unique())
        m = st.selectbox("Mes sobre el que preguntar", list(reversed(meses_p)),
                         format_func=mes_es, key="mes_preg")
        vm = ventas_g[ventas_g.mes == m]
        vme = ventas_emp[ventas_emp.mes == m]
        vm_serv = vm[~vm.concepto_limpio.str.strip().str.lower().isin(set_prod)]

        def _fact_mas():
            g = vme.groupby("emp").importe_kpi.sum().sort_values(ascending=False)
            if g.empty:
                return "No hay ventas de la plantilla ese mes."
            return (f"Quien más facturó en **{mes_es(m)}** fue **{g.index[0]}**, "
                    f"con **{eur(g.iloc[0])} €** ({IVA_LBL}).")

        def _fact_menos():
            g = vme.groupby("emp").importe_kpi.sum().sort_values()
            if g.empty:
                return "No hay ventas de la plantilla ese mes."
            return (f"Quien menos facturó en **{mes_es(m)}** fue **{g.index[0]}**, "
                    f"con **{eur(g.iloc[0])} €** ({IVA_LBL}). "
                    f"(Solo cuenta a quien tuvo alguna venta.)")

        def _total():
            return (f"En **{mes_es(m)}** se facturó **{eur(vm.total.sum())} € con IVA** "
                    f"y **{eur(vm.importe.sum())} € sin IVA** "
                    f"(el IVA fueron {eur(vm.impuesto.sum())} €).")

        def _clientes():
            n = vm[vm.cliente != GENERICO].cliente.nunique()
            return f"En **{mes_es(m)}** vinieron **{eur(n)} clientes** distintos."

        def _nuevos():
            if clientes.empty or "fecha_alta" not in clientes.columns:
                return "No tengo el fichero de Clientes con la fecha de alta."
            cl = clientes[clientes.nombre.astype(str).str.strip().str.lower() != "generico"]
            nuevos = cl[cl.fecha_alta.dt.strftime("%Y-%m") == m]
            return (f"En **{mes_es(m)}** se dieron de alta **{eur(len(nuevos))} clientes "
                    f"nuevos**.")

        def _serv_top():
            if vm_serv.empty:
                return "No hay servicios ese mes."
            g = vm_serv.groupby("concepto_limpio").cantidad.sum().sort_values(ascending=False)
            return (f"El servicio más hecho en **{mes_es(m)}** fue **{g.index[0]}** "
                    f"({eur(g.iloc[0])} veces).")

        def _serv_emp():
            if vme.empty:
                return "No hay ventas de la plantilla ese mes."
            se = vme[~vme.concepto_limpio.str.strip().str.lower().isin(set_prod)]
            lineas = []
            for e in EMPLEADOS_ACTIVOS.values():
                sub = se[se.emp == e]
                if sub.empty:
                    continue
                top = sub.groupby("concepto_limpio").cantidad.sum().sort_values(
                    ascending=False)
                lineas.append(f"- **{e}**: {top.index[0]} ({eur(top.iloc[0])} veces)")
            if not lineas:
                return "No hay servicios de la plantilla ese mes."
            return (f"Servicio más hecho por cada empleado en **{mes_es(m)}**:\n\n"
                    + "\n".join(lineas))

        def _carro():
            g = vme.groupby("emp").agg(f=("importe_kpi", "sum"),
                                       t=("numero", "nunique"))
            g = g[g.t > 0]
            if g.empty:
                return "No hay ventas de la plantilla ese mes."
            g["carro"] = g.f / g.t
            g = g.sort_values("carro", ascending=False)
            return (f"El carro medio más alto en **{mes_es(m)}** es de "
                    f"**{g.index[0]}**: **{eur(g.carro.iloc[0], 2)} €** por ticket "
                    f"({IVA_LBL}).")

        PREGUNTAS = {
            "¿Quién facturó más este mes?": _fact_mas,
            "¿Quién facturó menos este mes?": _fact_menos,
            "¿Cuánto se facturó este mes (con y sin IVA)?": _total,
            "¿Cuántos clientes vinieron este mes?": _clientes,
            "¿Cuántos clientes nuevos este mes?": _nuevos,
            "¿Cuál es el servicio más hecho?": _serv_top,
            "¿Servicio más hecho por cada empleado?": _serv_emp,
            "¿Quién tiene el carro medio más alto?": _carro,
        }

        def _match(texto):
            n = genero._norm(texto)
            if "carro" in n:
                return "¿Quién tiene el carro medio más alto?"
            if "nuevo" in n:
                return "¿Cuántos clientes nuevos este mes?"
            if "servici" in n and "emplead" in n:
                return "¿Servicio más hecho por cada empleado?"
            if "servici" in n:
                return "¿Cuál es el servicio más hecho?"
            if "factur" in n and "menos" in n:
                return "¿Quién facturó menos este mes?"
            if "factur" in n and any(k in n for k in
                                     ["mas", "mayor", "top", "mejor", "quien"]):
                return "¿Quién facturó más este mes?"
            if ("cuanto" in n and "factur" in n) or "facturacion total" in n:
                return "¿Cuánto se facturó este mes (con y sin IVA)?"
            if "client" in n:
                return "¿Cuántos clientes vinieron este mes?"
            return None

        def _responder(texto):
            it = _match(texto)
            if it:
                return PREGUNTAS[it]()
            return ("No estoy seguro de haber entendido 🤔. Puedo responder sobre el mes "
                    "elegido arriba: **quién facturó más o menos**, **cuánto se facturó**, "
                    "**cuántos clientes** (o **nuevos**) vinieron, el **servicio más "
                    "hecho** (en total o por empleada) y **quién tiene el carro medio "
                    "más alto**. Prueba a reformular o pulsa una de las preguntas "
                    "rápidas de abajo.")

        if "chat_preg" not in st.session_state:
            st.session_state.chat_preg = [
                ("assistant", "¡Hola! Soy el asistente de Salón BI. Pregúntame sobre el "
                 "mes que elijas arriba, o pulsa una de las preguntas rápidas de "
                 "abajo.")]

        # Historial del chat
        for _rol, _txt in st.session_state.chat_preg:
            with st.chat_message(_rol, avatar="💇" if _rol == "assistant" else "🧑"):
                st.markdown(_txt)

        # Entrada (formulario: funciona dentro de la pestaña y se limpia solo)
        with st.form("form_preg", clear_on_submit=True):
            _q = st.text_input(
                "Escribe tu pregunta",
                placeholder=f"p. ej. ¿quién facturó más en {mes_es(m)}?")
            _send = st.form_submit_button("Preguntar", type="primary")
        if _send and _q:
            st.session_state.chat_preg.append(("user", _q))
            st.session_state.chat_preg.append(("assistant", _responder(_q)))
            st.rerun()

        ancla("preg-botones")
        st.markdown("#### Preguntas rápidas")
        cols = st.columns(2)
        for i, preg in enumerate(PREGUNTAS):
            if cols[i % 2].button(preg, key=f"pq_{i}", width="stretch"):
                st.session_state.chat_preg.append(("user", preg))
                st.session_state.chat_preg.append(("assistant", PREGUNTAS[preg]()))
                st.rerun()
        if len(st.session_state.chat_preg) > 1 and st.button(
                "Borrar conversación", key="clear_chat"):
            del st.session_state.chat_preg
            st.rerun()


# =========================================================================== #
# COMPARATIVAS ENTRE AÑOS  (usa TODO el historico, ignora el filtro global)
# =========================================================================== #
with tab_comp:
    st.subheader("Comparativas entre años")
    if not hay_datos:
        aviso_sin_datos()
    else:
        menu_lateral([
            ("comp-mes", "Facturación por mes"),
            ("comp-anio", "Total por año"),
            ("comp-detalle", "Comparar dos años"),
        ])
        st.caption(f"Comparativas sobre TODO el histórico (no dependen del filtro de "
                   f"fechas de arriba). Importes {IVA_LBL}.")
        MESES_ABBR = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago",
                      "Sep", "Oct", "Nov", "Dic"]
        anios = sorted(ventas_g.anio.unique())
        anios_str = [str(a) for a in anios]

        # ---- A. Facturación mensual, una línea por año ----
        ancla("comp-mes")
        st.markdown("### Facturación por mes, comparando años")
        st.caption("Cada línea es un año. Ideal para ver crecimiento y estacionalidad "
                   "(qué meses son más fuertes).")
        sel = st.multiselect("Años a mostrar", anios_str, default=anios_str)
        base = ventas_g[ventas_g.anio.astype(str).isin(sel)].copy()
        if base.empty:
            st.info("Elige al menos un año.")
        else:
            base["m_num"] = base.fecha.dt.month
            base["Año"] = base.anio.astype(str)
            gA = base.groupby(["Año", "m_num"], as_index=False).importe_kpi.sum()
            gA["mes_lbl"] = gA.m_num.map(lambda x: MESES_ABBR[x - 1])
            figA = px.line(gA, x="mes_lbl", y="importe_kpi", color="Año", markers=True,
                           color_discrete_sequence=PALETA_ANIOS,
                           labels={"mes_lbl": "", "importe_kpi": "€"})
            figA.update_xaxes(categoryorder="array", categoryarray=MESES_ABBR)
            figA.update_traces(
                hovertemplate="%{x}: %{y:,.2f} €<extra>%{fullData.name}</extra>")
            st.plotly_chart(estilo_fig(figA, 400), width="stretch")

        # ---- B. Facturación total por año ----
        st.markdown("---")
        ancla("comp-anio")
        st.markdown("### Facturación total por año")
        tot = ventas_g.groupby("anio", as_index=False).importe_kpi.sum().sort_values("anio")
        tot["Año"] = tot.anio.astype(str)
        figB = px.bar(tot, x="Año", y="importe_kpi", text="importe_kpi",
                      labels={"importe_kpi": "€"})
        figB.update_traces(marker_color=ACCENT, texttemplate="%{text:,.0f} €",
                           textposition="outside", cliponaxis=False,
                           hovertemplate="%{x}: %{y:,.2f} €<extra></extra>")
        st.plotly_chart(estilo_fig(figB, 340), width="stretch")
        tot["Variación"] = tot.importe_kpi.pct_change() * 100
        st.dataframe(
            tot.rename(columns={"importe_kpi": "Facturación €"})[
                ["Año", "Facturación €", "Variación"]]
            .style.format({"Facturación €": lambda v: f"{eur(v)} €",
                           "Variación": lambda v: ("—" if pd.isna(v)
                                                   else f"{'+' if v >= 0 else ''}"
                                                        f"{eur(v, 1)}% vs año anterior")}),
            width="stretch", hide_index=True)

        # ---- C+D. Comparar dos años en detalle (empleados y servicios) ----
        st.markdown("---")
        ancla("comp-detalle")
        st.markdown("### Comparar dos años en detalle")
        cc1, cc2 = st.columns(2)
        aA = cc1.selectbox("Año A", anios, index=max(0, len(anios) - 2), key="comp_aA")
        aB = cc2.selectbox("Año B", anios, index=len(anios) - 1, key="comp_aB")

        def _color_pos(v):
            if isinstance(v, (int, float)) and not pd.isna(v):
                return ("color:#2e7d32;font-weight:700" if v > 0
                        else ("color:#b23a2a;font-weight:700" if v < 0 else ""))
            return ""

        # -- Por empleado --
        st.markdown("#### Por empleado")
        if ventas_emp.empty:
            st.info("No hay datos de la plantilla.")
        else:
            d = ventas_emp[ventas_emp.anio.isin([aA, aB])].copy()
            d["Año"] = d.anio.astype(str)
            gC = d.groupby(["emp", "Año"], as_index=False).importe_kpi.sum()
            figC = px.bar(gC, x="emp", y="importe_kpi", color="Año", barmode="group",
                          color_discrete_sequence=PALETA_ANIOS,
                          labels={"emp": "", "importe_kpi": "€"})
            figC.update_traces(
                hovertemplate="%{x}: %{y:,.2f} €<extra>%{fullData.name}</extra>")
            st.plotly_chart(estilo_fig(figC, 360), width="stretch")

            pv = d.groupby(["emp", "anio"]).importe_kpi.sum().unstack(fill_value=0)
            for a in (aA, aB):
                if a not in pv.columns:
                    pv[a] = 0.0
            pv["Diferencia"] = pv[aB] - pv[aA]
            pv["Variación"] = pv.apply(
                lambda r: (r["Diferencia"] / r[aA] * 100) if r[aA] else float("nan"),
                axis=1)
            pv = pv.reset_index().rename(columns={"emp": "Empleado",
                                                  aA: f"{aA} €", aB: f"{aB} €"})
            st.dataframe(
                pv[["Empleado", f"{aA} €", f"{aB} €", "Diferencia", "Variación"]]
                .style.format({f"{aA} €": lambda v: f"{eur(v)} €",
                               f"{aB} €": lambda v: f"{eur(v)} €",
                               "Diferencia": lambda v: f"{'+' if v >= 0 else ''}{eur(v)} €",
                               "Variación": lambda v: ("—" if pd.isna(v)
                                                       else f"{'+' if v >= 0 else ''}"
                                                            f"{eur(v, 1)}%")})
                .map(_color_pos, subset=["Diferencia"]),
                width="stretch", hide_index=True)

        # -- Servicios --
        st.markdown("#### Servicios más hechos")
        st.caption("Cuántas veces se hizo cada servicio en un año y en otro.")
        sd = ventas_g[ventas_g.anio.isin([aA, aB])].copy()
        sd = sd[~sd.concepto_limpio.str.strip().str.lower().isin(set_prod)]
        if sd.empty:
            st.info("No hay servicios en esos años.")
        else:
            topn = st.slider("Cuántos servicios mostrar", 5, 25, 12, key="comp_serv_n")
            top_serv = (sd.groupby("concepto_limpio").cantidad.sum()
                        .sort_values(ascending=False).head(topn).index)
            sds = sd[sd.concepto_limpio.isin(top_serv)].copy()
            sds["Año"] = sds.anio.astype(str)
            gS = sds.groupby(["concepto_limpio", "Año"], as_index=False).cantidad.sum()
            figS = px.bar(gS, x="cantidad", y="concepto_limpio", color="Año",
                          barmode="group", orientation="h",
                          color_discrete_sequence=PALETA_ANIOS,
                          labels={"cantidad": "Veces", "concepto_limpio": ""})
            figS.update_traces(
                hovertemplate="%{y}: %{x:,.0f} veces<extra>%{fullData.name}</extra>")
            figS.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(estilo_fig(figS, 30 * topn + 80), width="stretch")

            pvs = sd.groupby(["concepto_limpio", "anio"]).cantidad.sum().unstack(fill_value=0)
            for a in (aA, aB):
                if a not in pvs.columns:
                    pvs[a] = 0
            pvs["Diferencia"] = pvs[aB] - pvs[aA]
            pvs = (pvs.sort_values(aB, ascending=False).head(topn).reset_index()
                   .rename(columns={"concepto_limpio": "Servicio",
                                    aA: f"{aA}", aB: f"{aB}"}))
            st.dataframe(
                pvs[["Servicio", f"{aA}", f"{aB}", "Diferencia"]]
                .style.format({f"{aA}": lambda v: eur(v), f"{aB}": lambda v: eur(v),
                               "Diferencia": lambda v: f"{'+' if v >= 0 else ''}{eur(v)}"})
                .map(_color_pos, subset=["Diferencia"]),
                width="stretch", hide_index=True)


# =========================================================================== #
# EMPLEADOS
# =========================================================================== #
with tab_emple:
    st.subheader("Empleados")
    if not hay_datos or ventas_emp.empty:
        aviso_sin_datos()
    else:
        menu_lateral([
            ("emp-items", "Ítems por mes"),
            ("emp-items-pond", "Ítems ponderados"),
            ("emp-resumen", "Resumen del periodo"),
            ("emp-rentab", "Rentabilidad / hora"),
            ("emp-servprod", "Servicio vs. producto"),
            ("emp-factmes", "Facturación por mes"),
            ("emp-carromes", "Carro medio por mes"),
            ("emp-carroind", "Carro medio individual"),
            ("emp-informe", "Informe descargable"),
        ])
        col_body = st.container()
        with col_body:
            meses = sorted(ventas_emp.mes.unique())
            sel = en_rango(ventas_emp)   # tablas de periodo -> filtro global de arriba
            st.caption(f"Casi todo en esta pestaña usa el rango global de arriba "
                       f"({G_D1.strftime('%d/%m/%Y')} — {G_D2.strftime('%d/%m/%Y')}). "
                       f"Solo la Rentabilidad tiene su propio selector de mes.")
            vista_mapas = st.radio(
                "Ver los cuadros por mes como", ["Mapa de calor", "Gráfico de líneas"],
                horizontal=True, index=0, key="vista_mapas")

            ancla("emp-items")
            st.markdown("### Items por empleado y mes")
            st.caption("Nº de articulos (servicios/productos) que hace cada empleado cada mes.")
            _dl = (sel.groupby(["emp", "mes"], as_index=False).cantidad.sum()
                   .rename(columns={"cantidad": "valor"}))
            por_mes_vista(_dl, vista_mapas, lambda v: eur(v), "Ítems",
                          "%{x}: %{y:,.0f} items")

            ancla("emp-items-pond")
            st.markdown("### Items ponderados por empleado y mes")
            st.caption("Igual que arriba pero dando a cada servicio su peso (un corte de "
                       "30 min = 1; un balayage brosse = 2; un lavado = 0,2…). Asi se ve "
                       "mejor la carga real de trabajo, no todos los items cuentan igual. "
                       "Está **normalizado de 0 a 1**: 1 = el empleado-mes con más carga, "
                       "y el resto en proporción a ese máximo.")
            _dl = (sel.groupby(["emp", "mes"], as_index=False).items_pond.sum()
                   .rename(columns={"items_pond": "valor"}))
            por_mes_vista(_dl, vista_mapas, lambda v: f"{v:.2f}".replace(".", ","),
                          "Ítems ponderados (0–1)", "%{x}: %{y:.2f}", normalizar=True)

            ancla("emp-resumen")
            st.markdown(f"### Resumen del periodo {G_D1.strftime('%d/%m/%Y')} — "
                        f"{G_D2.strftime('%d/%m/%Y')} por empleado")
            st.caption("Carro medio = facturacion / nº de tickets. Facturacion/dia = "
                       "facturacion / dias distintos trabajados (no tiene en cuenta las "
                       "horas). Facturacion/hora = facturacion / horas de contrato del "
                       "periodo: es la comparacion JUSTA entre empleadas con jornadas "
                       "distintas. Usa las horas actuales (orientativo en meses pasados); "
                       "quien no tiene horas fijadas sale «n/d».")
            resumen = sel.groupby("emp").agg(
                facturacion=("importe_kpi", "sum"), tickets=("numero", "nunique"),
                clientes=("cliente", "nunique"), items=("cantidad", "sum"),
                items_pond=("items_pond", "sum"),
                dias=("fecha", lambda s: s.dt.date.nunique()),
            ).reset_index()
            resumen["carro_medio"] = resumen.facturacion / resumen.tickets
            resumen["fact_dia"] = resumen.facturacion / resumen.dias
            # Facturacion/hora: horas del periodo segun las horas vigentes cada dia
            # (respeta los cambios de contrato con fecha).
            resumen["horas_periodo"] = resumen.emp.map(
                lambda e: horas_periodo_emp(e, G_D1, G_D2))
            resumen["fact_hora"] = (resumen.facturacion
                                    / pd.to_numeric(resumen.horas_periodo,
                                                    errors="coerce"))
            resumen = resumen.sort_values("facturacion", ascending=False)
            st.dataframe(
                resumen.rename(columns={
                    "emp": "Empleado", "facturacion": "Facturacion €",
                    "tickets": "Tickets", "clientes": "Clientes", "items": "Items",
                    "items_pond": "Items pond.", "dias": "Dias trabajados",
                    "carro_medio": "Carro medio €", "fact_dia": "Facturacion/dia €",
                    "fact_hora": "Facturacion/hora €"})
                [["Empleado", "Facturacion €", "Facturacion/dia €",
                  "Facturacion/hora €", "Dias trabajados",
                  "Tickets", "Clientes", "Items", "Items pond.", "Carro medio €"]]
                .style.format({"Facturacion €": lambda v: f"{eur(v)} €",
                         "Facturacion/dia €": lambda v: f"{eur(v, 2)} €",
                         "Facturacion/hora €": lambda v: f"{eur(v, 2)} €",
                         "Carro medio €": lambda v: f"{eur(v, 2)} €",
                         "Tickets": lambda v: eur(v), "Clientes": lambda v: eur(v),
                         "Items": lambda v: eur(v),
                         "Items pond.": lambda v: eur(v, 1),
                         "Dias trabajados": lambda v: eur(v)}, na_rep="n/d"),
                width="stretch", hide_index=True)

            st.markdown("---")
            ancla("emp-rentab")
            st.markdown("### Rentabilidad y facturacion por hora")
            st.caption("Compara lo que factura cada empleado con lo que cuesta (sueldo en "
                       "sucio + comisiones). Horas/mes = horas por semana × 52/12. "
                       "Sueldos, horas y comisiones se editan en la pestaña Ajustes.")
            meses_desc = list(reversed(meses))
            mes_r = st.selectbox("Mes a analizar", meses_desc, format_func=mes_es,
                                 key="mes_rentab")
            st.caption("El sueldo y las horas se toman según su fecha de vigencia "
                       "(Ajustes → Sueldos y horas): cada mes usa lo que valía "
                       "entonces. Para que un mes pasado sea exacto, añade en Ajustes "
                       "la fila con la fecha en que cambió el contrato; si no, se usa "
                       "el valor más antiguo que tengas puesto.")
            com_mes = ingest.leer_comisiones(mes_r)   # {empleado: importe}
            _fecha_mes = pd.Timestamp(mes_r + "-01")   # 1er dia del mes analizado
            filas = []
            for nombre in PLANTILLA:
                info = plantilla_en(nombre, _fecha_mes)   # sueldo/horas vigentes ese mes
                sub_m = ventas_emp[(ventas_emp.emp == nombre) & (ventas_emp.mes == mes_r)]
                fact = sub_m.importe_kpi.sum()
                sueldo = info["sueldo"]
                comision = float(com_mes.get(nombre, 0) or 0)
                coste = sueldo + comision
                hs = info["horas_sem"]
                horas_mes = hs * SEMANAS_MES if hs else None
                filas.append({
                    "Empleado": nombre,
                    "Facturacion": fact,
                    "Sueldo (sucio)": sueldo,
                    "Comision": comision,
                    "Margen": fact - coste,
                    "Fact./hora": (fact / horas_mes) if horas_mes else float("nan"),
                    "Coste/hora": (coste / horas_mes) if horas_mes else float("nan"),
                    "Horas/mes": horas_mes if horas_mes else float("nan"),
                    "Rentable": "Si" if fact - coste > 0 else "No",
                })
            rent = pd.DataFrame(filas)

            def _color_margen(v):
                if isinstance(v, (int, float)) and not pd.isna(v):
                    c = "#2e7d32" if v > 0 else "#b23a2a"
                    return f"color:{c};font-weight:700"
                return ""

            st.dataframe(
                rent.style.format({
                    "Facturacion": lambda v: f"{eur(v)} €",
                    "Sueldo (sucio)": lambda v: f"{eur(v)} €",
                    "Comision": lambda v: f"{eur(v)} €",
                    "Margen": lambda v: f"{eur(v)} €",
                    "Fact./hora": lambda v: f"{eur(v, 2)} €",
                    "Coste/hora": lambda v: f"{eur(v, 2)} €",
                    "Horas/mes": lambda v: eur(v, 0),
                }, na_rep="n/d").map(_color_margen, subset=["Margen"]),
                width="stretch", hide_index=True)
            st.info("Para que estos márgenes sean REALES hay que tener metidas las "
                    "comisiones de cada mes en **Ajustes → Comisiones por mes**. "
                    "Mientras falten, el margen sale más alto de lo que de verdad es.")
            if not any(com_mes.values()):
                st.markdown(
                    ":red[**Aun no has metido comisiones para este mes (van a 0). "
                    "Cuando las tengas, ponlas en Ajustes → Comisiones por mes.**]")
            st.caption("Margen = Facturacion − Sueldo − Comision (verde = rentable). "
                       "Carlos sale «n/d» por hora porque no se saben sus horas; su margen "
                       "si se calcula.")

            # Desglose de la comision (individual / general / productos / extra)
            com_det_r = ingest.leer_comisiones_detalle(mes_r)
            det_com = pd.DataFrame([
                {"Empleado": n,
                 "Individual (€)": float(com_det_r.get(n, {}).get("individual", 0) or 0),
                 "General (€)": float(com_det_r.get(n, {}).get("general", 0) or 0),
                 "Productos (€)": float(com_det_r.get(n, {}).get("productos", 0) or 0),
                 "Horas extra (€)": float(com_det_r.get(n, {}).get("extra", 0) or 0),
                 "Total (€)": float(com_mes.get(n, 0) or 0),
                 "Notas": str(com_det_r.get(n, {}).get("notas", "") or "")}
                for n in rent["Empleado"]])
            with st.expander("Ver desglose de las comisiones de este mes"):
                _fe = {c: (lambda v: f"{eur(v, 2)} €") for c in
                       ["Individual (€)", "General (€)", "Productos (€)",
                        "Horas extra (€)", "Total (€)"]}
                st.dataframe(det_com.style.format(_fe),
                             width="stretch", hide_index=True)
                st.caption("Importes sin IVA, tal como se meten en Ajustes → "
                           "Comisiones por mes. El Total es lo que se resta en la "
                           "rentabilidad de arriba.")

            st.markdown("---")
            ancla("emp-servprod")
            st.markdown("### Venta de servicio vs. producto por empleado")
            st.caption("Cuanto factura cada empleado en servicios y cuanto en venta de "
                       "producto de tienda, en el periodo elegido. Util para ver quien "
                       "empuja mas la venta de producto.")
            se = sel.assign(_n=sel.concepto_limpio.str.strip().str.lower())
            se["tipo"] = se._n.isin(set_prod).map({True: "Producto", False: "Servicio"})
            sp_emp = se.groupby(["emp", "tipo"]).importe_kpi.sum().unstack(fill_value=0)
            for col in ["Servicio", "Producto"]:
                if col not in sp_emp.columns:
                    sp_emp[col] = 0.0
            denom = (sp_emp["Servicio"] + sp_emp["Producto"]).replace(0, pd.NA)
            sp_emp["pct_prod"] = 100 * sp_emp["Producto"] / denom
            sp_emp = sp_emp.sort_values("Producto", ascending=False)
            st.dataframe(
                sp_emp.reset_index().rename(columns={
                    "emp": "Empleado", "Servicio": "Servicios €",
                    "Producto": "Productos €", "pct_prod": "% en producto"})
                [["Empleado", "Servicios €", "Productos €", "% en producto"]]
                .style.format({"Servicios €": lambda v: f"{eur(v)} €",
                               "Productos €": lambda v: f"{eur(v)} €",
                               "% en producto": lambda v: f"{eur(v, 1)}%"}),
                width="stretch", hide_index=True)
            long = sp_emp.reset_index().melt(
                id_vars="emp", value_vars=["Servicio", "Producto"],
                var_name="Tipo", value_name="euros")
            figsp = px.bar(long, x="emp", y="euros", color="Tipo", barmode="stack",
                           text="euros",
                           labels={"emp": "", "euros": "€", "Tipo": ""},
                           color_discrete_map={"Servicio": ACCENT, "Producto": ACCENT_SOFT})
            figsp.update_traces(
                texttemplate="%{y:,.0f} €", textposition="inside",
                insidetextanchor="middle", textfont_size=13, cliponaxis=False,
                hovertemplate="%{x} · %{fullData.name}<br>%{y:,.2f} €<extra></extra>")
            figsp.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0),
                                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                font=dict(color=TEXT, size=14), legend=dict(orientation="h"),
                                uniformtext=dict(minsize=9, mode="hide"), separators=",.")
            st.caption("Cada barra lleva escrito lo de **servicios** (terracota) y lo de "
                       "**producto** (claro). El % en producto está en la tabla de arriba.")
            st.plotly_chart(figsp, width="stretch")

            st.markdown("---")
            ancla("emp-factmes")
            st.markdown("### Facturacion por empleado y mes")
            st.caption("Lo que factura cada empleado cada mes (en €), en el rango global.")
            _dl = (sel.groupby(["emp", "mes"], as_index=False).importe_kpi.sum()
                   .rename(columns={"importe_kpi": "valor"}))
            por_mes_vista(_dl, vista_mapas, lambda v: f"{eur(v)} €", "€",
                          "%{x}: %{y:,.2f} €")

            st.markdown("---")
            ancla("emp-carromes")
            st.markdown("### Carro medio por empleado y mes")
            sexo_cm = st.selectbox("Clientela", ["Todos", "Solo mujeres", "Solo hombres"],
                                   key="sexo_carromes")
            sel_cm = sel
            if sexo_cm == "Solo mujeres":
                sel_cm = sel[sel.genero == "Mujer"]
            elif sexo_cm == "Solo hombres":
                sel_cm = sel[sel.genero == "Hombre"]
            st.caption(f"Importe medio por ticket de cada empleado, mes a mes · "
                       f"{sexo_cm.lower()} (rango global).")
            g = sel_cm.groupby(["emp", "mes"], as_index=False).agg(
                fact=("importe_kpi", "sum"), tk=("numero", "nunique"))
            g["valor"] = g.fact / g.tk.replace(0, pd.NA)
            por_mes_vista(g[["emp", "mes", "valor"]], vista_mapas,
                          lambda v: f"{eur(v, 1)} €", "Carro medio €",
                          "%{x}: %{y:,.2f} €")

            st.markdown("---")
            ancla("emp-carroind")
            st.markdown("### Carro medio de un empleado")
            cc1, cc2 = st.columns(2)
            emp_sel = cc1.selectbox("Empleado", list(EMPLEADOS_ACTIVOS.values()))
            sexo_sel = cc2.selectbox("Clientela",
                                     ["Todos", "Solo mujeres", "Solo hombres"])
            sub = en_rango(ventas_emp[ventas_emp.emp == emp_sel])
            if sexo_sel == "Solo mujeres":
                sub = sub[sub.genero == "Mujer"]
            elif sexo_sel == "Solo hombres":
                sub = sub[sub.genero == "Hombre"]
            datos_periodo = sub

            m1, m2, m3 = st.columns(3)
            m1.metric(f"Carro medio · {emp_sel}", f"{eur(carro_medio(datos_periodo), 2)} €")
            m2.metric("Tickets", eur(datos_periodo.numero.nunique()))
            m3.metric("Facturacion", f"{eur(datos_periodo.importe_kpi.sum())} €")
            st.caption(f"Calculado sobre el rango global "
                       f"({G_D1.strftime('%d/%m/%Y')} — {G_D2.strftime('%d/%m/%Y')}) · "
                       f"{sexo_sel.lower()}. Importes {IVA_LBL}. El sexo se estima por el "
                       f"nombre.")

            # Evolucion mensual del carro medio de ese empleado
            cm = sub.groupby("mes").agg(fact=("importe_kpi", "sum"),
                                        tk=("numero", "nunique")).reset_index()
            cm["carro"] = cm.fact / cm.tk
            cm["mes_lbl"] = cm.mes.map(mes_es)
            fig = px.bar(cm, x="mes_lbl", y="carro",
                         labels={"mes_lbl": "", "carro": "Carro medio €"})
            fig.update_traces(marker_color=ACCENT,
                              hovertemplate="%{x}<br>%{y:,.2f} €<extra></extra>")
            eje_meses(fig, cm.mes_lbl)
            st.plotly_chart(estilo_fig(fig, 320), width="stretch")

            # ---- Informe descargable de un empleado (confidencial) ----
            st.markdown("---")
            ancla("emp-informe")
            st.markdown("### Informe descargable de un empleado")
            st.caption(f"Genera un informe SOLO de esa persona (sin datos de las demás), "
                       f"para enseñárselo o mandárselo. Usa el periodo del filtro global "
                       f"de arriba: {G_D1.strftime('%d/%m/%Y')} — "
                       f"{G_D2.strftime('%d/%m/%Y')}.")
            emp_inf = st.selectbox("Empleado del informe",
                                   list(EMPLEADOS_ACTIVOS.values()), key="emp_informe")
            di = en_rango(ventas_emp[ventas_emp.emp == emp_inf])
            di = di[di.cliente != GENERICO] if not di.empty else di
            if di.empty:
                st.info("Esa persona no tiene ventas en el periodo elegido.")
            else:
                f_tot = di.importe_kpi.sum()
                f_tk = di.numero.nunique()
                f_cli = di.cliente.nunique()
                f_carro = f_tot / f_tk if f_tk else 0
                f_items = di.cantidad.sum()
                f_dias = di.fecha.dt.date.nunique()
                f_fdia = f_tot / f_dias if f_dias else 0
                # carro medio por sexo del cliente (estimado por nombre)
                di_m = di[di.genero == "Mujer"]
                di_h = di[di.genero == "Hombre"]
                carro_m = (di_m.importe_kpi.sum() / di_m.numero.nunique()
                           if di_m.numero.nunique() else 0)
                carro_h = (di_h.importe_kpi.sum() / di_h.numero.nunique()
                           if di_h.numero.nunique() else 0)
                # reparto de su clientela por sexo (por ticket)
                tk_m = di_m.numero.nunique()
                tk_h = di_h.numero.nunique()
                _tks = tk_m + tk_h
                pct_muj = 100 * tk_m / _tks if _tks else 0
                pct_hom = 100 * tk_h / _tks if _tks else 0
                # dia de la semana mas fuerte (media de facturacion por dia trabajado)
                _DIAS_SEM = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes",
                             "Sábado", "Domingo"]
                _dw = di.assign(_f=di.fecha.dt.normalize(), _dow=di.fecha.dt.dayofweek)
                _pdw = (_dw.groupby(["_dow", "_f"]).importe_kpi.sum()
                        .groupby(level=0).mean())
                if not _pdw.empty:
                    _bd = int(_pdw.idxmax())
                    dia_fuerte = _DIAS_SEM[_bd]
                    dia_fuerte_eur = float(_pdw.max())
                else:
                    dia_fuerte, dia_fuerte_eur = "—", 0
                por_mes = di.groupby("mes").importe_kpi.sum()
                _serv_group = (di[~di.concepto_limpio.str.strip().str.lower()
                                  .isin(set_prod)]
                               .groupby("concepto_limpio").cantidad.sum()
                               .sort_values(ascending=False))
                serv_all = _serv_group[_serv_group > 0]     # TODOS (para la tabla)
                serv_top = serv_all.head(15)                # top 15 (para la grafica)

                _card = ("<div class='card'><div class='n'>{}</div>"
                         "<div class='l'>{}</div></div>")
                cards = "".join([
                    _card.format(f"{eur(f_tot)} €", "Facturación"),
                    _card.format(eur(f_tk), "Tickets"),
                    _card.format(eur(f_cli), "Clientes atendidos"),
                    _card.format(f"{eur(f_carro, 2)} €", "Carro medio"),
                    _card.format(f"{eur(carro_m, 2)} €", "Carro medio (mujeres)"),
                    _card.format(f"{eur(carro_h, 2)} €", "Carro medio (hombres)"),
                    _card.format(eur(f_items), "Ítems"),
                    _card.format(f"{eur(f_fdia, 0)} €", "Facturación/día"),
                    _card.format(f"{eur(pct_muj, 0)}%", "Clientela mujer"),
                    _card.format(f"{eur(pct_hom, 0)}%", "Clientela hombre"),
                    _card.format(dia_fuerte, "Día más fuerte"),
                ])
                fmes = "".join(
                    f"<tr><td>{mes_es(mm)}</td><td class='r'>{eur(vv)} €</td></tr>"
                    for mm, vv in por_mes.items())
                fserv = "".join(
                    f"<tr><td>{ss}</td><td class='r'>{eur(cc)}</td></tr>"
                    for ss, cc in serv_all.items())
                svg_mes = _svg_vbars([(_MESES_ABBR[int(mm.split('-')[1]) - 1],
                                       float(vv)) for mm, vv in por_mes.items()])
                svg_serv = _svg_hbars([(ss, int(cc)) for ss, cc in serv_top.items()])

                # -- Comparativa año vs año (misma persona) --
                yr = G_D2.year
                mnums = sorted({int(mm.split('-')[1]) for mm in por_mes.index})
                femp = ventas_emp[(ventas_emp.emp == emp_inf)
                                  & (ventas_emp.cliente != GENERICO)]

                def _fa(anio):
                    s = femp[femp.fecha.dt.year == anio]
                    gg = s.groupby(s.fecha.dt.month).importe_kpi.sum()
                    return [float(gg.get(mn, 0)) for mn in mnums]

                sA, sB = _fa(yr - 1), _fa(yr)
                labs = [_MESES_ABBR[mn - 1] for mn in mnums]
                tot_prev, tot_curr = sum(sA), sum(sB)
                svg_yoy = _svg_grupo(labs, sA, sB, "#B9A886", ACCENT)
                if tot_prev > 0:
                    dp = (tot_curr - tot_prev) / tot_prev * 100
                    concl_yoy = (f"En estos meses has facturado <b>{eur(tot_curr)} €</b>, "
                                 f"un <b>{'+' if dp >= 0 else ''}{eur(dp, 1)}%</b> "
                                 f"{'más' if dp >= 0 else 'menos'} que en el mismo tramo "
                                 f"de {yr - 1} ({eur(tot_prev)} €).")
                else:
                    concl_yoy = f"No hay datos de {yr - 1} en esos meses para comparar."

                # -- Comparativa con la media del equipo (sin nombrar a nadie) --
                team = en_rango(ventas_emp)
                team = team[team.cliente != GENERICO]
                pe = team.groupby("emp").agg(f=("importe_kpi", "sum"),
                                             t=("numero", "nunique"))
                pe["carro"] = pe.f / pe.t.replace(0, pd.NA)
                team_carro = float(pe.carro.mean()) if len(pe) else 0
                team_fact = float(pe.f.mean()) if len(pe) else 0
                car_dp = (f_carro - team_carro) / team_carro * 100 if team_carro else 0
                fac_dp = (f_tot - team_fact) / team_fact * 100 if team_fact else 0

                def _pct(v):
                    c = "#2e7d32" if v >= 0 else "#b23a2a"
                    return (f"<span style='color:{c};font-weight:700'>"
                            f"{'+' if v >= 0 else ''}{eur(v, 1)}%</span>")

                bench = (
                    "<table><tr><th></th><th class='r'>Tú</th>"
                    "<th class='r'>Media del equipo</th><th class='r'>Diferencia</th></tr>"
                    f"<tr><td>Carro medio</td><td class='r'>{eur(f_carro, 2)} €</td>"
                    f"<td class='r'>{eur(team_carro, 2)} €</td>"
                    f"<td class='r'>{_pct(car_dp)}</td></tr>"
                    f"<tr><td>Facturación</td><td class='r'>{eur(f_tot)} €</td>"
                    f"<td class='r'>{eur(team_fact)} €</td>"
                    f"<td class='r'>{_pct(fac_dp)}</td></tr></table>")

                # -- Venta de producto (upselling) --
                di_low = di.concepto_limpio.str.strip().str.lower()
                es_prod_di = di_low.isin(set_prod)
                tk_prod = di[es_prod_di].numero.nunique()
                ratio_prod = tk_prod / f_tk if f_tk else 0
                prod_eur = di[es_prod_di].importe_kpi.sum()
                serv_eur = f_tot - prod_eur
                pct_serv_eur = 100 * serv_eur / f_tot if f_tot else 0
                pct_prod_eur = 100 * prod_eur / f_tot if f_tot else 0

                # -- Fidelizacion de SU clientela (visitas con ella) --
                _vis = di.assign(_d=di.fecha.dt.date).groupby("cliente")._d.nunique()
                cli_reg = int((_vis >= 2).sum())
                pct_cli_reg = 100 * cli_reg / len(_vis) if len(_vis) else 0
                _repset = set(_vis[_vis >= 2].index)
                tk_rep = di[di.cliente.isin(_repset)].numero.nunique()
                pct_tk_rep = 100 * tk_rep / f_tk if f_tk else 0

                # -- Tipo de trabajo que mas hace (familia del catalogo TPV) --
                if not servicios.empty and "familia" in servicios.columns:
                    _fammap2 = (servicios.assign(
                        _n=servicios.nombre.str.strip().str.lower())
                        .dropna(subset=["familia"])
                        .assign(familia=lambda d: d.familia.str.strip())
                        .drop_duplicates("_n").set_index("_n").familia)
                else:
                    _fammap2 = pd.Series(dtype=object)
                _sl = di[~es_prod_di].copy()
                _sl["_fam"] = (_sl.concepto_limpio.str.strip().str.lower()
                               .map(_fammap2))
                _es_vta = _sl._fam.fillna("").str.contains(
                    r"\bv(?:en)?tas?\b", case=False, regex=True)
                _fam_cnt = (_sl[~_es_vta].assign(_f=_sl._fam.fillna("Sin clasificar"))
                            .groupby("_f").cantidad.sum().sort_values(ascending=False))
                _fam_cnt = _fam_cnt[_fam_cnt > 0].head(8)
                _fam_tot = float(_fam_cnt.sum())

                def _ratio_prod(g):
                    tt = g.numero.nunique()
                    tp = g[g.concepto_limpio.str.strip().str.lower()
                           .isin(set_prod)].numero.nunique()
                    return tp / tt if tt else 0
                team_ratio = (float(team.groupby("emp").apply(_ratio_prod).mean())
                              if len(team) else 0)

                # -- Clientes suyos en riesgo (regulares que no vuelven) --
                hoy = pd.Timestamp(dt.date.today())
                femp_all = ventas_emp[(ventas_emp.emp == emp_inf)
                                      & (ventas_emp.cliente != GENERICO)]
                _dias_cli = (femp_all.assign(d=femp_all.fecha.dt.date)
                             .groupby("cliente").d.nunique())
                regulares = set(_dias_cli[_dias_cli >= 3].index)
                ult_salon = (ventas_g[ventas_g.cliente != GENERICO]
                             .groupby("cliente").fecha.max())
                tel_map = {}
                if not clientes.empty and "movil" in clientes.columns:
                    _cc = clientes.copy()
                    _cc["full"] = (_cc.nombre.astype(str).str.strip() + " "
                                   + _cc.apellidos.astype(str).str.strip()).str.strip()
                    tel_map = dict(zip(_cc.full, _cc.movil.astype(str)))
                riesgo = []
                for cli in regulares:
                    lv = ult_salon.get(cli)
                    if lv is None:
                        continue
                    dias = (hoy - lv).days
                    if 90 < dias <= 365:   # se enfriaron hace poco -> recuperables
                        riesgo.append((cli, lv.date(), round(dias / 30),
                                       tel_map.get(str(cli).strip(), "")))
                n_riesgo = len(riesgo)
                riesgo.sort(key=lambda r: -r[2])
                riesgo = riesgo[:15]

                # -- Puntos para la reunión (coaching automático) --
                serie = [float(v) for v in por_mes.values]
                tend = None
                if len(serie) >= 3:
                    if serie[-1] > serie[-3] * 1.05:
                        tend = ("tendencia",
                                "Tu facturación viene subiendo en los últimos meses.")
                    elif serie[-1] < serie[-3] * 0.95:
                        tend = ("tendencia", "Tu facturación viene bajando en los "
                                "últimos meses; conviene hablarlo.")
                puntos = []
                if car_dp >= 10:
                    puntos.append(("fuerte", f"Carro medio {eur(f_carro, 2)} € "
                                   f"(+{eur(car_dp, 0)}% sobre la media): trabajas "
                                   "servicios de valor."))
                elif car_dp <= -10:
                    puntos.append(("mejora", f"Carro medio {eur(f_carro, 2)} € "
                                   f"({eur(car_dp, 1)}% bajo la media): intenta subir "
                                   "el valor por cliente (más color/tratamiento)."))
                if fac_dp >= 12:
                    puntos.append(("fuerte", f"Facturas un +{eur(fac_dp, 0)}% más que "
                                   "la media del equipo."))
                elif fac_dp <= -15:
                    puntos.append(("mejora", f"Facturas un {eur(fac_dp, 1)}% menos que "
                                   "la media; veamos qué la frena."))
                if ratio_prod < 0.10:
                    puntos.append(("mejora", f"Vendes producto solo en el "
                                   f"{eur(ratio_prod * 100, 0)}% de tus tickets: gran "
                                   "margen para recomendar producto."))
                elif ratio_prod >= 0.25:
                    puntos.append(("fuerte", f"Vendes producto en el "
                                   f"{eur(ratio_prod * 100, 0)}% de tus tickets: muy "
                                   "buen upselling."))
                if tend:
                    puntos.append(tend)
                if n_riesgo:
                    puntos.append(("alerta", f"{eur(n_riesgo)} clientes tuyos habituales "
                                   "llevan entre 3 y 12 meses sin venir: aún son "
                                   "recuperables, podrías escribirles tú."))
                puntos = puntos[:5] or [
                    ("tendencia", "Sin señales destacables este periodo; buen momento "
                     "para fijar un objetivo.")]

                _tlab = {"fuerte": "Punto fuerte", "mejora": "A mejorar",
                         "tendencia": "Tendencia", "alerta": "Aviso"}
                puntos_html = "".join(
                    f"<div class='punto p-{t}'><b>{_tlab[t]}:</b> {tx}</div>"
                    for t, tx in puntos)
                prod_html = (
                    f"<p>Vendes producto en el <b>{eur(ratio_prod * 100, 0)}%</b> de "
                    f"tus tickets (media del equipo {eur(team_ratio * 100, 0)}%). "
                    f"Producto vendido en el periodo: <b>{eur(prod_eur)} €</b>.</p>"
                    f"<p>Reparto de su facturación: <b>{eur(serv_eur)} €</b> en "
                    f"servicios y <b>{eur(prod_eur)} €</b> en producto.</p>"
                    + _svg_donut([("Servicios", serv_eur, ACCENT),
                                  ("Producto", prod_eur, ACCENT_SOFT)], size=200))

                # -- Bloques HTML de las secciones nuevas del informe --
                sexo_html = (
                    "<p>Reparto de su clientela por sexo (estimado por el nombre del "
                    "cliente):</p>"
                    + _svg_donut([("Mujeres", tk_m, ACCENT),
                                  ("Hombres", tk_h, "#2C6E9B")], size=200))
                fidel_html = (
                    f"<p><b>{eur(pct_cli_reg, 0)}%</b> de sus clientes repiten con ella "
                    f"(2 o más visitas en el periodo) y <b>{eur(pct_tk_rep, 0)}%</b> de "
                    f"sus tickets son de clientes que ya la conocían. Cuanto más alto, "
                    f"mejor fideliza a su clientela.</p>")
                if _fam_tot:
                    _ff = "".join(
                        f"<tr><td>{f}</td><td class='r'>{eur(c)}</td>"
                        f"<td class='r'>{eur(100 * c / _fam_tot, 0)}%</td></tr>"
                        for f, c in _fam_cnt.items())
                    fam_html = ("<table><tr><th>Tipo de servicio</th>"
                                "<th class='r'>Veces</th><th class='r'>% de su "
                                f"trabajo</th></tr>{_ff}</table>")
                else:
                    fam_html = "<p>Sin datos de tipo de servicio en el periodo.</p>"
                if riesgo:
                    _fr = "".join(
                        f"<tr><td>{c}</td><td class='r'>{lv.strftime('%d/%m/%Y')}</td>"
                        f"<td class='r'>{eur(mm)}</td><td>{tel}</td></tr>"
                        for c, lv, mm, tel in riesgo)
                    riesgo_html = ("<table><tr><th>Cliente</th>"
                                   "<th class='r'>Última visita</th>"
                                   "<th class='r'>Meses sin venir</th>"
                                   f"<th>Teléfono</th></tr>{_fr}</table>")
                else:
                    riesgo_html = "<p>Ninguno ahora mismo. ¡Buena retención!</p>"

                _style = (
                    "<style>body{font-family:Arial,Helvetica,sans-serif;color:#3D372E;"
                    "background:#F4EDDE;margin:0;padding:32px;}"
                    ".logo{font-size:2rem;font-weight:800;letter-spacing:.5em;}"
                    ".sub{letter-spacing:.3em;font-size:.75rem;color:#3D372E;"
                    "border-bottom:2px solid #3D372E;display:inline-block;"
                    "padding-bottom:2px;margin-bottom:18px;}"
                    "h1{margin:.2rem 0;}"
                    ".per{color:#8a7d68;margin:.2rem 0 1.4rem 0;}"
                    ".grid{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:1.5rem;}"
                    ".card{background:#EAE0CC;border:1px solid #D9CDB2;border-radius:10px;"
                    "padding:14px 18px;min-width:150px;}"
                    ".card .n{font-size:1.4rem;font-weight:700;color:#A75D3B;}"
                    ".card .l{font-size:.8rem;color:#6b6155;}"
                    "table{border-collapse:collapse;width:100%;max-width:560px;"
                    "margin:.4rem 0 1rem 0;}"
                    "td,th{border-bottom:1px solid #D9CDB2;padding:6px 10px;"
                    "font-size:.9rem;} th{color:#8a7d68;font-weight:600;text-align:left;}"
                    ".r{text-align:right;}"
                    "h2{font-size:1.05rem;margin:1.4rem 0 .3rem 0;}"
                    ".leg{font-size:.8rem;color:#6b6155;margin:.2rem 0;}"
                    ".sq{display:inline-block;width:11px;height:11px;border-radius:2px;"
                    "vertical-align:middle;margin-right:4px;}"
                    ".concl{background:#EAE0CC;border-left:4px solid #A75D3B;"
                    "padding:10px 14px;border-radius:4px;margin:.4rem 0 1rem 0;"
                    "font-size:.92rem;}"
                    ".reunion{background:#fff;border:1px solid #D9CDB2;"
                    "border-radius:10px;padding:14px 16px;margin:.4rem 0 1.6rem 0;}"
                    ".punto{border-left:4px solid #999;padding:6px 12px;margin:6px 0;"
                    "font-size:.92rem;}"
                    ".p-fuerte{border-left-color:#2e7d32;}"
                    ".p-mejora{border-left-color:#E0A500;}"
                    ".p-alerta{border-left-color:#b23a2a;}"
                    ".p-tendencia{border-left-color:#2C6E9B;}"
                    ".pie{color:#8a7d68;font-size:.8rem;margin-top:2rem;}</style>")
                html_inf = (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>Informe {emp_inf}</title>{_style}</head><body>"
                    "<div class='logo'>SALÓN BI</div>"
                    "<div class='sub'>DASHBOARD DE PELUQUERÍA</div>"
                    f"<h1>Informe de {emp_inf}</h1>"
                    f"<p class='per'>Periodo: {G_D1.strftime('%d/%m/%Y')} — "
                    f"{G_D2.strftime('%d/%m/%Y')} · Importes {IVA_LBL}</p>"
                    "<h2>Valoraciones</h2>"
                    f"<div class='reunion'>{puntos_html}</div>"
                    f"<div class='grid'>{cards}</div>"
                    f"<p class='per'>Su día de la semana más fuerte es el "
                    f"<b>{dia_fuerte}</b> (de media {eur(dia_fuerte_eur, 0)} € en las "
                    f"jornadas de ese día).</p>"
                    f"<h2>Facturación por mes</h2>{svg_mes}<table>{fmes}</table>"
                    "<h2>Servicios que más hace (veces)</h2>"
                    "<p class='per'>La gráfica muestra los 15 más hechos; la tabla, "
                    "todos los servicios que realiza.</p>"
                    f"{svg_serv}<table>{fserv}</table>"
                    f"<h2>Venta de producto (upselling)</h2>{prod_html}"
                    f"<h2>Su clientela</h2>{sexo_html}{fidel_html}"
                    f"<h2>Tipo de trabajo que más hace</h2>"
                    "<p class='per'>Reparto de sus servicios por familia (color, corte, "
                    "balayage…), para ver en qué está más especializada.</p>"
                    f"{fam_html}"
                    f"<h2>Comparativa contigo misma: {yr} vs {yr - 1}</h2>"
                    f"<div class='leg'><span class='sq' style='background:#B9A886'>"
                    f"</span>{yr - 1} &nbsp;&nbsp;<span class='sq' "
                    f"style='background:{ACCENT}'></span>{yr}</div>"
                    f"{svg_yoy}<div class='concl'>{concl_yoy}</div>"
                    "<h2>Comparativa con la media del equipo</h2>"
                    f"{bench}<div class='concl'>Tu carro medio está {_pct(car_dp)} "
                    f"respecto a la media, y tu facturación {_pct(fac_dp)}. "
                    "(La media del equipo no revela datos de otras personas.)</div>"
                    "<h2>Clientes suyos que se están enfriando</h2>"
                    "<p class='per'>Clientes habituales de esta persona (3+ visitas) "
                    "que llevan entre 3 y 12 meses sin venir (aún recuperables). Buena "
                    "lista para que ella misma les escriba. Se muestran los 15 más "
                    "urgentes.</p>"
                    f"{riesgo_html}"
                    "<p class='pie'>Generado con Salón BI</p>"
                    "</body></html>")
                st.markdown("**Valoraciones (adelanto):**")
                _pcolor = {"fuerte": "green", "mejora": "orange",
                           "tendencia": "blue", "alerta": "red"}
                for _t, _tx in puntos:
                    st.markdown(f"- :{_pcolor[_t]}[**{_tlab[_t]}:**] {_tx}")
                st.caption(f"Venta de producto: {eur(ratio_prod * 100, 0)}% de sus "
                           f"tickets (media equipo {eur(team_ratio * 100, 0)}%) · "
                           f"Clientes suyos enfriándose (3-12 meses): {n_riesgo}")
                st.caption(f"Clientela: {eur(pct_muj, 0)}% mujeres / {eur(pct_hom, 0)}% "
                           f"hombres · Repiten con ella: {eur(pct_cli_reg, 0)}% · "
                           f"Día más fuerte: {dia_fuerte} · El informe descargable "
                           f"incluye roscos y el tipo de servicio que más hace.")

                st.download_button(
                    f"Descargar informe de {emp_inf} (HTML para imprimir/enviar)",
                    html_inf.encode("utf-8"),
                    file_name=f"informe_{emp_inf}_{G_D1}_{G_D2}.html",
                    mime="text/html", type="primary")
                st.caption("Se descarga un archivo que se abre en cualquier navegador y "
                           "se puede imprimir o guardar como PDF. No incluye sueldo ni "
                           "datos de otras empleadas.")


# =========================================================================== #
# SERVICIOS
# =========================================================================== #
with tab_serv:
    st.subheader("Servicios que mas se hacen")
    if not hay_datos:
        aviso_sin_datos()
    else:
        menu_lateral([
            ("serv-top", "Servicios más hechos"),
            ("serv-fam", "Agrupado por tipo"),
            ("serv-tend", "Suben y bajan"),
            ("serv-sexo", "Por sexo (H/M)"),
        ])
        ancla("serv-top")
        if G_D1 is not None:
            st.caption(f"Periodo (filtro global): {G_D1.strftime('%d/%m/%Y')} — "
                       f"{G_D2.strftime('%d/%m/%Y')}")
        st.caption("Solo servicios de peluqueria: se deja fuera la venta de producto "
                   "(champus, mascarillas, ceras...).")
        TODOS = "Todos (peluqueria)"
        cse1, cse2 = st.columns(2)
        emp_serv = cse1.selectbox("Empleado", [TODOS] + list(EMPLEADOS_ACTIVOS.values()))

        if emp_serv == TODOS:
            base = en_rango(ventas_g).copy()          # toda la peluqueria
        else:
            base = en_rango(ventas_emp[ventas_emp.emp == emp_serv]).copy()

        base["_n"] = base.concepto_limpio.str.strip().str.lower()
        serv_ventas = base[base._n.isin(set_serv)] if set_serv else base

        # Familia de cada servicio (catalogo TPV) y filtro de venta de producto:
        # se dejan fuera las familias "vta"/"venta"/"Ventas" (champus, ceras...),
        # asi los dos graficos muestran solo servicios de peluqueria reales.
        fam_map = (servicios.assign(_n=servicios.nombre.str.strip().str.lower())
                   .dropna(subset=["familia"])
                   .assign(familia=lambda d: d.familia.str.strip())
                   .drop_duplicates("_n").set_index("_n").familia)
        serv_ventas = serv_ventas.copy()
        serv_ventas["familia"] = (serv_ventas._n.map(fam_map)
                                  .replace("", pd.NA).fillna("Sin clasificar"))
        _es_venta = serv_ventas.familia.str.contains(
            r"\bv(?:en)?tas?\b", case=False, regex=True, na=False)
        serv_ventas = serv_ventas[~_es_venta]

        top_n = cse2.slider("Cuantos mostrar", 5, 30, 15)
        top = (serv_ventas.groupby("concepto_limpio")
               .agg(veces=("cantidad", "sum"), facturacion=("importe_kpi", "sum"))
               .sort_values("veces", ascending=False).head(top_n).reset_index())

        _ts = top.sort_values("veces")
        fig = px.bar(_ts, x="veces", y="concepto_limpio", orientation="h",
                     text=_ts.veces.map(lambda v: eur(v)),
                     labels={"veces": "Veces realizado", "concepto_limpio": ""})
        fig.update_traces(marker_color=ACCENT, textposition="outside",
                          cliponaxis=False)
        fig.update_xaxes(range=[0, _ts.veces.max() * 1.12])
        st.plotly_chart(estilo_fig(fig, 30 * top_n + 60), width="stretch")

        st.dataframe(
            top.rename(columns={"concepto_limpio": "Servicio", "veces": "Veces",
                                "facturacion": "Facturacion €"})
            .style.format({"Facturacion €": lambda v: f"{eur(v)} €",
                           "Veces": lambda v: eur(v)}),
            width="stretch", hide_index=True)

        # ---- Agrupado por FAMILIA de servicio -------------------------------
        # Junta los servicios casi iguales (Color + Color Plus, los distintos
        # Balayage, etc.) usando la clasificacion "familia" del propio TPV.
        st.divider()
        ancla("serv-fam")
        st.markdown("### Agrupado por tipo de servicio")
        st.caption("Junta en una sola familia los servicios que son practicamente "
                   "lo mismo (p. ej. Color y Color Plus, o los distintos Balayage), "
                   "usando la clasificacion del catalogo del TPV.")
        topf = (serv_ventas.groupby("familia")
                .agg(veces=("cantidad", "sum"), facturacion=("importe_kpi", "sum"))
                .sort_values("veces", ascending=False).head(top_n).reset_index())

        _tf = topf.sort_values("veces")
        figf = px.bar(_tf, x="veces", y="familia", orientation="h",
                      text=_tf.veces.map(lambda v: eur(v)),
                      labels={"veces": "Veces realizado", "familia": ""})
        figf.update_traces(marker_color=ACCENT, textposition="outside",
                           cliponaxis=False)
        figf.update_xaxes(range=[0, _tf.veces.max() * 1.12])
        st.plotly_chart(estilo_fig(figf, 30 * len(topf) + 60), width="stretch")

        st.dataframe(
            topf.rename(columns={"familia": "Familia", "veces": "Veces",
                                 "facturacion": "Facturacion €"})
            .style.format({"Facturacion €": lambda v: f"{eur(v)} €",
                           "Veces": lambda v: eur(v)}),
            width="stretch", hide_index=True)

        # ---- Servicios que suben y bajan (ano vs ano) ----
        st.divider()
        ancla("serv-tend")
        st.markdown("### Servicios que suben y bajan")
        st.caption("Compara cuántas veces se hizo cada servicio entre dos años. Útil "
                   "para ver qué técnicas crecen o caen (formación, compras). Usa todo "
                   "el histórico, no el filtro de fechas.")
        _anios_s = sorted(ventas_g.anio.unique())
        if len(_anios_s) < 2:
            st.info("Necesito al menos dos años de datos para comparar.")
        else:
            ct1, ct2 = st.columns(2)
            _a_ant = ct1.selectbox("Año base", _anios_s, index=len(_anios_s) - 2,
                                   key="serv_t_a")
            _a_new = ct2.selectbox("Año a comparar", _anios_s, index=len(_anios_s) - 1,
                                   key="serv_t_b")
            _sv = ventas_g[~ventas_g.concepto_limpio.str.strip().str.lower()
                           .isin(set_prod)]

            def _cnt(a):
                return _sv[_sv.anio == a].groupby("concepto_limpio").cantidad.sum()

            _comp = pd.DataFrame({"antes": _cnt(_a_ant),
                                  "ahora": _cnt(_a_new)}).fillna(0)
            _comp["dif"] = _comp.ahora - _comp.antes
            _comp = _comp[(_comp.antes + _comp.ahora) >= 10]
            if _comp.empty:
                st.info("No hay suficientes servicios para comparar esos años.")
            else:
                def _barra_dif(df, titulo, color, col):
                    d = df.sort_values("dif")
                    f = px.bar(
                        d, x="dif", y="concepto_limpio", orientation="h",
                        text=d.dif.map(
                            lambda v: f"{'+' if v > 0 else ''}{eur(v)}"),
                        labels={"dif": "Diferencia de veces",
                                "concepto_limpio": ""})
                    f.update_traces(marker_color=color, textposition="outside",
                                    cliponaxis=False)
                    col.markdown(f"**{titulo}**")
                    col.plotly_chart(estilo_fig(f, 30 * len(d) + 80),
                                     width="stretch")

                cs1, cs2 = st.columns(2)
                _barra_dif(_comp.sort_values("dif", ascending=False).head(8)
                           .reset_index(), f"Suben ({_a_ant} → {_a_new})",
                           "#2E8B4A", cs1)
                _barra_dif(_comp.sort_values("dif").head(8).reset_index(),
                           f"Bajan ({_a_ant} → {_a_new})", "#C0392B", cs2)
                if _a_new == max(_anios_s):
                    st.caption("Ojo: si el último año aún no ha terminado, tendrá "
                               "menos meses y casi todo saldrá «bajando». Para una "
                               "lectura justa, compara años completos.")

        # ---- Servicios por sexo (hombre vs mujer) ----
        st.divider()
        ancla("serv-sexo")
        st.markdown("### Servicios por sexo (hombre vs mujer)")
        st.caption("Qué servicios hacen los hombres y cuáles las mujeres, en el periodo "
                   "y empleado elegidos arriba. El sexo es estimado por el nombre; los "
                   "no clasificados quedan fuera de este desglose.")
        _sx = serv_ventas[serv_ventas.genero.isin(["Hombre", "Mujer"])]
        if _sx.empty:
            st.info("No hay datos para el desglose por sexo en esta selección.")
        else:
            _topcs = (_sx.groupby("concepto_limpio").cantidad.sum()
                      .sort_values(ascending=False).head(top_n).index)
            _gsx = (_sx[_sx.concepto_limpio.isin(_topcs)]
                    .groupby(["concepto_limpio", "genero"], as_index=False)
                    .cantidad.sum())
            _orden = (_gsx.groupby("concepto_limpio").cantidad.sum()
                      .sort_values().index.tolist())
            figsx = px.bar(_gsx, x="cantidad", y="concepto_limpio", color="genero",
                           orientation="h", barmode="group",
                           text="cantidad",
                           color_discrete_map={"Mujer": ACCENT, "Hombre": "#2C6E9B"},
                           labels={"cantidad": "Veces realizado",
                                   "concepto_limpio": "", "genero": ""})
            figsx.update_traces(texttemplate="%{text:,.0f}", textposition="outside",
                                cliponaxis=False)
            figsx.update_yaxes(categoryorder="array", categoryarray=_orden)
            figsx.update_xaxes(range=[0, _gsx.cantidad.max() * 1.2])
            st.plotly_chart(estilo_fig(figsx, 34 * len(_topcs) + 80), width="stretch")

            # Reparto global y servicio top de cada sexo
            _tot_h = int(_sx[_sx.genero == "Hombre"].cantidad.sum())
            _tot_m = int(_sx[_sx.genero == "Mujer"].cantidad.sum())
            _tot = _tot_h + _tot_m
            _top_h = (_sx[_sx.genero == "Hombre"].groupby("concepto_limpio")
                      .cantidad.sum().sort_values(ascending=False))
            _top_m = (_sx[_sx.genero == "Mujer"].groupby("concepto_limpio")
                      .cantidad.sum().sort_values(ascending=False))
            _l_h = f"**{_top_h.index[0]}**" if len(_top_h) else "—"
            _l_m = f"**{_top_m.index[0]}**" if len(_top_m) else "—"
            _pct_h = (_tot_h / _tot * 100) if _tot else 0
            st.caption(f"Del total de servicios clasificados, **{eur(_pct_h, 0)}% los "
                       f"hacen hombres** y **{eur(100 - _pct_h, 0)}% mujeres**. "
                       f"Lo que más hacen los hombres: {_l_h}. Las mujeres: {_l_m}.")


# =========================================================================== #
# PRODUCTOS
# =========================================================================== #
with tab_prod:
    st.subheader("Facturación y beneficio por línea de producto")
    if not hay_datos or productos.empty:
        aviso_sin_datos()
    else:
        menu_lateral([
            ("prod-fam", "Por línea de producto"),
            ("prod-top", "Los que más se venden"),
            ("prod-cruzada", "Venta cruzada"),
            ("prod-norepite", "Probaron y no repiten"),
        ])
        ancla("prod-fam")
        if G_D1 is not None:
            st.caption(f"Periodo (filtro global): {G_D1.strftime('%d/%m/%Y')} — "
                       f"{G_D2.strftime('%d/%m/%Y')}")
        st.caption("La **gráfica** muestra lo que **factura** cada línea "
                   "(Cronologiste, Nutritive, Blond Absolute…) = lo que paga el "
                   "cliente. En la **tabla de debajo** tienes además el **beneficio** "
                   "(lo que ganas: venta − lo que te costó el bote) y las unidades. "
                   "Una línea puede facturar mucho y dejar poco margen, por eso están "
                   "las dos cosas juntas.")

        def _linea_limpia(f):
            """'Ritual-vta cronologiste' -> 'Cronologiste'. Nombre de linea legible."""
            s = str(f).strip()
            if "sin asignar" in s.lower():
                return "Sin asignar"
            for pre in ("ritual-vta ", "ritual-vta", "vta ", "ventas ", "venta "):
                if s.lower().startswith(pre):
                    s = s[len(pre):].strip()
                    break
            return (s[:1].upper() + s[1:]) if s else "Sin línea"

        prod = productos.copy()
        prod["_n"] = prod.nombre.str.strip().str.lower()
        prod["coste"] = pd.to_numeric(prod.coste, errors="coerce").fillna(0)
        prod["precio"] = pd.to_numeric(prod.precio, errors="coerce").fillna(0)

        vp = en_rango(ventas_g).copy()
        vp["_n"] = vp.concepto_limpio.str.strip().str.lower()
        vp = vp.merge(prod[["_n", "familia", "coste", "precio"]], on="_n", how="inner")
        vp["beneficio"] = (vp.precio - vp.coste) * vp.cantidad
        vp["linea"] = vp.familia.map(_linea_limpia)

        st.caption(f"{eur(len(vp))} lineas de venta identificadas como producto de "
                   f"catalogo (con coste y precio conocidos).")

        fam = vp.groupby("linea").agg(
            unidades=("cantidad", "sum"), ingreso=("importe_kpi", "sum"),
            beneficio=("beneficio", "sum"),
        ).reset_index().sort_values("ingreso", ascending=False)

        # Grafica = FACTURACION por linea
        _fb = fam.sort_values("ingreso")
        fig = px.bar(_fb, x="ingreso", y="linea", orientation="h",
                     text=_fb.ingreso.map(lambda v: f"{eur(v)} €"),
                     custom_data=["beneficio", "unidades"],
                     labels={"ingreso": "€ facturados", "linea": ""})
        fig.update_traces(marker_color=ACCENT, textposition="outside",
                          cliponaxis=False,
                          hovertemplate="%{y}<br>Factura: %{x:,.2f} €<br>"
                                        "Beneficio: %{customdata[0]:,.2f} €<br>"
                                        "%{customdata[1]} unidades<extra></extra>")
        fig.update_xaxes(range=[0, fam.ingreso.max() * 1.18])
        st.plotly_chart(estilo_fig(fig, max(300, 26 * len(fam))), width="stretch")
        if not fam.empty:
            _mej = fam.iloc[0]
            st.caption(f"La línea que más factura es **{_mej.linea}** "
                       f"({eur(_mej.ingreso)} €, con {eur(_mej.beneficio)} € de "
                       f"beneficio).")

        # Tabla debajo = facturacion + BENEFICIO + unidades
        st.dataframe(
            fam.rename(columns={"linea": "Línea", "unidades": "Unidades",
                                "ingreso": "Facturación €", "beneficio": "Beneficio €"})
            .style.format({"Facturación €": lambda v: f"{eur(v)} €",
                           "Beneficio €": lambda v: f"{eur(v)} €",
                           "Unidades": lambda v: eur(v)}),
            width="stretch", hide_index=True)
        st.info("Solo cuenta el producto vendido al cliente. El champu/tinte gastado "
                "dentro de un servicio no se puede separar todavia (no viene en el TPV).")

        # ---- Productos que MAS se venden (por unidades) ----
        st.divider()
        ancla("prod-top")
        st.markdown("### Los productos que más se venden")
        st.caption("Los productos de tienda con más unidades vendidas en el periodo "
                   "del filtro global. Se actualiza solo. Al lado de cada barra, las "
                   "unidades; pasa el ratón para ver también lo facturado.")
        if vp.empty:
            st.info("No hay ventas de producto identificadas en el periodo elegido.")
        else:
            _top = (vp.groupby("concepto_limpio")
                    .agg(unidades=("cantidad", "sum"),
                         ingreso=("importe_kpi", "sum"))
                    .reset_index().sort_values("unidades", ascending=False)
                    .head(12).sort_values("unidades"))
            f_top = px.bar(
                _top, x="unidades", y="concepto_limpio", orientation="h",
                text=_top.unidades.map(lambda v: f"{eur(v)} u."),
                custom_data=["ingreso"],
                labels={"unidades": "Unidades vendidas", "concepto_limpio": ""})
            f_top.update_traces(
                marker_color=ACCENT, textposition="outside", cliponaxis=False,
                hovertemplate="%{y}<br>%{x} unidades<br>%{customdata[0]:,.2f} € "
                              "facturados<extra></extra>")
            f_top.update_xaxes(range=[0, _top.unidades.max() * 1.18])
            st.plotly_chart(estilo_fig(f_top, max(300, 30 * len(_top) + 60)),
                            width="stretch")

        # ---- Venta cruzada de producto ----
        st.divider()
        ancla("prod-cruzada")
        st.markdown("### Venta de producto (venta cruzada)")
        st.caption("Qué % de los tickets se llevan también algún producto de tienda "
                   "(alto margen). Enseña dónde hay oportunidad de recomendar bote. "
                   "Usa el periodo del filtro global. Aviso: hasta 2025 el producto "
                   "apenas se registraba en el TPV como artículo, así que este dato "
                   "solo es fiable de 2026 en adelante (no compares con años previos).")
        _vp2 = en_rango(ventas_g).assign(
            _n=lambda d: d.concepto_limpio.str.strip().str.lower())
        _vp2["es_prod"] = _vp2._n.isin(set_prod)
        _tk = _vp2.groupby("numero").es_prod.any()
        if len(_tk) == 0:
            st.info("No hay ventas en el periodo elegido arriba.")
        else:
            _pct_salon = _tk.mean() * 100
            c1, c2 = st.columns(2)
            c1.metric("Tickets con producto (salón)", f"{eur(_pct_salon, 0)}%")
            c2.metric("Cuántos", f"{eur(int(_tk.sum()))} de {eur(len(_tk))} tickets")

            _ve2 = en_rango(ventas_emp).assign(
                _n=lambda d: d.concepto_limpio.str.strip().str.lower())
            _ve2["es_prod"] = _ve2._n.isin(set_prod)

            def _pct_emp(d):
                t = d.groupby("numero").es_prod.any()
                return t.mean() * 100 if len(t) else 0

            _be = (_ve2.groupby("emp").apply(_pct_emp)
                   .reset_index(name="pct").sort_values("pct"))
            if not _be.empty:
                fig_c = px.bar(_be, x="pct", y="emp", orientation="h",
                               text=_be.pct.map(lambda v: f"{eur(v, 0)}%"),
                               labels={"pct": "% de sus tickets con producto",
                                       "emp": ""})
                fig_c.update_traces(marker_color=ACCENT, textposition="outside",
                                    cliponaxis=False)
                fig_c.update_xaxes(range=[0, max(_be.pct.max() * 1.2, 5)])
                st.plotly_chart(estilo_fig(fig_c, 30 * len(_be) + 80),
                                width="stretch")
                st.caption("Cada bote vendido es margen casi directo. Las que están "
                           "por debajo tienen el mayor margen de mejora recomendando "
                           "producto.")

        # ---- Clientes que probaron producto y no repiten ----
        st.divider()
        ancla("prod-norepite")
        _yr = dt.date.today().year
        st.markdown("### Clientes que probaron producto y no repiten")
        st.caption(f"Personas que han comprado algún producto de tienda (champú, "
                   f"acondicionador…) **una sola vez en {_yr}** y no lo han vuelto a "
                   f"comprar en lo que va de año. Es solo información: sirve para "
                   f"interesarse por qué no repiten (precio, no les gustó, se "
                   f"olvidan…). Solo cuenta compras de {_yr} (no años anteriores); "
                   f"recuerda que el producto se registra bien en el TPV desde 2026.")

        def _nkp(s):
            return " ".join(genero._norm(s).split())

        _pl = ventas_g[ventas_g.concepto_limpio.str.strip().str.lower().isin(set_prod)
                       & ventas_g.cliente.notna()
                       & (ventas_g.cliente != GENERICO)].copy()
        _pl["_d"] = _pl.fecha.dt.normalize()
        _pl = _pl[_pl._d.dt.year == _yr]           # SOLO compras de producto de este año
        if _pl.empty:
            st.info(f"No hay compras de producto registradas en {_yr} todavía.")
        else:
            _pl["_k"] = _pl.cliente.astype(str).map(_nkp)
            _gp = _pl.groupby("_k").agg(
                ocasiones=("_d", "nunique"), ult_compra=("_d", "max"),
                cliente=("cliente", "first"),
                prod=("concepto_limpio", lambda s: ", ".join(pd.unique(s.str.strip()))))
            _hoy_p = pd.Timestamp(dt.date.today())
            _uno = _gp[_gp.ocasiones == 1].copy()   # compraron UNA vez este año
            _uno["dias"] = (_hoy_p - _uno.ult_compra).dt.days
            # Última visita al salón (cualquier ticket) para saber si sigue viniendo.
            _lv = (ventas_g[ventas_g.cliente != GENERICO]
                   .assign(_k=lambda d: d.cliente.astype(str).map(_nkp),
                           _d=lambda d: d.fecha.dt.normalize())
                   .groupby("_k")._d.max())
            _uno["ult_visita"] = _uno.index.map(_lv)
            _uno["activo"] = (_hoy_p - _uno.ult_visita).dt.days <= 365
            # Móvil desde la ficha de clientes (informativo).
            if not clientes.empty:
                _cm2 = clientes.copy()
                _cm2["_k"] = (_cm2.nombre.fillna("").astype(str) + " "
                              + _cm2.apellidos.fillna("").astype(str)).map(_nkp)
                _mov = _cm2.groupby("_k").movil.first()
                _uno["movil"] = _uno.index.map(_mov)
            else:
                _uno["movil"] = ""

            cA, cB = st.columns([2, 2])
            _min = cA.segmented_control(
                "Desde su compra ha pasado al menos",
                ["1 mes", "2 meses", "3 meses", "6 meses"], default="2 meses")
            _umb = {"1 mes": 30, "2 meses": 60, "3 meses": 90, "6 meses": 180}.get(
                _min or "2 meses", 60)
            _solo_act = cB.toggle("Solo los que siguen viniendo al salón", value=True,
                                  help="Clientes con alguna visita en el último año.")
            tgt = _uno[_uno.dias >= _umb].copy()
            if _solo_act:
                tgt = tgt[tgt.activo]
            tgt = tgt.sort_values(["activo", "dias"], ascending=[False, False])

            st.metric("Clientes que probaron y no repiten", eur(len(tgt)))
            if tgt.empty:
                st.success("No hay nadie que cumpla el criterio ahora mismo.")
            else:
                _exp = tgt.reset_index(drop=True)[
                    ["cliente", "movil", "prod", "ult_compra", "dias", "activo"]].copy()
                _exp["ult_compra"] = _exp.ult_compra.dt.strftime("%d/%m/%Y")
                _exp["activo"] = _exp.activo.map({True: "sí", False: "no"})
                _exp = _exp.rename(columns={
                    "cliente": "Cliente", "movil": "Móvil", "prod": "Producto",
                    "ult_compra": "Fecha compra", "dias": "Días desde compra",
                    "activo": "Sigue viniendo"})
                st.download_button(
                    "Descargar lista (Excel)",
                    _to_xlsx(_exp),
                    file_name=f"producto_no_repiten_{_yr}.xlsx", mime=_XLSX_MIME)
                st.dataframe(_exp, width="stretch", hide_index=True)


# =========================================================================== #
# CLIENTES
# =========================================================================== #
with tab_cli:
    st.subheader("Frecuencia de visita de los clientes")
    if not hay_datos:
        aviso_sin_datos()
    else:
        menu_lateral([
            ("cli-frec", "Frecuencia de visita"),
            ("cli-fidel", "Fidelidad ese año"),
            ("cli-rank", "Ranking de clientes"),
            ("cli-antig", "Fidelización por antigüedad"),
            ("cli-freq", "Fidelización por frecuencia"),
            ("cli-nuevos", "Clientes nuevos y retención"),
        ])
        ancla("cli-frec")
        vg_cli = ventas_g[ventas_g.cliente != GENERICO]  # fuera el "cajon de sastre"
        visitas = (vg_cli.assign(dia=vg_cli.fecha.dt.date)
                   .groupby(["cliente", "anio"]).dia.nunique()
                   .reset_index(name="visitas"))
        anios = sorted(visitas.anio.unique())
        anio_sel = st.selectbox("Año", anios, index=len(anios) - 1)
        vis_a = visitas[visitas.anio == anio_sel]

        c1, c2, c3 = st.columns(3)
        c1.metric("Clientes que vinieron", eur(vis_a.cliente.nunique()))
        c2.metric("Visitas medias/cliente", eur(vis_a.visitas.mean(), 1))
        c3.metric("Cliente mas fiel", f"{eur(vis_a.visitas.max())} visitas")

        ancla("cli-fidel")
        st.markdown("### Fidelidad de los clientes ese año")
        st.caption("Clientes agrupados por cuántas veces vinieron. De un vistazo se ve "
                   "cuánta gente probó y no volvió frente a los clientes fieles.")
        bins = [0, 1, 3, 9, float("inf")]
        labels = ["Solo 1 vez", "2-3 veces", "4-9 veces", "10 o más"]
        seg = pd.cut(vis_a.visitas, bins=bins, labels=labels)
        dist = seg.value_counts().reindex(labels).reset_index()
        dist.columns = ["tramo", "clientes"]
        _tot = int(dist.clientes.sum())
        dist["pct"] = dist.clientes / _tot * 100 if _tot else 0
        dist["etq"] = dist.apply(
            lambda r: f"{eur(r.clientes)}  ({eur(r.pct, 0)}%)", axis=1)
        fig = px.bar(dist, x="clientes", y="tramo", orientation="h", text="etq",
                     labels={"clientes": "Nº de clientes", "tramo": ""})
        fig.update_traces(marker_color=ACCENT, textposition="outside",
                          cliponaxis=False,
                          hovertemplate="%{y}: %{x} clientes<extra></extra>")
        fig.update_yaxes(categoryorder="array", categoryarray=labels[::-1])
        fig.update_xaxes(range=[0, dist.clientes.max() * 1.22])
        st.plotly_chart(estilo_fig(fig, 300), width="stretch")

        una_vez = int((vis_a.visitas == 1).sum())
        fieles = int((vis_a.visitas >= 10).sum())
        st.caption(f"En {anio_sel}: **{una_vez}** clientes vinieron una sola vez "
                   f"(gente que probo y no volvio) y **{fieles}** vinieron 10 veces "
                   f"o mas (los mas fieles).")

        ancla("cli-rank")
        st.markdown("### Ranking de clientes por visitas")
        colr = st.columns([2, 3])[0]
        colr.dataframe(
            vis_a.sort_values("visitas", ascending=False)
            .rename(columns={"cliente": "Cliente", "visitas": "Visitas"})
            [["Cliente", "Visitas"]].head(50),
            width="stretch", hide_index=True,
            column_config={"Visitas": st.column_config.NumberColumn(width="small")})

        # ------------------------------------------------------------------ #
        # Fidelizacion (usa el FILTRO DE FECHAS GLOBAL de arriba)
        # ------------------------------------------------------------------ #
        # Primera visita de cada cliente en TODO el historico = antiguedad real
        # (dato fiable, sacado de las ventas; no depende del ano de nacimiento).
        primera = vg_cli.groupby("cliente").fecha.min()
        # Visitas y facturacion de cada cliente DENTRO del periodo elegido.
        per = en_rango(vg_cli)
        per_cli = (per.assign(dia=per.fecha.dt.date)
                   .groupby("cliente")
                   .agg(visitas=("dia", "nunique"),
                        fact=("importe_kpi", "sum"))
                   .reset_index()) if not per.empty else per

        _periodo = (f"{G_D1.strftime('%d/%m/%Y')} – {G_D2.strftime('%d/%m/%Y')}"
                    if G_D1 is not None else "")

        def _tabla_fidelidad(res, col_grupo, titulo_col):
            """Tabla comun: clientes, %, visitas medias y % facturacion por grupo."""
            _tc = int(res.clientes.sum())
            _tf = float(res.fact.sum())
            res = res.copy()
            res["pct_cli"] = res.clientes / _tc * 100 if _tc else 0.0
            res["pct_fact"] = res.fact / _tf * 100 if _tf else 0.0
            tbl = pd.DataFrame({
                titulo_col: res[col_grupo].astype(str),
                "Clientes": res.clientes.map(lambda v: eur(v)),
                "% clientes": res.pct_cli.map(lambda v: f"{eur(v, 0)}%"),
                "Visitas medias": res.vis_media.map(lambda v: eur(v, 1)),
                "Facturacion": res.fact.map(lambda v: f"{eur(v)} €"),
                "% facturacion": res.pct_fact.map(lambda v: f"{eur(v, 0)}%"),
            })
            return tbl, res

        def _barras_fidelidad(res, col_grupo, orden, alto=280):
            """Barras horizontales de visitas medias por grupo."""
            f = px.bar(res, x="vis_media", y=col_grupo, orientation="h",
                       text=res.vis_media.map(lambda v: eur(v, 1)),
                       labels={"vis_media": "Visitas medias en el periodo",
                               col_grupo: ""})
            f.update_traces(marker_color=ACCENT, textposition="outside",
                            cliponaxis=False,
                            hovertemplate="%{y}: %{x:.1f} visitas de media"
                                          "<extra></extra>")
            f.update_yaxes(categoryorder="array", categoryarray=orden[::-1])
            _mx = res.vis_media.max()
            f.update_xaxes(range=[0, (_mx if _mx == _mx else 1) * 1.25])
            return f

        st.divider()
        ancla("cli-antig")
        st.markdown("### Fidelización por antigüedad del cliente")
        st.caption("Clientes agrupados por cuándo vinieron por PRIMERA vez a la "
                   "peluquería (dato fiable de las ventas). Mide cómo de fieles han "
                   "sido en el periodo del filtro global de arriba"
                   + (f" ({_periodo})" if _periodo else "") + ".")

        if per.empty:
            st.info("No hay ventas en el periodo elegido arriba.")
        else:
            pca = per_cli.copy()
            pca["antig_anios"] = ((pd.Timestamp(G_D2) - pca.cliente.map(primera))
                                  .dt.days / 365.25)
            binsA = [-0.001, 1, 3, float("inf")]
            lblA = ["Nuevos (menos de 1 año)", "De 1 a 3 años", "Más de 3 años"]
            pca["grupo"] = pd.cut(pca.antig_anios, bins=binsA, labels=lblA)
            resA = (pca.groupby("grupo", observed=False)
                    .agg(clientes=("cliente", "nunique"),
                         vis_media=("visitas", "mean"),
                         fact=("fact", "sum"))
                    .reindex(lblA).reset_index().fillna(0))
            tblA, resA = _tabla_fidelidad(resA, "grupo", "Antigüedad")
            cA1, cA2 = st.columns([3, 2])
            cA1.plotly_chart(_barras_fidelidad(resA, "grupo", lblA), width="stretch")
            cA2.dataframe(tblA, width="stretch", hide_index=True)
            _vet = resA[resA.grupo == "Más de 3 años"]
            _nue = resA[resA.grupo == "Nuevos (menos de 1 año)"]
            if len(_vet) and len(_nue) and _nue.vis_media.iloc[0] > 0:
                _r = _vet.vis_media.iloc[0] / _nue.vis_media.iloc[0]
                st.caption(f"Los clientes de más de 3 años vienen de media "
                           f"**{eur(_r, 1)} veces** lo que un cliente nuevo en este "
                           f"periodo. Cuanto más alto, más fiel es la clientela "
                           f"antigua.")

            st.divider()
            ancla("cli-freq")
            st.markdown("### Fidelización por frecuencia de visita")
            st.caption("Clientes repartidos según CUÁNTAS veces vinieron en el periodo "
                       "del filtro global" + (f" ({_periodo})" if _periodo else "")
                       + ". La columna de % de facturación enseña cuánto dinero "
                       "aporta cada grupo: normalmente pocos clientes muy fieles "
                       "sostienen la mayor parte del negocio.")
            binsF = [0, 1, 3, 9, float("inf")]
            lblF = ["Puntual (1 visita)", "Ocasional (2-3)",
                    "Fiel (4-9)", "Muy fiel (10+)"]
            pcf = per_cli.copy()
            pcf["grupo"] = pd.cut(pcf.visitas, bins=binsF, labels=lblF)
            resF = (pcf.groupby("grupo", observed=False)
                    .agg(clientes=("cliente", "nunique"),
                         vis_media=("visitas", "mean"),
                         fact=("fact", "sum"))
                    .reindex(lblF).reset_index().fillna(0))
            tblF, resF = _tabla_fidelidad(resF, "grupo", "Frecuencia")
            _pf = resF[resF.grupo.isin(["Fiel (4-9)", "Muy fiel (10+)"])]
            cF1, cF2 = st.columns([2, 3])
            figF = px.pie(resF, names="grupo", values="fact", hole=.55,
                          color="grupo",
                          color_discrete_sequence=PALETA_ANIOS[:len(lblF)])
            figF.update_traces(textinfo="percent",
                               hovertemplate="%{label}<br>%{value:.0f} € "
                                             "(%{percent})<extra></extra>")
            figF.update_layout(legend_title_text="",
                               title="Reparto de la facturación")
            cF1.plotly_chart(estilo_fig(figF, 300), width="stretch")
            cF2.dataframe(tblF, width="stretch", hide_index=True)
            if len(_pf):
                st.caption(f"Los clientes fieles (4 o más visitas) son "
                           f"**{eur(_pf.pct_cli.sum(), 0)}%** de la clientela pero "
                           f"aportan **{eur(_pf.pct_fact.sum(), 0)}%** de la "
                           f"facturación del periodo.")

        # ---- Clientes nuevos y retencion (todo el historico) ----
        st.divider()
        ancla("cli-nuevos")
        st.markdown("### Clientes nuevos y retención")
        st.caption("Cuántos clientes NUEVOS entran cada mes (su primera visita) y qué % "
                   "de ellos vuelve al menos una vez más. Mide si el salón atrae gente y "
                   "si la fideliza. Usa TODO el histórico (no el filtro de fechas).")
        _prim = (vg_cli.assign(_dia=vg_cli.fecha.dt.date).groupby("cliente")
                 .agg(prim=("fecha", "min"), ndias=("_dia", "nunique")))
        _prim["mes_alta"] = _prim.prim.dt.to_period("M").astype(str)
        _nuevos_mes = _prim.groupby("mes_alta").size().reset_index(name="nuevos")
        _corte = ventas_g.fecha.max() - pd.Timedelta(days=60)
        _elig = _prim[_prim.prim <= _corte]
        _ret = (int((_elig.ndias > 1).sum()) / len(_elig) * 100) if len(_elig) else 0
        _ult6 = _nuevos_mes.tail(6).nuevos.mean() if len(_nuevos_mes) else 0
        k1, k2, k3 = st.columns(3)
        k1.metric("Clientes nuevos (histórico)", eur(len(_prim)))
        k2.metric("Vuelven otra vez", f"{eur(_ret, 0)}%",
                  help="De los clientes cuya primera visita fue hace más de 2 meses, "
                       "qué % ha vuelto al menos una segunda vez.")
        k3.metric("Nuevos al mes (media últimos 6)", eur(_ult6, 1))
        _nm = _nuevos_mes.copy()
        _nm["lbl"] = _nm.mes_alta.map(mes_es)
        fig_n = px.bar(_nm, x="lbl", y="nuevos", text="nuevos",
                       labels={"lbl": "", "nuevos": "clientes nuevos"})
        fig_n.update_traces(marker_color=ACCENT, textposition="outside",
                            cliponaxis=False)
        fig_n.update_yaxes(range=[0, _nm.nuevos.max() * 1.18])
        eje_meses(fig_n, _nm.lbl)
        st.plotly_chart(estilo_fig(fig_n, 340), width="stretch")
        st.caption("Ojo: en los primeros meses del histórico casi todos figuran como "
                   "«nuevos» (no hay datos anteriores), así que fíjate sobre todo en la "
                   "tendencia reciente.")


# =========================================================================== #
# RECUPERAR CLIENTES
# =========================================================================== #
with tab_recup:
    st.subheader(f"Recuperar clientes · campaña -{DESC_CAMPANA}%")
    if clientes.empty or "ultima_visita" not in clientes.columns:
        st.info("Falta el fichero de Clientes con la columna 'Ult.Visita'. "
                "Cargalo en la pestaña Actualizar datos.")
    else:
        menu_lateral([
            ("rec-crit", "¿A quién avisamos?"),
            ("rec-pend", "Pendientes de avisar"),
            ("rec-color", "Color que no vuelve"),
        ])
        hoy = pd.Timestamp(dt.date.today())
        cl = clientes[clientes.ultima_visita.notna()].copy()
        cl = cl[cl.nombre.astype(str).str.strip().str.lower() != "generico"]
        cl["cod"] = cl["cod"].astype(str)
        # Fuera los clientes excluidos a mano (fallecidos, fichas duplicadas...).
        _excluidos = ingest.leer_excluidos_cods()
        if _excluidos:
            cl = cl[~cl.cod.isin(_excluidos)]
        # Ultima visita REAL = la mas reciente entre la ficha (Clientes.xls) y el
        # ultimo ticket de la Caja (cruzando por nombre). Asi, aunque el Excel de
        # Clientes este viejo, NO avisamos a quien si vino hace poco.
        def _nkey_r(s):
            return " ".join(genero._norm(s).split())
        # Último ticket y ritmo por cliente vienen ya CACHEADOS (solo se recalculan
        # al cambiar los datos), así pulsar botones no rehace este cálculo pesado.
        _agg = agg_cliente_ticket(st.session_state.data_version)
        _kcl = (cl.nombre.fillna("").astype(str) + " "
                + cl.apellidos.fillna("").astype(str)).map(_nkey_r)
        # Última visita REAL = la más reciente entre la ficha y el último ticket.
        _tk_date = _kcl.map(_agg["ult_ticket"]) if "ult_ticket" in _agg else pd.Series(
            index=cl.index, dtype="datetime64[ns]")
        _newer = _tk_date.notna() & (_tk_date > cl["ultima_visita"])
        cl.loc[_newer, "ultima_visita"] = _tk_date[_newer]
        cl["dias_sin_venir"] = (hoy - cl.ultima_visita).dt.days
        cl["anios_sin_venir"] = (cl.dias_sin_venir / 365).round(1)

        # ------ Ritmo propio de cada cliente (cada cuantos dias suele venir) ------
        cl["ritmo"] = _kcl.map(_agg["ritmo"]) if "ritmo" in _agg else pd.NA
        _rn = pd.to_numeric(cl["ritmo"], errors="coerce")
        cl["retraso_x"] = (cl.dias_sin_venir / _rn).round(2)
        cl["prox_esperada"] = cl.ultima_visita + pd.to_timedelta(_rn, unit="D")

        ancla("rec-crit")
        st.markdown("**¿A quién avisamos?** La lista **se actualiza sola**: cada mes "
                    "quien llevaba 3 meses pasa a 4, y quien vuelve a la pelu "
                    "desaparece.")
        modo = st.radio(
            "Cómo elegir",
            ["Por su propio ritmo (recomendado)", "Por riesgo de fuga (IA)",
             "Por tiempo sin venir"],
            horizontal=True, label_visibility="collapsed", key="rec_modo")

        def _sel_ritmo():
            """Selección por ritmo propio (también sirve de plan B para el modo IA)."""
            _rx = pd.to_numeric(cl["retraso_x"], errors="coerce")
            _con = cl[pd.to_numeric(cl["ritmo"], errors="coerce").notna()]
            _o = cl[(_rx >= 1.2) & (_rx <= 3)].copy()
            return _o, len(_con)

        if modo.startswith("Por riesgo de fuga"):
            sel_lbl = "riesgo de fuga"
            _cm = entrenar_churn(st.session_state.data_version) if _SKLEARN_OK else None
            if _cm is None:
                st.warning("El modo IA necesita la librería **scikit-learn** (y algo de "
                           "histórico). Si acabas de actualizar la app, vuelve a "
                           "ejecutar el instalador para instalarla. Mientras "
                           "tanto uso «su propio ritmo».")
                obj, _ncr = _sel_ritmo()
                banner_txt = ("{} clientes se han pasado de su ritmo habitual y aún son "
                              "recuperables (y sin avisar todavía)")
                obj_sort = "retraso_x"
            else:
                _kall = (cl.nombre.fillna("").astype(str) + " "
                         + cl.apellidos.fillna("").astype(str)).map(_nkey_r)
                cl["riesgo"] = pd.to_numeric(_kall.map(_cm["scores"]), errors="coerce")
                _nsc = int(cl["riesgo"].notna().sum())
                # Alto riesgo Y aún recuperable (última visita <= 24 meses): si alguien
                # se fue hace años el modelo le da ~100% de fuga, pero ya no es un buen
                # objetivo de campaña.
                obj = cl[(cl["riesgo"] >= 0.5)
                         & (cl["dias_sin_venir"] <= 730)].copy()
                _met = _cm["met"]
                _mt = (f" En validación temporal (sin hacer trampa con el futuro) "
                       f"acierta con **precisión {_met[0]*100:.0f}%** y "
                       f"**recall {_met[1]*100:.0f}%**." if _met else "")
                st.caption(
                    "Un modelo de **regresión logística** estima la **probabilidad de "
                    "que cada cliente no vuelva**, combinando recencia, ritmo, "
                    "frecuencia de visita, gasto y tendencia. Se **reentrena solo** con "
                    "tus datos. Aquí salen los de **riesgo ≥ 50 % que aún son "
                    f"recuperables** (última visita hace menos de 2 años): **{len(obj)}** "
                    f"de {_nsc} clientes con historial suficiente." + _mt)
                banner_txt = ("{} clientes con alto riesgo de fuga (modelo IA) y sin "
                              "avisar todavía")
                obj_sort = "riesgo"
        elif modo.startswith("Por su propio ritmo"):
            sel_lbl = "su ritmo"
            obj, _ncr = _sel_ritmo()
            st.caption(
                "Mira **cada cuánto viene cada persona** y la saca cuando se pasa de "
                "LO SUYO (entre 1,2 y 3 veces su ritmo: ya toca, pero aún es "
                "recuperable). Quien viene cada mes salta a las pocas semanas; quien "
                "viene una vez al año, no. Hacen falta al menos 3 visitas para conocer "
                f"su ritmo. Hoy hay **{len(obj)}** personas así (de "
                f"{_ncr} con ritmo conocido).")
            banner_txt = ("{} clientes se han pasado de su ritmo habitual y aún son "
                          "recuperables (y sin avisar todavía)")
            obj_sort = "retraso_x"
        else:
            # Rangos de tiempo sin venir (en dias). Se recalculan con la fecha de hoy.
            RANGOS = [
                ("1-3 meses", 30, 90),
                ("3-5 meses", 90, 150),
                ("5-7 meses", 150, 210),
                ("7-12 meses", 210, 365),
                ("1-2 años", 365, 730),
                ("+2 años", 730, 10 ** 9),
            ]
            _cnt = {n: int(((cl.dias_sin_venir >= a) & (cl.dias_sin_venir < b)).sum())
                    for n, a, b in RANGOS}
            st.caption("Cuántas personas hay en cada tramo hoy — "
                       + " · ".join(f"{n}: **{_cnt[n]}**" for n, _, _ in RANGOS))
            sel_lbl = st.segmented_control(
                "Tramo sin venir", [n for n, _, _ in RANGOS], default="3-5 meses",
                label_visibility="collapsed")
            if sel_lbl is None:
                sel_lbl = "3-5 meses"
            _ra, _rb = next((a, b) for n, a, b in RANGOS if n == sel_lbl)
            obj = cl[(cl.dias_sin_venir >= _ra) & (cl.dias_sin_venir < _rb)].copy()
            banner_txt = (f"{{}} clientes pendientes de enviar el mensaje del "
                          f"-{DESC_CAMPANA}% (llevan {sel_lbl} sin venir y sin avisar "
                          f"todavía)")
            obj_sort = "dias_sin_venir"

        # ¿A quien ya se le mando el mensaje? Un aviso "cuenta" solo si se envio
        # DESPUES de su ultima visita (si volvio despues, el aviso ya se consumio).
        avisos = ingest.leer_avisos()
        if not avisos.empty:
            avisos["fa_dt"] = pd.to_datetime(avisos.fecha_aviso, errors="coerce")
            ult_aviso = avisos.groupby("cod").fa_dt.max()
        else:
            ult_aviso = pd.Series(dtype="datetime64[ns]")

        obj["avisado"] = False
        if not obj.empty and not ult_aviso.empty:
            fa = obj["cod"].map(ult_aviso)
            obj["avisado"] = fa.notna() & (fa >= obj["ultima_visita"])

        pend = obj[~obj.avisado].sort_values(obj_sort, ascending=False)
        yav = obj[obj.avisado].sort_values(obj_sort, ascending=False)

        # ---- Banner AVISO ----
        if len(pend):
            st.markdown(
                f"<div class='aviso'>AVISO · {banner_txt.format(len(pend))}</div>",
                unsafe_allow_html=True)
        else:
            st.success("No hay clientes nuevos pendientes de avisar ahora mismo.")

        c1, c2, c3 = st.columns(3)
        c1.metric("Pendientes de avisar", eur(len(pend)))
        c2.metric("Ya avisados", eur(len(yav)))
        c3.metric(f"Total ({sel_lbl})", eur(len(obj)))

        # ---- Pendientes (envio MANUAL uno a uno por WhatsApp) ----
        ancla("rec-pend")
        st.markdown("### Pendientes de avisar")
        st.caption("Estos han cumplido el tiempo y AÚN no se les ha mandado el mensaje. "
                   "Escríbeles por WhatsApp uno a uno con el botón verde y, en cuanto "
                   "envíes el mensaje, pulsa el botón rojo **AVISADO/A** de esa persona "
                   "para marcarla (así ya no vuelve a salir). Si alguien NO debe "
                   "recibirlo (fallecido, ficha duplicada…), pulsa **Quitar**.")
        cols_m = [c for c in ["cod", "nombre", "apellidos", "movil", "email",
                              "ultima_visita", "anios_sin_venir"] if c in pend.columns]
        ren = {"cod": "Cod", "nombre": "Nombre", "apellidos": "Apellidos",
               "movil": "Movil", "email": "Email", "ultima_visita": "Ultima visita",
               "anios_sin_venir": "Años sin venir"}
        if pend.empty:
            st.success("No hay nadie pendiente ahora mismo. 🎉")
        else:
            st.download_button(
                "Descargar lista completa (Excel)",
                _to_xlsx(pend[cols_m].rename(columns=ren)),
                file_name=f"pendientes_{sel_lbl.replace(' ', '_').replace('+', 'mas')}.xlsx",
                mime=_XLSX_MIME)
            st.caption(f"{len(pend)} pendientes. Desliza dentro del recuadro para "
                       f"verlos todos (sin cambiar de página).")
            with st.container(height=560):
                for _, r in pend.iterrows():
                    cod = str(r["cod"])
                    nom = (f"{r.get('nombre', '')} "
                           f"{r.get('apellidos', '') or ''}").strip()
                    movil = str(r.get("movil", "") or "").strip()
                    anios = r.get("anios_sin_venir", "")
                    cA, cB, cC, cD = st.columns([3, 1.5, 1.2, 1])
                    _uv = r.get("ultima_visita")
                    _uvs = _uv.strftime("%d/%m/%Y") if pd.notna(_uv) else "—"
                    if modo.startswith("Por riesgo de fuga") \
                            and pd.notna(r.get("riesgo")):
                        _rit = (f" · viene cada {int(r['ritmo'])} días"
                                if pd.notna(r.get("ritmo")) else "")
                        cA.markdown(
                            f"**{nom}**  \n{movil or '— sin móvil —'} · "
                            f"**riesgo de fuga: {r['riesgo']*100:.0f}%** · última "
                            f"visita {_uvs}{_rit}")
                    elif modo.startswith("Por su propio ritmo") \
                            and pd.notna(r.get("ritmo")):
                        _prox = r.get("prox_esperada")
                        _prox_s = (_prox.strftime("%d/%m/%Y")
                                   if pd.notna(_prox) else "—")
                        _rxv = r.get("retraso_x")
                        cA.markdown(
                            f"**{nom}**  \n{movil or '— sin móvil —'} · viene cada "
                            f"**{int(r['ritmo'])} días** · última visita {_uvs} · "
                            f"tocaba el {_prox_s} (**{_rxv:.1f}× su ritmo**)")
                    else:
                        cA.markdown(f"**{nom}**  \n{movil or '— sin móvil —'} · última "
                                    f"visita: {_uvs} ({anios} años)")
                    _txt = (f"¡Hola {r.get('nombre', '')}! 💇‍♀️ Hace un tiempo que no "
                            f"te vemos por Salón Demo y te echamos de menos. Para darte la "
                            f"bienvenida de nuevo, tienes un {DESC_CAMPANA}% de "
                            f"descuento en tu próxima visita. ¿Te reservamos un "
                            f"hueco? ✨")
                    _url = wa_url(movil, _txt, web=True)
                    if _url:
                        cB.link_button("💬 WhatsApp", _url, width="stretch")
                    else:
                        cB.caption("sin móvil")
                    if cC.button("AVISADO/A", key=f"avisar_{cod}", width="stretch"):
                        ingest.registrar_avisos(pd.DataFrame([r]),
                                                hoy.strftime("%Y-%m-%d"))
                        st.toast(f"{nom}: marcado como avisado hoy.")
                        st.rerun()
                    if cD.button("Quitar", key=f"quitar_{cod}", width="stretch"):
                        ingest.excluir_clientes(
                            [{"cod": cod, "nombre": str(r.get("nombre", "")),
                              "motivo": ""}])
                        st.toast(f"{nom}: quitado de la campaña.")
                        st.rerun()

        # ---- Ya avisados ----
        with st.expander(f"Ver clientes ya avisados ({len(yav)})"):
            if len(yav):
                yav = yav.copy()
                yav["fecha_aviso"] = yav["cod"].map(ult_aviso).dt.date
                st.caption("Si marcaste a alguien por error, márcalo en «Deshacer» y "
                           "pulsa el botón: volverá a la lista de pendientes.")
                cols_y = [c for c in ["cod", "nombre", "apellidos", "movil",
                                      "ultima_visita", "fecha_aviso"] if c in yav.columns]
                _yv = yav[cols_y].rename(columns={
                    "cod": "Cod", "nombre": "Nombre", "apellidos": "Apellidos",
                    "movil": "Movil", "ultima_visita": "Ultima visita",
                    "fecha_aviso": "Avisado el"}).copy()
                _yv.insert(0, "Deshacer", False)
                ed_yav = st.data_editor(
                    _yv, width="stretch", hide_index=True, key="ed_yav",
                    disabled=[c for c in _yv.columns if c != "Deshacer"],
                    column_config={"Deshacer": st.column_config.CheckboxColumn(
                        "Deshacer", help="Quitar la marca de avisado.")})
                _des = ed_yav[ed_yav["Deshacer"] == True]  # noqa: E712
                if len(_des) and st.button(
                        f"Quitar la marca de avisado a {len(_des)} persona(s)"):
                    ingest.borrar_avisos([str(c) for c in _des["Cod"]])
                    st.success(f"{len(_des)} persona(s) vuelven a pendientes.")
                    st.rerun()
            else:
                st.caption("Todavia no has marcado a nadie como avisado.")

        # ---- Clientes de COLOR que no han vuelto a hacerselo ----
        ancla("rec-color")
        st.markdown("### Clientas que no repiten color")
        st.caption("Personas que se hicieron COLOR y hace mucho que no vuelven a "
                   "repetirlo. Lo normal es re-colorarse cada ~1 mes y el 90 % vuelve "
                   "antes de 3 meses; a partir del tiempo que elijas abajo ya es raro "
                   "que no hayan vuelto (posibles clientas perdidas a recuperar). Usa "
                   "TODO el histórico, no el filtro global de fechas.")
        if servicios.empty or "familia" not in servicios.columns:
            st.info("Falta el catálogo de Servicios con la columna 'familia' para saber "
                    "qué es color. Súbelo en Actualizar datos.")
        else:
            def _nkey(s):
                return " ".join(genero._norm(s).split())
            # familia de cada concepto segun el catalogo del TPV
            _fam = (servicios.assign(_n=servicios.nombre.str.strip().str.lower())
                    .dropna(subset=["familia"])
                    .assign(familia=lambda d: d.familia.str.strip().str.lower())
                    .drop_duplicates("_n").set_index("_n").familia)
            _vc = ventas_g.copy()                 # TODO el historico (color de siempre)
            _vc["_n"] = _vc.concepto_limpio.str.strip().str.lower()
            _col = _vc[(_vc._n.map(_fam) == "color") & _vc.cliente.notna()].copy()
            _col = _col[_col.cliente.astype(str).str.strip().str.lower()
                        != GENERICO.lower()]
            _col["_f"] = pd.to_datetime(_col.fecha, errors="coerce")
            _col = _col.dropna(subset=["_f"]).sort_values("_f")
            if _col.empty:
                st.info("No hay servicios de color registrados todavía.")
            else:
                _ult = _col.groupby("cliente").tail(1).set_index("cliente")
                _empb = _ult["empleado_norm"].map(EMPLEADOS_TODOS).fillna(
                    _ult["empleado_norm"])
                gcol = _col.groupby("cliente").agg(
                    ultimo=("_f", "max"),
                    veces=("_f", lambda s: s.dt.normalize().nunique())).reset_index()
                gcol["emp"] = gcol.cliente.map(_empb).fillna("")
                gcol["dias"] = (hoy - gcol.ultimo).dt.days
                # telefono + cod desde las fichas (cruce por nombre completo normalizado)
                _cf = clientes.copy()
                _cf["full"] = (_cf.nombre.fillna("").astype(str) + " "
                               + _cf.apellidos.fillna("").astype(str)).map(_nkey)
                _cf["cod"] = _cf["cod"].astype(str)
                _cf = _cf[_cf.full.str.strip() != ""].drop_duplicates("full").set_index("full")
                gcol["k"] = gcol.cliente.map(_nkey)
                gcol["movil"] = gcol.k.map(_cf["movil"]).fillna("") if "movil" in _cf else ""
                gcol["cod"] = gcol.k.map(_cf["cod"])
                if _excluidos:
                    gcol = gcol[~gcol.cod.isin(_excluidos)]

                st.markdown("**¿Cuánto tiempo sin repetir color para considerarlo "
                            "raro?**")
                OP_COLOR = {"3 meses": 3, "4 meses": 4, "6 meses": 6,
                            "9 meses": 9, "1 año": 12}
                _selc = st.segmented_control(
                    "Tiempo sin color", list(OP_COLOR.keys()), default="4 meses",
                    label_visibility="collapsed", key="color_seg")
                if _selc is None:
                    _selc = "4 meses"
                meses_x = OP_COLOR[_selc]
                solo_hab = st.checkbox("Solo habituales (2+ veces de color)",
                                        value=True, key="color_hab")
                over = gcol[gcol.dias > meses_x * 30].copy()
                if solo_hab:
                    over = over[over.veces >= 2]
                over = over.sort_values(["veces", "dias"], ascending=[False, False])
                if over.empty:
                    st.success("Ningún cliente de color pasa de ese tiempo. ¡Bien!")
                else:
                    over["meses"] = (over.dias / 30).round().astype(int)
                    m1, m2 = st.columns(2)
                    m1.metric("Clientes de color a recuperar", eur(len(over)))
                    m2.metric("Con teléfono",
                              eur(int((over.movil.str.strip() != "").sum())))
                    # Grafico: cuantos, segun cuanto hace de su ultimo color
                    _orden = ["Menos de 6 meses", "6-12 meses", "1-2 años", "Más de 2 años"]
                    _tr = pd.cut(over.dias / 30, bins=[0, 6, 12, 24, 100000],
                                 labels=_orden)
                    _cnt = (_tr.value_counts().reindex(_orden).fillna(0)
                            .astype(int).reset_index())
                    _cnt.columns = ["tramo", "n"]
                    figc = px.bar(_cnt, x="tramo", y="n", text="n",
                                  labels={"tramo": "Tiempo desde el último color",
                                          "n": "Clientes"})
                    figc.update_traces(marker_color=ACCENT, textposition="outside",
                                       cliponaxis=False)
                    figc.update_yaxes(range=[0, max(1, _cnt.n.max()) * 1.18])
                    st.plotly_chart(estilo_fig(figc, 300), width="stretch")
                    # Lista accionable (con telefono) para escribirles
                    _show = pd.DataFrame({
                        "Cliente": over.cliente.values,
                        "Último color": over.ultimo.dt.date.values,
                        "Hace (meses)": over.meses.values,
                        "Veces de color": over.veces.values,
                        "Empleada (última)": over.emp.values,
                        "Móvil": over.movil.values})
                    st.dataframe(_show, width="stretch", hide_index=True)
                    st.download_button(
                        "Descargar lista (Excel) para WhatsApp",
                        _to_xlsx(_show),
                        file_name=f"color_no_vuelve_{meses_x}meses.xlsx", mime=_XLSX_MIME)
                    st.caption("Ordenados por quién se lo hacía más veces (mejores para "
                               "recuperar). Mensaje sugerido: «Hace tiempo que no te "
                               "retocas el color con nosotros, ¿te reservamos cita? Te "
                               "cuidamos el tono como te gusta.»")

        # ---- Personas excluidas de la campaña (restaurables) ----
        _exc = ingest.leer_excluidos()
        with st.expander(f"Personas excluidas de la campaña ({len(_exc)})"):
            if _exc.empty:
                st.caption("No has excluido a nadie. Los que marques con «Quitar» "
                           "arriba aparecerán aquí por si te equivocas.")
            else:
                st.caption("Estas personas no reciben mensajes de la campaña. Marca "
                           "«Restaurar» y pulsa el botón para volver a incluirlas.")
                _ex = _exc.rename(columns={"cod": "Cod", "nombre": "Nombre",
                                           "fecha": "Excluido el"}).copy()
                _ex = _ex[["Cod", "Nombre", "Excluido el"]]
                _ex.insert(0, "Restaurar", False)
                ed_exc = st.data_editor(
                    _ex, width="stretch", hide_index=True, key="ed_exc",
                    disabled=["Cod", "Nombre", "Excluido el"],
                    column_config={"Restaurar": st.column_config.CheckboxColumn(
                        "Restaurar")})
                _rest = ed_exc[ed_exc["Restaurar"] == True]  # noqa: E712
                if len(_rest) and st.button(
                        f"Restaurar {len(_rest)} persona(s)"):
                    ingest.restaurar_excluidos([str(c) for c in _rest["Cod"]])
                    st.success(f"{len(_rest)} persona(s) restaurada(s) a la campaña.")
                    st.rerun()


# =========================================================================== #
# CUMPLEAÑOS DEL MES (felicitar por WhatsApp) — se actualiza solo cada mes
# =========================================================================== #
with tab_cumple:
    st.subheader("Cumpleaños del mes")
    if clientes.empty or "cumpleanos" not in clientes.columns:
        st.info("Falta el fichero de Clientes con la fecha de cumpleaños. Cárgalo en "
                "la pestaña Actualizar datos.")
    else:
        menu_lateral([
            ("cum-mes", "Cumpleaños del mes"),
            ("cum-felic", "Ya felicitados"),
        ])
        _hoy_c = dt.date.today()
        _mes_c = _hoy_c.month
        _anio_c = _hoy_c.year
        _mes_nom = _MESES_ES[_mes_c - 1]
        ancla("cum-mes")
        st.caption(f"Personas que cumplen años en **{_mes_nom}** y que han venido a la "
                   f"pelu **al menos una vez en los últimos 3 años** (los que hace más "
                   f"de 3 años que no vienen no salen). La lista se actualiza "
                   f"sola: el día 1 de cada mes pasa a mostrar los del mes nuevo (el 1 "
                   f"de octubre saldrán los de octubre). Escríbeles felicitando por "
                   f"WhatsApp y pulsa el botón rojo **FELICITADO/A**. Si alguien no debe "
                   f"recibirlo, pulsa **Quitar**.")
        st.markdown(f"🎁 **Descuento de cumpleaños activo: {DESC_CUMPLE}%** "
                    f"· se cambia en Ajustes → «Descuento de cumpleaños».")
        cb = clientes.copy()
        cb = cb[cb.nombre.astype(str).str.strip().str.lower() != "generico"]
        cb["cod"] = cb["cod"].astype(str)
        _exc_c = ingest.leer_excluidos_cods()
        if _exc_c:
            cb = cb[~cb.cod.isin(_exc_c)]
        cb["_cump"] = pd.to_datetime(cb.cumpleanos, errors="coerce")
        cb = cb.dropna(subset=["_cump"])
        cb = cb[cb._cump.dt.month == _mes_c].copy()
        # Solo quienes han venido a la pelu al menos UNA vez en los ultimos 3 años.
        # Ultima visita = la mas reciente entre la ficha y el ultimo ticket (Caja).
        _lim3 = pd.Timestamp(_hoy_c) - pd.DateOffset(years=3)
        _uv3 = pd.to_datetime(cb["ultima_visita"], errors="coerce")
        # Último ticket por cliente: reutiliza la MISMA tabla cacheada de Recuperar.
        _agg_c = agg_cliente_ticket(st.session_state.data_version)
        if "ult_ticket" in _agg_c and not _agg_c.empty:
            def _nk3(s):
                return " ".join(genero._norm(s).split())
            _k3 = (cb.nombre.fillna("").astype(str) + " "
                   + cb.apellidos.fillna("").astype(str)).map(_nk3)
            _td3 = _k3.map(_agg_c["ult_ticket"])
            _uv3 = _uv3.where(~(_td3.notna() & (_td3 > _uv3)), _td3)
        cb = cb[_uv3 >= _lim3].copy()
        cb["_dia"] = cb._cump.dt.day.astype(int)
        cb = cb.sort_values("_dia")
        _felic = ingest.leer_felicitados(_anio_c)
        cb["felic"] = cb.cod.isin(_felic)
        pend_c = cb[~cb.felic]
        yav_c = cb[cb.felic]

        if cb.empty:
            st.info(f"Nadie cumple años en {_mes_nom} (según las fichas de clientes).")
        else:
            c1, c2 = st.columns(2)
            c1.metric(f"Cumpleaños en {_mes_nom}", eur(len(cb)))
            c2.metric("Pendientes de felicitar", eur(len(pend_c)))
            if pend_c.empty:
                st.success("¡Ya has felicitado a todos los de este mes! 🎉")
            else:
                st.caption(f"{len(pend_c)} pendientes. Desliza dentro del recuadro para "
                           f"verlos y felicitarlos todos (sin cambiar de página).")
                with st.container(height=560):
                    for _, r in pend_c.iterrows():
                        cod = str(r["cod"])
                        nom = (f"{r.get('nombre', '')} "
                               f"{r.get('apellidos', '') or ''}").strip()
                        movil = str(r.get("movil", "") or "").strip()
                        dia = int(r["_dia"])
                        cA, cB, cC, cD = st.columns([3, 1.5, 1.4, 1])
                        cA.markdown(f"**{nom}**  \nCumple el **{dia}** de "
                                    f"{_mes_nom.lower()} · {movil or '— sin móvil —'}")
                        _txt = (f"¡Hola {r.get('nombre', '')}! 😊\n"
                                f"Próximamente será tu cumpleaños🎂🎉\n"
                                f"Recordarte que durante este mes podrás disfrutar de un "
                                f"descuento del {DESC_CUMPLE}% en Salón Demo "
                                f"✨.")
                        _url = wa_url(movil, _txt, web=True)
                        if _url:
                            cB.link_button("💬 WhatsApp", _url, width="stretch")
                        else:
                            cB.caption("sin móvil")
                        if cC.button("FELICITADO/A", key=f"felic_{cod}",
                                     width="stretch"):
                            ingest.registrar_felicitacion(cod, _anio_c,
                                                           _hoy_c.strftime("%Y-%m-%d"))
                            st.toast(f"{nom}: felicitado/a.")
                            st.rerun()
                        if cD.button("Quitar", key=f"quitarc_{cod}", width="stretch"):
                            ingest.excluir_clientes(
                                [{"cod": cod, "nombre": str(r.get("nombre", "")),
                                  "motivo": ""}])
                            st.toast(f"{nom}: quitado.")
                            st.rerun()

            ancla("cum-felic")
            with st.expander(f"Ver ya felicitados este mes ({len(yav_c)})"):
                if len(yav_c):
                    st.caption("Si marcaste a alguien por error, márcalo en «Deshacer» "
                               "y pulsa el botón: vuelve a pendientes.")
                    _yc = yav_c[["cod", "nombre", "apellidos", "movil", "_dia"]].rename(
                        columns={"cod": "Cod", "nombre": "Nombre",
                                 "apellidos": "Apellidos", "movil": "Movil",
                                 "_dia": "Día"}).copy()
                    _yc.insert(0, "Deshacer", False)
                    ed_yc = st.data_editor(
                        _yc, width="stretch", hide_index=True, key="ed_yc",
                        disabled=[c for c in _yc.columns if c != "Deshacer"],
                        column_config={"Deshacer": st.column_config.CheckboxColumn(
                            "Deshacer")})
                    _desc = ed_yc[ed_yc["Deshacer"] == True]  # noqa: E712
                    if len(_desc) and st.button(
                            f"Deshacer {len(_desc)} felicitación(es)"):
                        for _c in _desc["Cod"]:
                            ingest.borrar_felicitacion(str(_c), _anio_c)
                        st.success(f"{len(_desc)} vuelven a pendientes.")
                        st.rerun()
                else:
                    st.caption("Aún no has felicitado a nadie este mes.")


# =========================================================================== #
# EXCLUSIVOS (ingresos manuales sin IVA que suman a la facturacion total)
# =========================================================================== #
with tab_excl:
    st.subheader("Personas exclusivas")
    menu_lateral([
        ("exc-lista", "Lista del mes"),
        ("exc-resumen", "Resumen por mes"),
    ])
    st.caption("Apunta las visitas que se cobran SIN IVA y que no pasan por el TPV. "
               "Cada mes tiene su propia lista (solo el nombre y lo cobrado); el mes "
               "en el que estamos sale por defecto. El total del mes se ve debajo y se "
               "suma a la facturacion del Resumen.")
    ancla("exc-lista")

    # ---- Selector de mes (por defecto, el mes actual) --------------------
    hoy = pd.Timestamp(dt.date.today())
    mes_actual = hoy.to_period("M")
    ini_anio = pd.Period(dt.date(hoy.year, 1, 1), "M")
    if not exclusivos.empty and exclusivos.fecha_dt.notna().any():
        ini_anio = min(ini_anio, exclusivos.fecha_dt.min().to_period("M"))
    meses = list(pd.period_range(ini_anio, mes_actual, freq="M"))[::-1]
    mes_sel = st.selectbox("Mes", meses, index=0,
                           format_func=lambda p: mes_es(str(p)))

    # ---- Filas de ESE mes (editable, sin columna de fecha) ---------------
    if not exclusivos.empty and exclusivos.fecha_dt.notna().any():
        _mp = exclusivos.fecha_dt.dt.to_period("M")
        cur = exclusivos[_mp == mes_sel]
    else:
        cur = exclusivos.iloc[0:0]
    ed_df = (cur[["nombre", "importe"]].reset_index(drop=True)
             if not cur.empty else
             pd.DataFrame({"nombre": pd.Series([], dtype="object"),
                           "importe": pd.Series([], dtype="float")}))

    st.markdown(f"### Lista de {mes_es(str(mes_sel))}")
    st.caption("Anade filas con el boton +. Cuando termines, pulsa Guardar.")
    ed = st.data_editor(
        ed_df, num_rows="dynamic", width="stretch", hide_index=True,
        key=f"ed_excl_{mes_sel}",
        column_config={
            "nombre": st.column_config.TextColumn("Nombre", width="medium"),
            "importe": st.column_config.NumberColumn(
                "Cobrado (sin IVA) €", min_value=0.0, step=1.0, format="%.2f"),
        })

    tot_mes = float(pd.to_numeric(ed.importe, errors="coerce").fillna(0).sum())
    st.metric(f"Total de {mes_es(str(mes_sel))} (sin IVA)", f"{eur(tot_mes)} €")

    if st.button(f"Guardar lista de {mes_es(str(mes_sel))}", type="primary"):
        # Conservamos los otros meses y reemplazamos solo el mes elegido.
        if not exclusivos.empty:
            otros = exclusivos[exclusivos.fecha_dt.dt.to_period("M") != mes_sel]
            otros = otros[["fecha", "nombre", "importe"]]
        else:
            otros = pd.DataFrame(columns=["fecha", "nombre", "importe"])
        nuevas = ed.copy()
        nuevas["fecha"] = mes_sel.to_timestamp().date().isoformat()  # dia 1 del mes
        full = pd.concat([otros, nuevas[["fecha", "nombre", "importe"]]],
                         ignore_index=True)
        ingest.guardar_exclusivos(full)
        st.success(f"Guardada la lista de {mes_es(str(mes_sel))}.")
        st.rerun()

    # ---- Vision de conjunto de todos los meses ---------------------------
    if not exclusivos.empty and exclusivos.fecha_dt.notna().any():
        st.divider()
        ancla("exc-resumen")
        st.markdown("### Resumen por mes")
        d = exclusivos.dropna(subset=["fecha_dt"]).copy()
        d["_p"] = d.fecha_dt.dt.to_period("M")
        por_mes = (d.groupby("_p")
                   .agg(Visitas=("nombre", "count"), Total=("importe", "sum"))
                   .reset_index().sort_values("_p", ascending=False))
        por_mes["Mes"] = por_mes._p.astype(str).map(mes_es)
        st.dataframe(
            por_mes[["Mes", "Visitas", "Total"]]
            .style.format({"Total": lambda v: f"{eur(v)} €",
                           "Visitas": lambda v: eur(v)}),
            width="stretch", hide_index=True)
        st.caption(f"Total histórico (sin IVA): **{eur(float(d.importe.sum()))} €**.")

    st.info("Estos ingresos se guardan para que tengas el total real del negocio. "
            "Recuerda declararlos como el resto: en peluqueria el servicio lleva "
            "21% de IVA.")


# =========================================================================== #
# ACTUALIZAR DATOS
# =========================================================================== #
with tab_datos:
    st.subheader("Actualizar datos")
    menu_lateral([
        ("dat-subir", "1. Subir los Excel"),
        ("dat-descargar", "2. Cómo descargar"),
        ("dat-cadencia", "3. Qué subir y cuándo"),
        ("dat-estado", "Estado de la base"),
    ])
    st.markdown(
        "Sube aqui los Excel que exportas desde **TPV 123 peluqueros**. La **Caja** "
        "se acumula sin duplicar (subes el export completo y solo entra lo nuevo); "
        "**Clientes / Productos / Servicios** se refrescan con la ultima version."
    )

    ancla("dat-subir")
    st.markdown("### 1. Subir los Excel")
    subidos = st.file_uploader(
        "Sube los Excel exportados de 123 (puedes arrastrarlos o seleccionarlos)",
        type=["xls", "xlsx"], accept_multiple_files=True)

    if subidos and st.button("Procesar ficheros", type="primary"):
        resultados = []
        for f in subidos:
            try:
                res = ingest.cargar_fichero(f, f.name)
                res["nombre"] = f.name
                resultados.append(res)
            except Exception as e:  # noqa
                resultados.append({"nombre": f.name, "ok": False,
                                   "tipo": f"ERROR: {e}", "filas_fichero": 0,
                                   "filas_nuevas": 0})
        refrescar()
        st.success("Procesado terminado. Los indicadores ya reflejan los datos nuevos.")
        st.dataframe(pd.DataFrame(resultados)[
            ["nombre", "tipo", "filas_fichero", "filas_nuevas", "ok"]],
            width="stretch", hide_index=True)

    # ---- Guia: como sacar los Excel del TPV 123 -------------------------------
    ancla("dat-descargar")
    st.markdown("### 2. ¿Cómo descargar los Excel desde el TPV 123?")
    st.caption("Pasos orientativos por si al principio no sabéis dónde están los "
               "datos. Los nombres exactos de los menús pueden variar según la "
               "versión del programa; si alguno no coincide, buscad la opción "
               "parecida (normalmente Informes/Listados → fechas → Exportar a Excel).")

    PASOS_TPV = [
        ("Caja Diaria General (la más importante)", [
            "Abre el TPV 123 peluqueros.",
            "Entra en el menú de Informes / Listados (o Caja).",
            "Elige el informe 'Caja Diaria General'.",
            "Selecciona el rango de fechas del mes que quieres (o de todo el año).",
            "Pulsa Exportar / Excel y guarda el archivo .xls.",
        ]),
        ("Clientes", [
            "Ve al apartado de Clientes / Fichas de cliente.",
            "Abre el Listado de clientes.",
            "Pulsa Exportar / Excel para guardar el .xls (incluye la última visita).",
        ]),
        ("Productos", [
            "Ve al apartado de Artículos / Productos.",
            "Abre el Listado de productos (con precio y coste).",
            "Pulsa Exportar / Excel.",
        ]),
        ("Servicios", [
            "Ve al apartado de Servicios.",
            "Abre el Listado de servicios (con precios y familias).",
            "Pulsa Exportar / Excel.",
        ]),
    ]
    with st.expander("Ver los pasos aquí mismo"):
        for titulo, pasos in PASOS_TPV:
            st.markdown(f"**{titulo}**")
            st.markdown("\n".join(f"{i}. {p}" for i, p in enumerate(pasos, 1)))
        st.caption("Consejo: guarda los archivos siempre en la misma carpeta para "
                   "encontrarlos rápido cada mes.")

    _bloques = "".join(
        "<h2>{}</h2><ol>{}</ol>".format(
            t, "".join(f"<li>{p}</li>" for p in ps)) for t, ps in PASOS_TPV)
    guia_html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Guía: descargar los Excel del TPV 123</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;color:#3D372E;background:#F4EDDE;
max-width:760px;margin:24px auto;padding:0 24px;line-height:1.5}}
h1{{color:#A75D3B}} h2{{color:#A75D3B;margin-top:26px;font-size:1.15em}}
ol{{margin:6px 0 0}} li{{margin:4px 0}}
.nota{{background:#EAE0CC;padding:12px 16px;border-radius:8px;margin:18px 0}}
.tabla{{width:100%;border-collapse:collapse;margin-top:10px}}
.tabla th,.tabla td{{border:1px solid #d8cbb0;padding:8px;text-align:left;
font-size:.95em;vertical-align:top}} .tabla th{{background:#EAE0CC}}
</style></head><body>
<h1>Cómo descargar los Excel del TPV 123 peluqueros</h1>
<div class="nota">Pasos orientativos. Los nombres de los menús pueden variar según
la versión; si alguno no coincide, busca la opción parecida (por lo general
Informes/Listados → elegir fechas → Exportar a Excel).</div>
{_bloques}
<h2>¿Qué subir y cada cuánto?</h2>
<table class="tabla"><tr><th>Excel</th><th>Cada cuánto</th><th>Para qué</th></tr>
<tr><td>Caja Diaria General</td><td>Cada mes (imprescindible)</td>
<td>Facturación, tickets, servicios y ventas.</td></tr>
<tr><td>Clientes</td><td>De vez en cuando</td>
<td>Teléfono y cumpleaños de los clientes nuevos (la última visita ya se calcula sola con la Caja).</td></tr>
<tr><td>Productos</td><td>Solo cuando cambien</td><td>Precios y coste de tienda.</td></tr>
<tr><td>Servicios</td><td>Solo cuando cambien</td><td>Precios y familias.</td></tr>
</table>
<div class="nota">Después, en la app: pestaña <b>Actualizar datos</b> → subir los
archivos → <b>Procesar ficheros</b>. La Caja no se duplica aunque subas meses
repetidos. Este archivo se puede imprimir o guardar como PDF (Ctrl+P →
Guardar como PDF).</div>
</body></html>"""
    st.download_button(
        "Descargar la guía (para imprimir o guardar como PDF)",
        data=guia_html.encode("utf-8"),
        file_name="guia_descargar_excel_tpv123.html", mime="text/html")

    # ---- Cadencia recomendada -------------------------------------------------
    ancla("dat-cadencia")
    st.markdown("### 3. ¿Qué conviene subir y cada cuánto?")
    guia = pd.DataFrame([
        {"Excel del TPV": "Caja Diaria General",
         "Cada cuánto": "CADA MES (imprescindible)",
         "Para qué": "Facturacion, tickets, servicios y ventas del mes. Es el que "
                     "alimenta casi todo. Sube el rango del mes nuevo (o el ano "
                     "entero, da igual: no se duplica)."},
        {"Excel del TPV": "Clientes",
         "Cada cuánto": "De vez en cuando (cada 2-3 meses)",
         "Para qué": "Trae el TELEFONO y el CUMPLEAÑOS de los clientes NUEVOS (los "
                     "tickets no los tienen). La 'ultima visita' ya NO depende de "
                     "este Excel: se calcula sola con la Caja, siempre al dia."},
        {"Excel del TPV": "Productos",
         "Cada cuánto": "Solo cuando cambien",
         "Para qué": "Catalogo de productos de tienda (precio y coste). Resubelo si "
                     "suben precios o entran/salen articulos."},
        {"Excel del TPV": "Servicios",
         "Cada cuánto": "Solo cuando cambien",
         "Para qué": "Catalogo de servicios (precios y familias). Resubelo si cambian "
                     "precios o se anaden servicios."},
    ])
    st.table(guia)
    st.caption("Regla facil: **cada mes solo la Caja**. El de **Clientes**, de vez en "
               "cuando (para telefono y cumpleaños de los clientes nuevos). "
               "**Productos y Servicios** solo cuando cambien precios o el catalogo. "
               "Las personas exclusivas (sin IVA) van en su pestana, no en estos Excel.")
    if hay_datos:
        _hasta = ventas_g.fecha.max()
        _ph, _pa = _hasta.to_period("M"), pd.Timestamp(dt.date.today()).to_period("M")
        _pend = (_pa.year - _ph.year) * 12 + (_pa.month - _ph.month)
        _msg = f"Ahora mismo hay datos de Caja hasta el **{_hasta.strftime('%d/%m/%Y')}**."
        if _pend >= 1:
            _msg += (f" Parece que falta subir la Caja de **{_pend} mes(es)** mas "
                     f"reciente(s).")
        st.info(_msg)

    st.markdown("---")
    ancla("dat-estado")
    st.markdown("### Estado actual de la base de datos")
    filas = {"Ventas (caja)": len(ventas), "Clientes": len(clientes),
             "Productos": len(productos), "Servicios": len(servicios)}
    st.table(pd.DataFrame(filas.items(), columns=["Tabla", "Filas"]))

    with st.expander("Vaciar la base de datos (empezar de cero)"):
        st.caption("Borra ventas, clientes y catalogos. NO borra sueldos/horas ni "
                   "los avisos de campaña.")
        if st.button("Borrar todos los datos"):
            for _t in ["ventas", "clientes", "productos", "servicios"]:
                with ingest.get_conn() as _con:
                    _con.execute(f"DROP TABLE IF EXISTS {_t}")
                    _con.commit()
            refrescar()
            st.rerun()


# =========================================================================== #
# AJUSTES
# =========================================================================== #
with tab_ajustes:
    st.subheader("Ajustes")
    menu_lateral([
        ("aj-sueldos", "Sueldos y horas"),
        ("aj-com", "Comisiones por mes"),
        ("aj-empl", "Altas y bajas"),
        ("aj-horario", "Horario de apertura"),
        ("aj-pesos", "Pesos de servicios"),
        ("aj-desc", "Descuento campaña"),
        ("aj-desc-cumple", "Descuento cumpleaños"),
        ("aj-pass", "Contraseña"),
    ])
    st.caption("Datos que NO vienen del TPV y que puedes cambiar aquí. Se guardan en "
               "la base de datos y se aplican al instante en toda la app.")

    ancla("aj-sueldos")
    st.markdown("### Sueldos y horas por empleado (con vigencia)")
    st.caption("Sueldo **en sucio mensual** y **horas por semana**, **con fecha de "
               "vigencia**: cada fila vale DESDE su fecha. Si a alguien le cambia el "
               "sueldo o las horas, **NO borres la fila antigua**: añade una fila nueva "
               "(botón +) con esa persona, la **fecha del cambio** y los valores nuevos. "
               "Así los meses pasados siguen usando lo que valía entonces. Deja las "
               "horas vacías si no se saben (como Carlos): entonces no se calcula su "
               "facturación por hora.")
    _ph = plantilla_hist[["empleado", "desde", "sueldo", "horas_sem"]].copy()
    _ph["desde"] = pd.to_datetime(_ph.desde, errors="coerce")
    _ren_p = {"empleado": "Empleado", "desde": "Desde",
              "sueldo": "Sueldo (€/mes)", "horas_sem": "Horas/semana"}
    edit_ph = st.data_editor(
        _ph.rename(columns=_ren_p), num_rows="dynamic", width="stretch",
        hide_index=True, key="editor_plantilla_hist",
        column_config={
            "Empleado": st.column_config.SelectboxColumn(
                "Empleado", options=list(EMPLEADOS_ACTIVOS.values()), required=True),
            "Desde": st.column_config.DateColumn("Desde", format="DD/MM/YYYY"),
            "Sueldo (€/mes)": st.column_config.NumberColumn(
                "Sueldo (€/mes)", min_value=0.0, step=10.0, format="%.2f €"),
            "Horas/semana": st.column_config.NumberColumn(
                "Horas/semana", min_value=0.0, max_value=80.0, step=1.0,
                format="%.0f"),
        })
    if st.button("Guardar sueldos y horas", type="primary"):
        _inv_p = {v: k for k, v in _ren_p.items()}
        ingest.guardar_plantilla_hist(edit_ph.rename(columns=_inv_p))
        # Sincroniza el "valor actual" (ultima vigencia por empleado) para el resto.
        _hh = ingest.leer_plantilla_hist()
        _hh["desde_dt"] = pd.to_datetime(_hh.desde, errors="coerce")
        _act = {}
        for _e, _g in (_hh.dropna(subset=["desde_dt"])
                       .sort_values("desde_dt").groupby("empleado")):
            _r = _g.iloc[-1]
            _act[_e] = {"sueldo": float(_r.sueldo or 0),
                        "horas_sem": (None if pd.isna(_r.horas_sem)
                                      else float(_r.horas_sem))}
        if _act:
            ingest.guardar_plantilla(_act)
        st.success("Guardado. La rentabilidad y la facturación por hora usan cada "
                   "periodo el valor vigente.")
        st.rerun()

    # ---- Horario de apertura (con fecha de vigencia) ----
    st.markdown("---")
    ancla("aj-com")
    st.markdown("### Comisiones por mes")
    st.caption("Cambian cada mes, así que se meten a mano por partes: **Individual**, "
               "**General** y **Productos** (en euros y YA SIN IVA). Las **horas extra** "
               "se ponen en NÚMERO de horas y la app las multiplica por el precio/hora "
               "de abajo. El total es lo que se resta en la Rentabilidad. Recordatorio: "
               "en individual y general se llevan el 10% de lo que superen su objetivo, "
               "menos IVA.")

    # Precio de la hora extra (editable; puede cambiar en el futuro).
    _precio_hora = float(ingest.leer_ajuste("precio_hora_extra", "13.50") or 13.50)
    cph1, cph2 = st.columns([1, 2])
    _nuevo_precio = cph1.number_input("Precio de la hora extra (€)", min_value=0.0,
                                      value=_precio_hora, step=0.5, format="%.2f")
    if cph2.button("Guardar precio hora"):
        ingest.guardar_ajuste("precio_hora_extra", _nuevo_precio)
        st.success(f"Precio de la hora extra guardado: {eur(_nuevo_precio, 2)} €.")
        st.rerun()
    _precio_hora = _nuevo_precio  # usa el valor del cuadro para el calculo en vivo

    if hay_datos and not ventas_emp.empty:
        meses_all = sorted(ventas_g.mes.unique())
        mes_com = st.selectbox("Mes", list(reversed(meses_all)), format_func=mes_es,
                               key="mes_comision")
        com_det = ingest.leer_comisiones_detalle(mes_com)

        def _cd(n, k):
            return float(com_det.get(n, {}).get(k, 0) or 0)

        df_com = pd.DataFrame([
            {"Empleado": n,
             "Individual (€)": _cd(n, "individual"),
             "General (€)": _cd(n, "general"),
             "Productos (€)": _cd(n, "productos"),
             "Horas extra (nº)": _cd(n, "horas_extra"),
             "Notas": str(com_det.get(n, {}).get("notas", "") or "")}
            for n in EMPLEADOS_ACTIVOS.values()])
        _numcol = lambda t: st.column_config.NumberColumn(
            t, format="%.2f €", min_value=0.0, step=0.01)
        edit_com = st.data_editor(
            df_com, hide_index=True, width="stretch", key="editor_comisiones",
            disabled=["Empleado"],
            column_config={
                "Individual (€)": _numcol("Individual (€)"),
                "General (€)": _numcol("General (€)"),
                "Productos (€)": _numcol("Productos (€)"),
                "Horas extra (nº)": st.column_config.NumberColumn(
                    "Horas extra (nº)", min_value=0.0, step=0.5, format="%.1f",
                    help=f"Número de horas. Se pagan a {eur(_precio_hora, 2)} €/h."),
                "Notas": st.column_config.TextColumn(
                    "Notas", help="Solo informativo."),
            })

        _tot = edit_com.copy()
        for _c in ["Individual (€)", "General (€)", "Productos (€)", "Horas extra (nº)"]:
            _tot[_c] = pd.to_numeric(_tot[_c], errors="coerce").fillna(0)
        _tot["Extra (€)"] = _tot["Horas extra (nº)"] * _precio_hora
        _tot["Total (€)"] = (_tot["Individual (€)"] + _tot["General (€)"]
                             + _tot["Productos (€)"] + _tot["Extra (€)"])
        st.dataframe(
            _tot[["Empleado", "Horas extra (nº)", "Extra (€)", "Total (€)"]]
            .style.format({"Extra (€)": lambda v: f"{eur(v, 2)} €",
                           "Total (€)": lambda v: f"{eur(v, 2)} €",
                           "Horas extra (nº)": lambda v: eur(v, 1)}),
            width="stretch", hide_index=True)
        st.caption(f"Total de comisiones de {mes_es(mes_com)}: "
                   f"**{eur(float(_tot['Total (€)'].sum()), 2)} €** "
                   f"(horas extra a {eur(_precio_hora, 2)} €/h).")

        if st.button("Guardar comisiones", type="primary"):
            dic = {}
            for _, r in edit_com.iterrows():
                _h = pd.to_numeric(r["Horas extra (nº)"], errors="coerce")
                _h = 0.0 if pd.isna(_h) else float(_h)
                dic[r["Empleado"]] = {
                    "individual": r["Individual (€)"], "general": r["General (€)"],
                    "productos": r["Productos (€)"],
                    "extra": _h * _precio_hora,   # euros de las horas extra
                    "horas_extra": _h, "notas": r["Notas"]}
            ingest.guardar_comisiones(mes_com, dic)
            st.success(f"Comisiones de {mes_es(mes_com)} guardadas.")
            st.rerun()
    else:
        st.info("Sube datos primero para poder elegir el mes.")

    # ---- Pesos de los servicios ----
    st.markdown("---")
    ancla("aj-empl")
    st.markdown("### Altas y bajas de empleados")
    st.caption("Marca quién trabaja actualmente y, opcionalmente, la **fecha de alta** "
               "(cuándo entró) y **de baja** (cuándo se fue). Reglas: si pones una "
               "**fecha de baja pasada**, deja de contar como activo automáticamente; "
               "aun así **seguirá saliendo en los periodos del histórico en que sí "
               "trabajó** (sus ventas de entonces no se pierden). Al desmarcar «Activo» "
               "sin fecha, se le trata como baja inmediata.")
    emp_cfg = ingest.leer_empleados(EMPLEADOS_DEFECTO)
    cfg_by_tpv = {e["nombre_tpv"]: e for e in emp_cfg}
    nombres_datos = sorted(ventas.empleado_norm.dropna().unique()) if hay_datos else []
    todos_tpv = sorted(set(nombres_datos) | set(cfg_by_tpv))

    def _fecha_col(t, campo):
        v = cfg_by_tpv.get(t, {}).get(campo, "")
        return pd.to_datetime(v, errors="coerce") if v else pd.NaT

    df_emp = pd.DataFrame([
        {"Nombre en el TPV": t,
         "Activo": bool(cfg_by_tpv.get(t, {}).get("activo", False)),
         "Fecha alta": _fecha_col(t, "fecha_alta"),
         "Fecha baja": _fecha_col(t, "fecha_baja")}
        for t in todos_tpv])
    edit_emp = st.data_editor(
        df_emp, hide_index=True, width="stretch", key="editor_empleados",
        disabled=["Nombre en el TPV"],
        column_config={
            "Activo": st.column_config.CheckboxColumn("Activo"),
            "Fecha alta": st.column_config.DateColumn("Fecha alta",
                                                      format="DD/MM/YYYY"),
            "Fecha baja": st.column_config.DateColumn("Fecha baja",
                                                      format="DD/MM/YYYY")})
    if st.button("Guardar empleados", type="primary"):
        def _f(v):
            return "" if pd.isna(v) else str(v)[:10]
        lista = [{"nombre_tpv": r["Nombre en el TPV"],
                  "nombre_bonito": cfg_by_tpv.get(r["Nombre en el TPV"], {}).get(
                      "nombre_bonito", r["Nombre en el TPV"]),
                  "activo": int(bool(r["Activo"])),
                  "fecha_alta": _f(r["Fecha alta"]),
                  "fecha_baja": _f(r["Fecha baja"])}
                 for _, r in edit_emp.iterrows()]
        ingest.guardar_empleados(lista)
        st.success("Plantilla actualizada.")
        st.rerun()

    with st.expander("Añadir un empleado nuevo (que aún no aparece en la caja)"):
        st.caption("Para alguien recién contratado que todavía no ha trabajado. Escribe "
                   "su nombre tal y como saldrá en el TPV: **nombre + inicial del primer "
                   "apellido** (por ejemplo «Marta L»). En cuanto empiece a trabajar y "
                   "subas la caja, se enlazará solo con sus ventas.")
        nuevo_tpv = st.text_input("Nombre en el TPV (nombre + inicial apellido)",
                                  key="nuevo_tpv")
        if st.button("Añadir empleado", type="primary"):
            if nuevo_tpv.strip():
                ingest.guardar_empleados([{
                    "nombre_tpv": nuevo_tpv.strip(),
                    "nombre_bonito": nuevo_tpv.strip(),
                    "activo": 1}])
                # tambien le creamos una fila de sueldo/horas vacia para que salga
                pl_actual = ingest.leer_plantilla(PLANTILLA_DEFECTO)
                nombre_disp = nuevo_tpv.strip()
                if nombre_disp not in pl_actual:
                    pl_actual[nombre_disp] = {"sueldo": 0.0, "horas_sem": None}
                    ingest.guardar_plantilla(pl_actual)
                st.success(f"Empleado «{nombre_disp}» añadido y activado. "
                           "Ponle su sueldo y horas arriba.")
                st.rerun()
            else:
                st.warning("Escribe al menos el nombre en el TPV.")

    # ---- Comisiones por mes ----
    st.markdown("---")
    ancla("aj-horario")
    st.markdown("### Horario de apertura (horas por día)")
    st.caption("Horas que abre la peluquería cada día de la semana. Se usa en la "
               "gráfica de «Días de la semana» del Resumen (facturación por hora). "
               "**Con fecha de vigencia**: cada fila vale DESDE su fecha. Si cambiáis "
               "el horario, NO borréis la fila antigua: añadid una fila nueva con la "
               "fecha del cambio y el horario nuevo. Así los periodos pasados siguen "
               "usando el horario que había entonces. Pon 0 en los días que cierra.")
    _hor_df = horarios_cfg[["desde"] + DIAS_KEYS].copy()
    _hor_df["desde"] = pd.to_datetime(_hor_df.desde, errors="coerce")
    _ren_h = {"desde": "Desde", "lun": "Lunes", "mar": "Martes", "mie": "Miércoles",
              "jue": "Jueves", "vie": "Viernes", "sab": "Sábado", "dom": "Domingo"}
    _hor_ed = st.data_editor(
        _hor_df.rename(columns=_ren_h), num_rows="dynamic", width="stretch",
        hide_index=True, key="editor_horario",
        column_config={
            "Desde": st.column_config.DateColumn("Desde", format="DD/MM/YYYY"),
            **{_ren_h[k]: st.column_config.NumberColumn(
                _ren_h[k], min_value=0.0, max_value=24.0, step=0.5, format="%.1f h")
               for k in DIAS_KEYS}})
    if st.button("Guardar horario", type="primary"):
        _inv = {v: k for k, v in _ren_h.items()}
        _save = _hor_ed.rename(columns=_inv)
        ingest.guardar_horarios(_save)
        st.success("Horario guardado. La gráfica de días de la semana ya lo usa.")
        st.rerun()

    st.markdown("---")
    ancla("aj-pesos")
    st.markdown("### Pesos de los servicios")
    st.caption("Cuánto pesa cada servicio para los «ítems ponderados» (un corte de "
               "30 min = 1). Edita los valores o añade filas nuevas con el signo +. "
               "El concepto va en minúsculas, tal cual aparece. Lo que no esté aquí "
               "pesa 1.")
    pes = ingest.leer_pesos(PESOS_DEFECTO)
    df_pes = pd.DataFrame([{"Concepto (minúsculas)": k, "Peso": v}
                           for k, v in pes.items()])
    edit_pes = st.data_editor(
        df_pes, hide_index=True, width="stretch", num_rows="dynamic",
        key="editor_pesos",
        column_config={"Peso": st.column_config.NumberColumn(
            format="%.2f", min_value=0, step=0.1)})
    if st.button("Guardar pesos", type="primary"):
        dic = {}
        for _, r in edit_pes.iterrows():
            c = str(r["Concepto (minúsculas)"] or "").strip().lower()
            if c and not pd.isna(r["Peso"]):
                dic[c] = float(r["Peso"])
        ingest.guardar_pesos(dic)
        st.success("Pesos guardados. Los ítems ponderados ya usan los nuevos valores.")
        st.rerun()
    st.markdown("---")
    ancla("aj-desc")
    st.markdown("### Descuento de la campaña de recuperación")
    st.caption("El % de descuento del mensaje a los clientes que hace tiempo que no vienen.")
    ca1, ca2 = st.columns([1, 3])
    nuevo_desc = ca1.number_input("Descuento (%)", min_value=0, max_value=100,
                                  value=DESC_CAMPANA, step=5)
    if ca1.button("Guardar descuento"):
        ingest.guardar_ajuste("descuento", int(nuevo_desc))
        st.success(f"Descuento cambiado a {int(nuevo_desc)}%.")
        st.rerun()

    st.markdown("---")
    ancla("aj-desc-cumple")
    st.markdown("### Descuento de cumpleaños")
    st.caption("El % de descuento que aparece en el mensaje de felicitación de la "
               "pestaña Cumpleaños. Cámbialo aquí cuando quieras.")
    cc1, cc2 = st.columns([1, 3])
    nuevo_desc_c = cc1.number_input("Descuento cumpleaños (%)", min_value=0,
                                    max_value=100, value=DESC_CUMPLE, step=5,
                                    key="desc_cumple_input")
    if cc1.button("Guardar descuento de cumpleaños"):
        ingest.guardar_ajuste("descuento_cumple", int(nuevo_desc_c))
        st.success(f"Descuento de cumpleaños cambiado a {int(nuevo_desc_c)}%.")
        st.rerun()

    # ---- Contraseña de acceso ----
    st.markdown("---")
    ancla("aj-pass")
    st.markdown("### Contraseña de acceso")
    st.caption("Si pones una contraseña, al abrir la app la pedirá para entrar. Es una "
               "protección básica de acceso. Se guarda **cifrada** (con hash), nunca en "
               "texto plano, así que no se puede «ver» ni recuperar: solo cambiarla.")
    cp1, cp2 = st.columns([1, 3])
    _pwd_guardada = str(ingest.leer_ajuste("password", "") or "")
    cp1.caption("🔒 Ahora mismo **hay** una contraseña puesta."
                if _pwd_guardada else "🔓 Ahora mismo **no** hay contraseña.")
    nueva_pwd = cp1.text_input("Nueva contraseña (escríbela para cambiarla)",
                               value="", type="password", key="set_pwd")
    if cp1.button("Guardar contraseña"):
        _np = nueva_pwd.strip()
        ingest.guardar_ajuste("password", _hash_password(_np) if _np else "")
        st.success("Contraseña actualizada." if _np else "Se ha quitado la contraseña.")
        st.rerun()
    if cp1.button("Quitar contraseña"):
        ingest.guardar_ajuste("password", "")
        st.success("Se ha quitado la contraseña.")
        st.rerun()
    if cp1.button("Cerrar sesión ahora"):
        st.session_state.autenticado = False
        st.rerun()

    # ---- Altas / bajas de empleados ----
