@echo off
REM Assign spkid_m to this folder's raw table, by column, via ..\lookup
REM   spkid_by_column.bat
REM   spkid_by_column.bat --show
REM   spkid_by_column.bat other_table.csv
REM   spkid_by_column.bat other_table.csv --show

setlocal
cd /d "%~dp0"

REM ---- edit these two lines ----------------------------------------------
REM COLUMNS is one comma-separated list, STRONGEST FIRST. Spaces and dots
REM in a column name are fine; do not add quotes around the individual
REM names, the whole list is quoted once when it is passed.
set "INFILE=johnston_contact_binaries_raw.csv"
set "COLUMNS=number,prov. des.,name"
REM ------------------------------------------------------------------------

set "FLAGS="
if /i "%~1"=="--show" set "FLAGS=--show"
if /i "%~2"=="--show" set "FLAGS=--show"
if not "%~1"=="" if /i not "%~1"=="--show" set "INFILE=%~1"

if not exist "%INFILE%" (
    echo %INFILE% not found in %CD%.
    exit /b 1
)
if not exist "..\lookup\spkid_by_column_cli.py" (
    echo ..\lookup\spkid_by_column_cli.py not found.
    exit /b 1
)

python -c "import roman" 2>nul || python -m pip install roman
python "..\lookup\spkid_by_column_cli.py" "%INFILE%" --cols "%COLUMNS%" %FLAGS%
if errorlevel 1 exit /b 1

echo.
echo done.
endlocal