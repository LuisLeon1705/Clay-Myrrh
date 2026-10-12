@echo off
chcp 65001 >nul
setlocal
title Clay-Myrrh - Asistente de instalacion
cd /d "%~dp0"
echo.
echo   Clay-Myrrh - tu IA, en casa
echo   ===========================
echo   Asistente de instalacion
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   [!] No se encontro Python.
  echo       Instalalo desde https://www.python.org/downloads/  ^(marca "Add python.exe to PATH"^)
  echo       o en una terminal:  winget install Python.Python.3.13
  echo.
  pause
  exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo   [!] Se necesita Python 3.10 o superior.
  pause
  exit /b 1
)

python -c "import rich, prompt_toolkit, requests, psutil, pymupdf, PIL, pynvml" >nul 2>nul
if errorlevel 1 (
  echo   Faltan librerias de Python: rich, prompt_toolkit, requests, psutil, pymupdf, pillow, nvidia-ml-py
  choice /c SN /m "   Instalarlas ahora con pip"
  if errorlevel 2 (
    echo   Sin ellas el asistente no puede continuar.
    pause
    exit /b 1
  )
  python -m pip install --user -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo   [!] Fallo la instalacion de librerias.
    pause
    exit /b 1
  )
)

rem accesos directos con el icono (los .bat no pueden llevar icono propio)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0localai\assets\accesos.ps1" >nul 2>nul
echo   Se abrira el asistente en tu navegador.
echo   Esta ventana se cerrara sola al terminar la instalacion (o al cerrar la pagina).
echo.
python -X utf8 "%~dp0local_ai.py" --setup
if not errorlevel 1 exit /b 0
echo.
echo   No se pudo abrir la version visual. Abriendo la version de consola...
python -X utf8 "%~dp0local_ai.py" --setup-cli
echo.
echo   Para empezar a usarlo:  acceso directo "Clay-Myrrh" o clay-myrrh.cmd
echo   Para descargar lo elegido mas tarde:  clay-myrrh --download
echo.
pause
