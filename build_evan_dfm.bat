@echo off
echo ============================================
echo  Evan_DFM: Building executable (evan.ico)...
echo ============================================
echo.

pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo Cleaning old build (Evan_DFM only)...
if exist "build\Evan_DFM" rmdir /s /q "build\Evan_DFM"
if exist "dist\Evan_DFM"  rmdir /s /q "dist\Evan_DFM"

echo.
echo Building with PyInstaller...
python -m PyInstaller --clean --noconfirm "Evan_DFM.spec"

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo  Build SUCCESS!
    echo  Output: dist\Evan_DFM\Evan_DFM.exe
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  Build FAILED!
    echo ============================================
)
pause