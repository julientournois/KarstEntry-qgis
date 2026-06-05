@echo off
setlocal
rem Paquet utilisateur : plugin dans un sous-dossier karst_entry\ ;
rem depot de dev : plugin a cote du script.
if exist "%~dp0karst_entry\metadata.txt" (
    set SOURCE=%~dp0karst_entry\
) else (
    set SOURCE=%~dp0
)
set DEST3=%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\karst_entry
set DEST4=%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\karst_entry

rem Fichiers/dossiers de dev a ne pas deployer dans le plugin installe.
rem xcopy /exclude exclut tout fichier dont le chemin contient une de ces chaines.
set EXCLUDE=%TEMP%\karst_entry_exclude.txt
(
echo .git
echo __pycache__
echo .pytest_cache
echo \tests\
echo build_guide_pdf.py
echo install_plugin.bat
echo install_plugin.sh
) > "%EXCLUDE%"

set INSTALLED=0

if exist "%APPDATA%\QGIS\QGIS3" (
    if exist "%DEST3%" rmdir /s /q "%DEST3%"
    xcopy "%SOURCE%" "%DEST3%\" /e /i /q /exclude:%EXCLUDE%
    echo [QGIS 3] Plugin installe dans : %DEST3%
    set INSTALLED=1
)

if exist "%APPDATA%\QGIS\QGIS4" (
    if exist "%DEST4%" rmdir /s /q "%DEST4%"
    xcopy "%SOURCE%" "%DEST4%\" /e /i /q /exclude:%EXCLUDE%
    echo [QGIS 4] Plugin installe dans : %DEST4%
    set INSTALLED=1
)

del "%EXCLUDE%" 2>nul

if "%INSTALLED%"=="0" (
    echo Aucune installation QGIS 3 ou 4 detectee dans %APPDATA%\QGIS\
)

echo Recharge le plugin dans QGIS (Extensions ^> Recharger) ou redemarrer QGIS.
pause
