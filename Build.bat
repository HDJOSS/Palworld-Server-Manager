@echo off
setlocal

title Palworld Server Manager - Build

echo ==========================================
echo   Palworld Server Manager - EXE Builder
echo ==========================================
echo.

set "PYTHON=C:\Users\joelo\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set "SCRIPT=server_manager.py"
set "ICON=palworld.ico"
set "APPNAME=Palworld Server Manager"

if not exist "%PYTHON%" (
    echo [ERROR] Python was not found:
    echo %PYTHON%
    echo.
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] %SCRIPT% was not found.
    echo.
    pause
    exit /b 1
)

if not exist "%ICON%" (
    echo [ERROR] %ICON% was not found.
    echo.
    pause
    exit /b 1
)

echo [1/5] Checking PyInstaller...
"%PYTHON%" -m PyInstaller --version >nul 2>&1

if errorlevel 1 (
    echo PyInstaller is not installed.
    echo Installing PyInstaller...
    echo.

    "%PYTHON%" -m pip install pyinstaller

    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

echo [OK] PyInstaller found.
echo.

echo [2/5] Cleaning old build files...

if exist "build" (
    rmdir /s /q "build"
)

if exist "dist" (
    rmdir /s /q "dist"
)

if exist "%APPNAME%.spec" (
    del /q "%APPNAME%.spec"
)

echo [OK] Old build files removed.
echo.

echo [3/5] Building application...
echo.

"%PYTHON%" -m PyInstaller ^
    --noconsole ^
    --onefile ^
    --clean ^
    --name "%APPNAME%" ^
    --icon="%ICON%" ^
    --add-data "%ICON%;." ^
    "%SCRIPT%"

if errorlevel 1 (
    echo.
    echo ==========================================
    echo [ERROR] Build failed!
    echo ==========================================
    echo.
    pause
    exit /b 1
)

echo.
echo [4/5] Checking generated EXE...

if not exist "dist\%APPNAME%.exe" (
    echo [ERROR] EXE was not created.
    echo.
    pause
    exit /b 1
)

echo [OK] EXE created successfully.
echo.

echo [5/5] Build completed!
echo.
echo ==========================================
echo   BUILD SUCCESSFUL
echo ==========================================
echo.
echo EXE:
echo %CD%\dist\%APPNAME%.exe
echo.

echo Opening dist folder...
explorer "%CD%\dist"

echo.
echo Done.
pause