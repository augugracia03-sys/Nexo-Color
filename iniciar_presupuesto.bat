@echo off
chcp 65001 >NUL
set PYTHONUTF8=1
cd /d "%~dp0"
streamlit run app_presupuestos_pdf.py
pause
