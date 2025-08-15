
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle

DB_PATH = "presupuestos.db"

def limpiar_precio(valor):
    if pd.isna(valor):
        return 0
    if isinstance(valor, str):
        valor = valor.replace(".", "").replace(",", ".")
    try:
        return int(round(float(valor)))
    except:
        return 0

st.set_page_config(page_title="Presupuestos & Costos", layout="wide")

@st.cache_data
def cargar_productos():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM productos", conn)
    cols = [c for c in ["codigo","descripcion","categoria","unidad","costo","precio","margen_bruto","markup","margen_%"] if c in df.columns]
    return df[cols]

def buscar_productos(df, query, categoria):
    q = (query or "").strip().lower()
    out = df.copy()
    if categoria and categoria != "Todas":
        out = out[out["categoria"].fillna("").astype(str) == categoria]
    if q:
        mask = pd.Series(False, index=out.index)
        for col in ["codigo","descripcion","categoria"]:
            if col in out.columns:
                mask = mask | out[col].fillna("").astype(str).str.lower().str.contains(q, na=False)
        out = out[mask]
    return out

def init_state():
    if "items" not in st.session_state:
        st.session_state["items"] = []
    if "cliente" not in st.session_state:
        st.session_state.cliente = ""
    if "observaciones" not in st.session_state:
        st.session_state.observaciones = ""
    if "fecha" not in st.session_state:
        st.session_state.fecha = datetime.today().date().isoformat()

def exportar_a_pdf():
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Encabezado
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Presupuesto")
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 70, f"Fecha: {st.session_state.fecha}")
    c.drawString(200, height - 70, f"Cliente: {st.session_state.cliente}")
    c.drawString(50, height - 85, f"Observaciones: {st.session_state.observaciones}")

    # Tabla de ítems
    data = [["Código", "Descripción", "Cantidad", "P. Unitario", "Subtotal"]]
    total = 0
    for it in st.session_state["items"]:
        subtotal = it["cantidad"] * it["precio_unitario"]
        total += subtotal
        data.append([it["codigo"], it["descripcion"], it["cantidad"], f"${it['precio_unitario']:,}".replace(",", "."), f"${subtotal:,}".replace(",", ".")])

    table = Table(data, colWidths=[60, 200, 60, 80, 80])
    style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("TEXTCOLOR", (0,0), (-1,0), colors.black),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("BOTTOMPADDING", (0,0), (-1,0), 6),
        ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
    ])
    table.setStyle(style)

    table.wrapOn(c, width, height)
    table.drawOn(c, 50, height - 300)

    # Total
    c.setFont("Helvetica-Bold", 12)
    c.drawString(400, height - 320 - (len(st.session_state["items"]) * 15), f"TOTAL: ${total:,}".replace(",", "."))

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

def calcular_totales(items):
    return sum(it["cantidad"] * it["precio_unitario"] for it in items)

init_state()
df_prod = cargar_productos()

st.title("🧾 Presupuestos & Costos")
st.caption("Versión con exportación a PDF y opción de ítems personalizados")

with st.sidebar:
    st.header("Cliente")
    st.session_state.fecha = st.date_input("Fecha", value=pd.to_datetime(st.session_state.fecha)).strftime("%Y-%m-%d")
    st.session_state.cliente = st.text_input("Nombre / Razón Social", value=st.session_state.cliente)
    st.session_state.observaciones = st.text_area("Observaciones", value=st.session_state.observaciones, height=100)

    st.markdown("---")
    if st.button("⬇️ Exportar a PDF"):
        if not st.session_state["items"]:
            st.warning("Agregá al menos un ítem antes de exportar.")
        else:
            pdf_data = exportar_a_pdf()
            st.download_button("Descargar Presupuesto.pdf", data=pdf_data, file_name="Presupuesto.pdf", mime="application/pdf")

st.subheader("Buscar productos")
col1, col2 = st.columns([3,2])
with col1:
    q = st.text_input("Texto (código, descripción, categoría)", "")
with col2:
    categorias = ["Todas"] + sorted(df_prod["categoria"].dropna().astype(str).unique()) if "categoria" in df_prod.columns else ["Todas"]
    cat = st.selectbox("Categoría", categorias, index=0)

filt = buscar_productos(df_prod, q, cat)
st.write(f"Resultados: {len(filt)}")
st.dataframe(filt.head(500), use_container_width=True)

# Agregar desde base de datos
st.markdown("### Agregar producto desde la base")
if not filt.empty:
    producto_sel = st.selectbox("Selecciona un producto", filt["descripcion"].tolist())
    fila = filt[filt["descripcion"] == producto_sel].iloc[0]

    col1, col2, col3 = st.columns([3, 2, 2])
    with col1:
        st.text_input("Código", value=str(fila["codigo"]), disabled=True)
    with col2:
        cant_add = st.number_input("Cantidad", min_value=1, value=1, step=1, key="cant_db")
    with col3:
        precio_add = st.number_input("Precio sugerido", value=limpiar_precio(fila["precio"]), step=1, key="precio_db")

    if st.button("➕ Agregar ítem desde base"):
        st.session_state["items"].append({
            "codigo": str(fila["codigo"]),
            "descripcion": str(fila["descripcion"]),
            "cantidad": cant_add,
            "precio_unitario": precio_add
        })

# Agregar ítem personalizado
st.markdown("### Agregar ítem personalizado")
col1, col2, col3, col4 = st.columns([2,4,2,2])
with col1:
    cod_pers = st.text_input("Código", key="codigo_pers")
with col2:
    desc_pers = st.text_input("Descripción", key="desc_pers")
with col3:
    cant_pers = st.number_input("Cantidad", min_value=1, value=1, step=1, key="cant_pers")
with col4:
    precio_pers = st.number_input("Precio unitario", min_value=0, value=0, step=1, key="precio_pers")

if st.button("➕ Agregar ítem personalizado"):
    st.session_state["items"].append({
        "codigo": cod_pers,
        "descripcion": desc_pers,
        "cantidad": cant_pers,
        "precio_unitario": precio_pers
    })

# Lista de ítems
st.markdown("## Ítems del presupuesto")
if not st.session_state["items"]:
    st.info("Todavía no agregaste ítems.")
else:
    to_delete = None
    for i, it in enumerate(st.session_state["items"]):
        c1, c2, c3, c4, c5 = st.columns([2,2,5,2,1])
        with c1:
            st.session_state["items"][i]["cantidad"] = st.number_input("Cantidad", min_value=0, value=int(it["cantidad"]), step=1, key=f"cant_{i}")
        with c2:
            st.session_state["items"][i]["codigo"] = st.text_input("Código", value=it["codigo"], key=f"codigo_{i}")
        with c3:
            st.session_state["items"][i]["descripcion"] = st.text_input("Descripción", value=it["descripcion"], key=f"desc_{i}")
        with c4:
            st.session_state["items"][i]["precio_unitario"] = st.number_input("Precio unitario", min_value=0, value=int(it["precio_unitario"]), step=1, key=f"precio_{i}")
        with c5:
            if st.button("🗑️", key=f"del_{i}"):
                to_delete = i

    if to_delete is not None:
        st.session_state["items"].pop(to_delete)

    total = calcular_totales(st.session_state["items"])
    st.markdown(f"### Total: **${int(total):,}**".replace(",", "."))
