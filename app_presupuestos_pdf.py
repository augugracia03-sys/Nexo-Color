# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from io import BytesIO

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


# Ensure useful indexes exist for faster lookups if the columns are present
@st.cache_resource(show_spinner=False)
def ensure_db_indexes():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cols_df = pd.read_sql_query("PRAGMA table_info(productos)", conn)
            col_names = set(cols_df["name"].astype(str).tolist()) if not cols_df.empty else set()
            cur = conn.cursor()
            if "codigo" in col_names:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_productos_codigo ON productos(codigo)")
            if "descripcion" in col_names:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_productos_descripcion ON productos(descripcion)")
            if "categoria" in col_names:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_productos_categoria ON productos(categoria)")
            conn.commit()
    except Exception:
        # If anything goes wrong, just continue without blocking the app
        pass
    return True


st.set_page_config(page_title="Presupuestos & Costos", layout="wide")
# Run once per session
ensure_db_indexes()


@st.cache_data(show_spinner=False)
def cargar_productos():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM productos", conn)
    cols = [c for c in [
        "codigo",
        "descripcion",
        "categoria",
        "unidad",
        "costo",
        "precio",
        "margen_bruto",
        "markup",
        "margen_%",
    ] if c in df.columns]
    df = df[cols].copy()

    # Precompute normalized columns once for faster repeated searches
    for base_col in ["codigo", "descripcion", "categoria"]:
        if base_col in df.columns:
            df[f"__{base_col}_norm"] = df[base_col].fillna("").astype(str).str.lower()

    return df


def buscar_productos(df, query, categoria):
    q = (query or "").strip().lower()
    out = df
    if categoria and categoria != "Todas" and "categoria" in out.columns:
        out = out[out["categoria"].fillna("").astype(str) == categoria]
    if q:
        norm_cols = [c for c in ["__codigo_norm", "__descripcion_norm", "__categoria_norm"] if c in out.columns]
        if norm_cols:
            mask = pd.Series(False, index=out.index)
            for c in norm_cols:
                mask = mask | out[c].str.contains(q, na=False)
        else:
            # Fallback in case normalized columns are not present
            mask = pd.Series(False, index=out.index)
            for col in ["codigo", "descripcion", "categoria"]:
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
    # Lazy import heavy dependencies to improve initial page load time
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()

    # Try to register Unicode-capable fonts to properly render accents
    def try_register_fonts():
        candidates = [
            ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ("Arial", r"C:\\Windows\\Fonts\\arial.ttf", r"C:\\Windows\\Fonts\\arialbd.ttf"),
            ("LiberationSans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ]
        for name, regular, bold in candidates:
            if os.path.exists(regular) and os.path.exists(bold):
                try:
                    pdfmetrics.registerFont(TTFont(name, regular))
                    pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold))
                    return name, f"{name}-Bold"
                except Exception:
                    continue
        return "Helvetica", "Helvetica-Bold"

    base_font, base_font_bold = try_register_fonts()

    # Custom styles
    title_style = ParagraphStyle(
        "TitleCustom",
        parent=styles["Title"],
        fontName=base_font_bold,
        fontSize=18,
        leading=22,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#1f2937"),
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontName=base_font_bold,
        fontSize=10,
        textColor=colors.HexColor("#374151"),
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontName=base_font,
        fontSize=10,
        textColor=colors.HexColor("#111827"),
    )
    total_style = ParagraphStyle(
        "Total",
        parent=styles["Normal"],
        fontName=base_font_bold,
        fontSize=12,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#111827"),
    )

    def format_money(n):
        try:
            n = float(n)
        except Exception:
            n = 0
        s = f"{int(round(n)):,}".replace(",", ".")
        return f"${s}"

    story = []
    story.append(Paragraph("Presupuesto", title_style))
    story.append(Spacer(1, 6))

    # Details (Fecha, Cliente, Observaciones)
    details_data = [
        [Paragraph("Fecha:", label_style), Paragraph(str(st.session_state.fecha), value_style)],
        [Paragraph("Cliente:", label_style), Paragraph(str(st.session_state.cliente), value_style)],
    ]
    if st.session_state.observaciones:
        details_data.append([Paragraph("Observaciones:", label_style), Paragraph(str(st.session_state.observaciones), value_style)])
    details_tbl = Table(details_data, colWidths=[doc.width * 0.18, doc.width * 0.82])
    details_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(details_tbl)
    story.append(Spacer(1, 12))

    # Items table (without codes). Widen description column.
    data = [["Descripción", "Cantidad", "P. Unitario", "Subtotal"]]
    total = 0
    for it in st.session_state["items"]:
        subtotal = it["cantidad"] * it["precio_unitario"]
        total += subtotal
        data.append([
            str(it["descripcion"]),
            int(it["cantidad"]),
            format_money(it["precio_unitario"]),
            format_money(subtotal),
        ])

    col_widths = [doc.width * 0.6, doc.width * 0.1, doc.width * 0.15, doc.width * 0.15]

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), base_font_bold),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("ALIGN", (2, 1), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f9fafb")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("FONTNAME", (0, 1), (0, -1), base_font),
        ("FONTNAME", (1, 1), (-1, -1), base_font),
    ]))
    story.append(table)

    story.append(Spacer(1, 12))
    story.append(Paragraph(f"TOTAL: {format_money(total)}", total_style))

    doc.build(story)
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
col1, col2 = st.columns([3, 2])
with col1:
    q = st.text_input("Texto (código, descripción, categoría)", "")
with col2:
    categorias = [
        "Todas"
    ] + (sorted(df_prod["categoria"].dropna().astype(str).unique()) if "categoria" in df_prod.columns else [])
    cat = st.selectbox("Categoría", categorias, index=0)

filt = buscar_productos(df_prod, q, cat)
st.write(f"Resultados: {len(filt)}")
st.dataframe(filt.head(500), use_container_width=True)

# Agregar desde base de datos
st.markdown("### Agregar producto desde la base")
if not filt.empty and "descripcion" in filt.columns:
    MAX_OPTIONS = 1000
    opciones = filt["descripcion"].astype(str).head(MAX_OPTIONS).tolist()
    producto_sel = st.selectbox("Selecciona un producto", opciones)
    fila = filt[filt["descripcion"].astype(str) == producto_sel].iloc[0]

    col1, col2, col3 = st.columns([3, 2, 2])
    with col1:
        st.text_input("Código", value=str(fila["codigo"]) if "codigo" in fila else "", disabled=True)
    with col2:
        cant_add = st.number_input("Cantidad", min_value=1, value=1, step=1, key="cant_db")
    with col3:
        precio_add = st.number_input("Precio sugerido", value=limpiar_precio(fila["precio"]) if "precio" in fila else 0, step=1, key="precio_db")

    if st.button("➕ Agregar ítem desde base"):
        st.session_state["items"].append({
            "codigo": str(fila["codigo"]) if "codigo" in fila else "",
            "descripcion": str(fila["descripcion"]) if "descripcion" in fila else "",
            "cantidad": cant_add,
            "precio_unitario": precio_add,
        })

# Agregar ítem personalizado
st.markdown("### Agregar ítem personalizado")
col1, col2, col3, col4 = st.columns([2, 4, 2, 2])
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
        "precio_unitario": precio_pers,
    })

# Lista de ítems
st.markdown("## Ítems del presupuesto")
if not st.session_state["items"]:
    st.info("Todavía no agregaste ítems.")
else:
    to_delete = None
    for i, it in enumerate(st.session_state["items"]):
        c1, c2, c3, c4, c5 = st.columns([2, 2, 5, 2, 1])
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
