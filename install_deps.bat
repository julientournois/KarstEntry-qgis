@echo off
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   Karst Entry -- Installation des dependances Python
echo ============================================================
echo.

rem requirements.txt : a cote du script (depot) ou dans karst_entry\ (paquet).
set REQ=%~dp0requirements.txt
if not exist "%REQ%" set REQ=%~dp0karst_entry\requirements.txt
if not exist "%REQ%" (
    echo [ERREUR] requirements.txt introuvable.
    pause
    exit /b 1
)

rem Le fichier ne contient-il que des commentaires / lignes vides ?
set HAS_DEPS=0
for /f "usebackq tokens=* delims= " %%L in ("%REQ%") do (
    set "LINE=%%L"
    if not "!LINE!"=="" if not "!LINE:~0,1!"=="#" set HAS_DEPS=1
)
if "%HAS_DEPS%"=="0" (
    echo [INFO] Aucune dependance externe requise par Karst Entry.
    echo        Le plugin n'utilise que la stdlib Python et PyQGIS.
    echo        Rien a installer.
    echo.
    pause
    exit /b 0
)

rem -----------------------------------------------------------------------
rem Recherche du Python QGIS (QGIS 4.x avant 3.x ; lecteurs C: D: E:)
rem -----------------------------------------------------------------------
set PYTHON_EXE=
set QGIS_VER=
set QGIS_DIR=

for %%L in (C D E) do (
    for /d %%D in ("%%L:\Program Files\QGIS 4*") do (
        for %%P in (Python312 Python311 Python310) do (
            if "!PYTHON_EXE!"=="" if exist "%%D\apps\%%P\python.exe" (
                set "PYTHON_EXE=%%D\apps\%%P\python.exe"
                set "QGIS_VER=4"
                set "QGIS_DIR=%%D"
            )
        )
    )
)
if "!PYTHON_EXE!"=="" (
    for %%L in (C D E) do (
        for /d %%D in ("%%L:\Program Files\QGIS 3*") do (
            for %%P in (Python312 Python311 Python39) do (
                if "!PYTHON_EXE!"=="" if exist "%%D\apps\%%P\python.exe" (
                    set "PYTHON_EXE=%%D\apps\%%P\python.exe"
                    set "QGIS_VER=3"
                    set "QGIS_DIR=%%D"
                )
            )
        )
    )
)

if "!PYTHON_EXE!"=="" (
    echo [ERREUR] Aucune installation QGIS detectee.
    echo.
    echo Solutions :
    echo   1. Verifiez que QGIS est installe dans C:\Program Files ou D:\Program Files
    echo   2. Editez ce script et renseignez PYTHON_EXE manuellement :
    echo      set PYTHON_EXE=C:\chemin\vers\QGIS\apps\Python312\python.exe
    echo.
    pause
    exit /b 1
)

echo [OK] QGIS !QGIS_VER! detecte
echo      Dossier : !QGIS_DIR!
echo      Python  : !PYTHON_EXE!
echo.

"!PYTHON_EXE!" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] pip non disponible. Relancez en tant qu'Administrateur.
    pause
    exit /b 1
)

echo Installation depuis requirements.txt ...
echo.
"!PYTHON_EXE!" -m pip install --upgrade -r "%REQ%"
if errorlevel 1 (
    echo.
    echo [ERREUR] L'installation a echoue.
    echo   - Relancez en tant qu'Administrateur
    echo   - Verifiez votre connexion internet
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Installation terminee avec succes !
echo   Rechargez le plugin Karst Entry dans QGIS pour appliquer.
echo ============================================================
echo.
pause
