@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 >nul 2>&1

echo ============================================
echo   Meta Ads Agent - Dashboard
echo ============================================
echo.

REM --- Paso 1: Verificar que Python esta instalado ---
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python NO esta instalado o no esta en el PATH.
    echo.
    echo Solucion:
    echo  1. Ve a https://www.python.org/downloads/
    echo  2. Descarga Python 3.10 o superior
    echo  3. Al instalar, marca "Add Python to PATH"
    echo  4. Reinicia la computadora y vuelve a intentar
    echo.
    pause
    exit /b 1
)

python --version
echo [OK] Python encontrado
echo.

REM --- Paso 2: Instalar dependencias si faltan ---
echo Verificando librerias...
pip install flask requests python-dotenv werkzeug fpdf2 waitress --quiet --exists-action i
if %errorlevel% neq 0 (
    echo [AVISO] Hubo un problema instalando librerias. Intentando continuar...
)
echo [OK] Librerias listas
echo.

REM --- Paso 3: Verificar que el archivo principal existe ---
if not exist "meta_ads_agent.py" (
    echo [ERROR] No se encontro el archivo meta_ads_agent.py
    echo.
    echo Asegurate de que este archivo .bat este dentro de la carpeta meta-ads
    echo junto con el archivo meta_ads_agent.py
    echo.
    pause
    exit /b 1
)

REM --- Paso 4: Verificar que el archivo .env.txt existe ---
if not exist ".env.txt" (
    echo [AVISO] No se encontro el archivo .env.txt
    echo El sistema puede funcionar pero sin credenciales de Meta.
    echo.
)

REM --- Paso 5: Verificar si el puerto 5000 ya esta en uso ---
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [AVISO] El puerto 5000 ya esta en uso.
    echo Es posible que el dashboard ya este abierto en otra ventana.
    echo.
    echo Abriendo el navegador en http://localhost:5000 ...
    timeout /t 2 >nul
    start http://localhost:5000
    echo.
    echo Si el dashboard NO carga, cierra todas las ventanas negras
    echo y vuelve a hacer doble click en este archivo.
    echo.
    pause
    exit /b 0
)

REM --- Paso 6: Iniciar el dashboard ---
echo [OK] Iniciando el Dashboard...
echo.
echo El navegador se abrira automaticamente en unos segundos.
echo Para cerrar el dashboard, cierra esta ventana negra.
echo.

REM Abrir el navegador despues de 4 segundos (en segundo plano)
start /b cmd /c "timeout /t 4 >nul && start http://localhost:5000"

REM Iniciar el programa Python
python meta_ads_agent.py

REM Si Python termina con error, mostrar mensaje
echo.
echo ============================================
echo  El dashboard se cerro o hubo un error.
echo  Lee el mensaje de arriba para ver que paso.
echo ============================================
echo.
pause
