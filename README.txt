Presupuestos & Costos - Instrucciones de uso

Requisitos
- Python 3.9+ instalado

Instalación
1) Abrí una consola en la carpeta del proyecto
2) Instalá dependencias:
   pip install -r requirements.txt

Windows (.bat)
- Ejecutá: iniciar_presupuesto.bat

Linux/macOS
- Ejecutá:
   streamlit run app_presupuestos_pdf.py

Funciones
- Presupuestos: buscá productos, agregá ítems personalizados y exportá a PDF.
- Productos (Excel): descargá la lista actual y subí un .xlsx para reemplazar la tabla productos. Las columnas esperadas incluyen: codigo, descripcion, categoria, unidad, costo, precio. Los precios se guardan como enteros (sin decimales).
- Caja: registrá movimientos (Ingreso/Egreso) por tipo (Efectivo/Transferencia) y consultá totales por rango.

Notas
- Si ves problemas con acentos en Windows: el .bat ya fuerza UTF-8.
- Fuente PDF: se usa Arial en Windows si está disponible (o DejaVu/Liberation).