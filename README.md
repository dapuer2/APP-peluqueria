# Salón BI — Dashboard de analítica para una peluquería

Aplicación de *Business Intelligence* local que convierte los tickets de un TPV
de peluquería en un panel de KPIs, campañas de fidelización y modelos
predictivos. Está construida con **Python + Streamlit + SQLite + pandas**, corre
en local (sin servidor ni coste de nube) y lee los datos que el TPV exporta a
Excel.

> ⚠️ **Privacidad.** Este repositorio contiene únicamente el **código**. No
> incluye ningún dato real: la marca, los nombres de personas y las cifras son
> ficticios, y la base de datos real nunca se publica. Para poder probarlo se
> incluye un **generador de datos sintéticos** (`generar_datos_demo.py`).

---

## 🚀 Puesta en marcha

```bash
pip install -r requirements.txt     # solo la primera vez
python generar_datos_demo.py        # crea data/peluqueria.db con datos inventados
streamlit run app.py                # abre http://localhost:8501
```

La app pide una contraseña opcional al entrar (por defecto no hay: pulsa
**Entrar**).

---

## ✨ Qué hace

- **Resumen ejecutivo:** facturación, tickets, carro medio, reparto
  servicios/productos, efectivo vs. tarjeta, días más fuertes por hora abierta.
- **Empleados:** productividad, carro medio, rentabilidad (margen = facturación
  − sueldo − comisiones) e informe descargable por empleado.
- **Servicios y productos:** ranking, beneficio por línea de producto, venta
  cruzada.
- **Clientes:** frecuencia de visita, fidelización por antigüedad y por
  frecuencia (RFM), captación y retención de nuevos.
- **Recuperar clientes** (3 modos): por umbral de tiempo, por el **ritmo propio
  de cada cliente**, o por un **modelo de riesgo de fuga (ML)**. Con envío
  manual por WhatsApp.
- **Cumpleaños** del mes con campaña de descuento.
- **Asistente** de consultas rápidas por mes (cálculos exactos, sin IA
  generativa).

<img width="1600" height="668" alt="image" src="https://github.com/user-attachments/assets/141650b7-a5b7-46e9-914a-23ae265db102" />
<img width="1600" height="664" alt="image" src="https://github.com/user-attachments/assets/bddcae70-0300-4f2a-9701-197868ec5b30" />
<img width="1600" height="773" alt="image" src="https://github.com/user-attachments/assets/ef8d4421-f45a-4226-bea9-6077e1c62d02" />
<img width="1358" height="840" alt="image" src="https://github.com/user-attachments/assets/154cd50a-abb7-4639-ad2a-1be4d633fa00" />
<img width="1600" height="578" alt="image" src="https://github.com/user-attachments/assets/a8e45a61-45a0-491a-82ad-cbe300b1e54b" />
<img width="1600" height="631" alt="image" src="https://github.com/user-attachments/assets/2ab9b5f1-5509-4add-b027-48a0e45c1f4f" />
<img width="1600" height="660" alt="image" src="https://github.com/user-attachments/assets/76664978-9141-4dc1-bbaf-354774a912ce" />

---

## 🧠 Ciencia de datos (lo interesante para un portfolio)

Todo el modelado se ha hecho con criterio metodológico, no como adorno:

- **Predicción de fuga de clientes (churn).** Regresión logística sobre variables
  RFM (recencia, frecuencia, ritmo, gasto, tendencia). **Validación temporal**
  (entrena en cortes pasados, evalúa en un corte posterior) para evitar *data
  leakage*. Se comparó contra una heurística de una sola variable y contra
  *gradient boosting*; se eligió la logística porque con el tamaño muestral el
  modelo complejo no aportaba (menos varianza, menos sobreajuste). El modelo se
  **reentrena solo** con los datos disponibles.
- **Previsión de facturación.** Modelo estacional con ajuste de tendencia,
  validado con ***backtesting* de origen deslizante** (nunca *k-fold* aleatorio
  en series temporales) y comparado con *baselines* ingenuos (MAE / MAPE / RMSE).
- **Inferencia de sexo por nombre** (clasificación por diccionario) para segmentar
  clientela, dejando explícito el porcentaje "desconocido" en lugar de forzar la
  etiqueta.
- **Ingeniería de datos:** resolución de entidades por nombre normalizado,
  reconstrucción del IVA cuando falta, deduplicación de tickets por *hash* de
  línea (permite re-subir el export completo cada mes).
- **Rendimiento:** las agregaciones pesadas por cliente se cachean por versión de
  datos (`st.cache_data`), así la interacción es fluida.

---

## 🛠️ Stack

`Python` · `Streamlit` · `SQLite` · `pandas` · `scikit-learn` · `plotly` ·
`openpyxl`

## 📁 Estructura

```
app.py                 Dashboard Streamlit (UI + analítica + modelos)
ingest.py              Parseo de los Excel del TPV + base de datos SQLite
genero.py              Inferencia de sexo por nombre (diccionario)
generar_datos_demo.py  Genera datos sintéticos para probar la app
requirements.txt
.streamlit/config.toml  Tema + servidor atado a localhost
```

## 🔒 Notas de privacidad y seguridad

- La base de datos (`data/`, `*.db`) y cualquier Excel están en `.gitignore`:
  **nunca** se versionan.
- La contraseña de acceso se guarda **hasheada** (PBKDF2-SHA256), no en texto
  plano.
- El servidor se ata a `localhost`, de modo que no queda expuesto a la red local.

## ⚖️ Licencia

MIT — ver [LICENSE](LICENSE).
