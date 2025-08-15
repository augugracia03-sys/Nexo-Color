# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from io import BytesIO

DB_PATH = "presupuestos.db"


def limpiar_precio(valor):
    import re
    # Accept int/float directly
    if isinstance(valor, (int, float)):
        try:
            return int(round(float(valor)))
        except Exception:
            return 0
    s = str(valor or "")
    if not s:
        return 0
    # Keep only digits and separators
    s2 = re.sub(r"[^0-9.,]", "", s)
    if not s2:
        return 0
    has_dot = "." in s2
    has_comma = "," in s2
    # Both separators: last separator is decimal, drop decimals
    if has_dot and has_comma:
        last_dot = s2.rfind(".")
        last_comma = s2.rfind(",")
        last_sep_idx = max(last_dot, last_comma)
        int_part = s2[:last_sep_idx]
        digits_all = re.sub(r"[^0-9]", "", s2)
        int_digits = re.sub(r"[^0-9]", "", int_part)
        # If overall digits look like cents x100 (too large), try downscale
        try:
            raw = int(digits_all)
            if raw > 100_000:
                scaled = raw // 100
                if 100 <= scaled <= 2_000_000:
                    return scaled
        except Exception:
            pass
        return int(int_digits) if int_digits else 0
    # Only one kind of separator
    if has_dot or has_comma:
        sep = "." if has_dot else ","
        last_sep_idx = s2.rfind(sep)
        right_len = len(s2) - last_sep_idx - 1
        digits_all = re.sub(r"[^0-9]", "", s2)
        # If looks like decimals (1-2 digits), drop them
        if right_len in (1, 2):
            int_part = s2[:last_sep_idx]
            int_digits = re.sub(r"[^0-9]", "", int_part)
            # Also check for cents-scaled downscale
            try:
                raw = int(digits_all)
                if raw > 100_000:
                    scaled = raw // 100
                    if 100 <= scaled <= 2_000_000:
                        return scaled
            except Exception:
                pass
            return int(int_digits) if int_digits else 0
        # Otherwise treat as thousands separators: remove all
        digits = re.sub(r"[^0-9]", "", s2)
        # If digits still too large, try x100 downscale
        try:
            raw = int(digits)
            if raw > 100_000:
                scaled = raw // 100
                if 100 <= scaled <= 2_000_000:
                    return scaled
        except Exception:
            pass
        return int(digits) if digits else 0
    # No separators, digits only
    digits = re.sub(r"[^0-9]", "", s2)
    # If digits too large, try downscale
    try:
        raw = int(digits or 0)
        if raw > 100_000:
            scaled = raw // 100
            if 100 <= scaled <= 2_000_000:
                return scaled
    except Exception:
        pass
    return int(digits) if digits else 0


def normalizar_precio_entero(valor):
    try:
        # First try string-based sanitize for strings
        if isinstance(valor, str):
            return limpiar_precio(valor)
        n = int(valor)
    except Exception:
        return limpiar_precio(valor)
    # Heuristic: many sources store cents by scaling x100 (e.g., 4230.32 -> 423032)
    if n > 100_000:
        scaled = n // 100
        if 100 <= scaled <= 2_000_000:
            return scaled
    # Keep reasonable ranges
    if n <= 2_000_000:
        return n
    # Try reducing magnitude assuming concatenated decimals or extra zeros (2-5 digits)
    for k in (5, 4, 3, 2):
        m = n // (10 ** k)
        if 100 <= m <= 2_000_000:
            return m
    return n


def normalizar_codigo(valor):
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    if isinstance(valor, (int, float)):
        try:
            return str(int(round(float(valor))))
        except Exception:
            return str(valor)
    s = str(valor).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def formatear_dinero(valor):
    try:
        n = float(valor)
    except Exception:
        n = 0.0
    return f"${int(round(n)):,}".replace(",", ".")


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


@st.cache_resource(show_spinner=False)
def ensure_caja_schema():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS caja_movimientos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha TEXT NOT NULL,
                    tipo TEXT NOT NULL CHECK(tipo IN ('Efectivo','Transferencia')),
                    movimiento TEXT NOT NULL CHECK(movimiento IN ('Ingreso','Egreso')),
                    concepto TEXT,
                    monto REAL NOT NULL DEFAULT 0
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_caja_fecha ON caja_movimientos(fecha)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_caja_tipo ON caja_movimientos(tipo)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_caja_mov ON caja_movimientos(movimiento)")
            conn.commit()
    except Exception:
        pass
    return True


@st.cache_resource(show_spinner=False)
def ensure_precios_costos_normalizados():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            info = pd.read_sql_query("PRAGMA table_info(productos)", conn)
            if info.empty:
                return True
            cols = set(info["name"].astype(str))
            has_precio = "precio" in cols
            has_costo = "costo" in cols
            if not (has_precio or has_costo):
                return True
            select_cols = []
            if has_precio:
                select_cols.append("precio")
            if has_costo:
                select_cols.append("costo")
            # Use rowid for updates
            sql = f"SELECT rowid, {', '.join(select_cols)} FROM productos"
            cur = conn.cursor()
            rows = cur.execute(sql).fetchall()
            updates_precio = []
            updates_costo = []
            for r in rows:
                rowid = r[0]
                idx = 1
                if has_precio:
                    v = r[idx]
                    idx += 1
                    new_v = normalizar_precio_entero(v)
                    try:
                        old_int = int(v) if v is not None else None
                    except Exception:
                        old_int = None
                    if old_int is None or new_v != old_int:
                        updates_precio.append((new_v, rowid))
                if has_costo:
                    v = r[idx] if len(r) > idx else None
                    new_v = limpiar_precio(v)
                    try:
                        old_int = int(v) if v is not None else None
                    except Exception:
                        old_int = None
                    if old_int is None or new_v != old_int:
                        updates_costo.append((new_v, rowid))
            if updates_precio:
                cur.executemany("UPDATE productos SET precio = ? WHERE rowid = ?", updates_precio)
            if updates_costo:
                cur.executemany("UPDATE productos SET costo = ? WHERE rowid = ?", updates_costo)
            conn.commit()
    except Exception:
        pass
    return True


st.set_page_config(page_title="Presupuestos & Costos", layout="wide")
# Run once per session
ensure_db_indexes()
ensure_caja_schema()
ensure_precios_costos_normalizados()


def cargar_productos():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM productos", conn)
    # Keep only needed columns
    keep_cols = [c for c in [
        "codigo",
        "descripcion",
        "costo",
        "precio",
        "margen_bruto",
    ] if c in df.columns]
    df = df[keep_cols].copy()

    # Normalize codigo
    if "codigo" in df.columns:
        df["codigo"] = df["codigo"].apply(normalizar_codigo)

    # Coerce numeric price-related columns to integers (no decimals)
    for col_num in ["precio", "costo"]:
        if col_num in df.columns:
            df[col_num] = df[col_num].apply(limpiar_precio)

    # Precompute normalized columns once for faster repeated searches
    for base_col in ["codigo", "descripcion"]:
        if base_col in df.columns:
            df[f"__{base_col}_norm"] = df[base_col].fillna("").astype(str).str.lower()

    return df


def buscar_productos(df, query, categoria):
    q = (query or "").strip().lower()
    out = df
    if categoria and categoria != "Todas" and "categoria" in out.columns:
        out = out[out["categoria"].fillna("").astype(str) == categoria]
    if q:
        norm_cols = [c for c in ["__codigo_norm", "__descripcion_norm"] if c in out.columns]
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


def caja_insertar(fecha: str, tipo: str, movimiento: str, concepto: str, monto: float):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO caja_movimientos (fecha, tipo, movimiento, concepto, monto) VALUES (?, ?, ?, ?, ?)",
            (fecha, tipo, movimiento, concepto, float(monto) if monto is not None else 0.0),
        )
        conn.commit()


def caja_consultar(desde: str, hasta: str):
    query = (
        "SELECT fecha, tipo, movimiento, concepto, monto FROM caja_movimientos "
        "WHERE date(fecha) BETWEEN date(?) AND date(?) ORDER BY fecha ASC, id ASC"
    )
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(query, conn, params=[desde, hasta])
    return df


def exportar_productos_a_excel_bytes() -> BytesIO:
    with sqlite3.connect(DB_PATH) as conn:
        try:
            df = pd.read_sql_query("SELECT * FROM productos", conn)
        except Exception:
            df = pd.DataFrame()
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="productos")
    output.seek(0)
    return output


def importar_productos_desde_excel(file_bytes: BytesIO) -> int:
    df_xl = pd.read_excel(file_bytes, engine="openpyxl")
    # Normalize column names
    df_xl.columns = [str(c).strip().lower() for c in df_xl.columns]
    # Expected minimal columns
    mapping = {
        "codigo": ["codigo", "código", "cod"],
        "descripcion": ["descripcion", "descripción", "desc"],
        "categoria": ["categoria", "categoría"],
        "unidad": ["unidad", "uni"],
        "costo": ["costo", "coste"],
        "precio": ["precio", "precio_venta", "pv"],
        "margen_bruto": ["margen_bruto", "margen"],
        "markup": ["markup"],
        "margen_%": ["margen_%", "margen_porcentaje"],
    }

    def find_col(candidates):
        for c in candidates:
            if c in df_xl.columns:
                return c
        return None

    out_cols = {}
    for target, candidates in mapping.items():
        col = find_col(candidates)
        if col:
            out_cols[target] = df_xl[col]
        else:
            out_cols[target] = pd.Series([None] * len(df_xl))

    df_out = pd.DataFrame(out_cols)

    # Coerce numerics without decimals
    for col_num in ["precio", "costo"]:
        if col_num in df_out.columns:
            df_out[col_num] = df_out[col_num].apply(limpiar_precio)

    # Write to DB (replace table)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS productos")
        conn.commit()
        df_out.to_sql("productos", conn, if_exists="replace", index=False)
        conn.commit()
    return len(df_out)


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

# ============== Productos (Excel) ==============
st.subheader("Productos: Importar/Exportar Excel")
colx1, colx2 = st.columns([1, 2])
with colx1:
    if st.button("Descargar Excel actual"):
        xls = exportar_productos_a_excel_bytes()
        st.download_button(
            label="Descargar productos.xlsx",
            data=xls,
            file_name="productos.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
with colx2:
    archivo_excel = st.file_uploader("Subir Excel (.xlsx) para reemplazar la lista", type=["xlsx"])
    if archivo_excel is not None:
        num = importar_productos_desde_excel(archivo_excel)
        st.success(f"Se importaron {num} filas a 'productos'.")
        st.cache_data.clear()
        df_prod = cargar_productos()

st.subheader("Buscar productos")
col1, = st.columns([1])
with col1:
    q = st.text_input("Texto (código o descripción)", "")

# Remove category filtering since column is dropped
cat = None
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
        precio_add = st.number_input("Precio sugerido", value=int(fila["precio"]) if "precio" in fila else 0, step=1, key="precio_db")

    if st.button("➕ Agregar ítem desde base"):
        st.session_state["items"].append({
            "codigo": str(fila["codigo"]) if "codigo" in fila else "",
            "descripcion": str(fila["descripcion"]) if "descripcion" in fila else "",
            "cantidad": cant_add,
            "precio_unitario": precio_add,
            "costo_unitario": int(fila["costo"]) if "costo" in fila else 0,
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

    # Totals: cost, profit, margin (UI only)
    costo_total = 0
    for it in st.session_state["items"]:
        qty = int(it.get("cantidad", 0) or 0)
        costo_unit = int(it.get("costo_unitario", 0) or 0)
        costo_total += qty * costo_unit
    ganancia_total = max(total - costo_total, 0)
    margen_pct = (ganancia_total / total * 100.0) if total else 0.0
    st.caption(
        f"Costo: {formatear_dinero(costo_total)} | "
        f"Ganancia: {formatear_dinero(ganancia_total)} | "
        f"Margen: {margen_pct:.1f}%"
    )


@st.cache_data(show_spinner=False)
def _today_str():
    return datetime.today().date().isoformat()


def formatear_dinero(valor):
    try:
        n = float(valor)
    except Exception:
        n = 0.0
    return f"${int(round(n)):,}".replace(",", ".")

# ============== CAJA ==============
st.markdown("---")
st.header("Caja")

colf1, colf2 = st.columns(2)
with colf1:
    fecha_mov = st.date_input("Fecha del movimiento", value=pd.to_datetime(_today_str()))
with colf2:
    tipo_pago = st.selectbox("Tipo de pago", ["Efectivo", "Transferencia"])

colf3, colf4, colf5 = st.columns([2, 4, 2])
with colf3:
    tipo_mov = st.selectbox("Movimiento", ["Ingreso", "Egreso"])
with colf4:
    concepto = st.text_input("Concepto")
with colf5:
    monto = st.number_input("Monto", min_value=0, value=0, step=1)

if st.button("Registrar movimiento"):
    caja_insertar(
        fecha_mov.strftime("%Y-%m-%d"),
        tipo_pago,
        tipo_mov,
        concepto,
        monto,
    )
    st.success("Movimiento registrado en caja")
    st.cache_data.clear()

st.subheader("Consulta de caja")
colr1, colr2 = st.columns(2)
with colr1:
    desde = st.date_input("Desde", value=pd.to_datetime(_today_str()))
with colr2:
    hasta = st.date_input("Hasta", value=pd.to_datetime(_today_str()))

if desde > hasta:
    st.warning("La fecha 'Desde' no puede ser mayor que 'Hasta'.")
else:
    df_caja = caja_consultar(desde.strftime("%Y-%m-%d"), hasta.strftime("%Y-%m-%d"))
    if not df_caja.empty:
        # Totals
        ingreso_efectivo = df_caja[(df_caja["movimiento"] == "Ingreso") & (df_caja["tipo"] == "Efectivo")]["monto"].sum()
        ingreso_transf = df_caja[(df_caja["movimiento"] == "Ingreso") & (df_caja["tipo"] == "Transferencia")]["monto"].sum()
        egreso_efectivo = df_caja[(df_caja["movimiento"] == "Egreso") & (df_caja["tipo"] == "Efectivo")]["monto"].sum()
        egreso_transf = df_caja[(df_caja["movimiento"] == "Egreso") & (df_caja["tipo"] == "Transferencia")]["monto"].sum()
        total_efectivo = ingreso_efectivo - egreso_efectivo
        total_transf = ingreso_transf - egreso_transf
        total_general = (ingreso_efectivo + ingreso_transf) - (egreso_efectivo + egreso_transf)

        st.write(
            f"Ingresos (Efectivo): {formatear_dinero(ingreso_efectivo)} | "
            f"Ingresos (Transferencia): {formatear_dinero(ingreso_transf)} | "
            f"Egresos (Efectivo): {formatear_dinero(egreso_efectivo)} | "
            f"Egresos (Transferencia): {formatear_dinero(egreso_transf)}"
        )
        st.write(
            f"Total Efectivo: {formatear_dinero(total_efectivo)} | "
            f"Total Transferencias: {formatear_dinero(total_transf)} | "
            f"Total General: {formatear_dinero(total_general)}"
        )

        st.dataframe(df_caja, use_container_width=True)
    else:
        st.info("No hay movimientos en el período seleccionado.")
