@echo off
echo ============================================
echo  Asher_DFM: Building executable (Asher_DFM.ico)...
echo ============================================
echo.

pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM 若图标不存在则生成 (需要 Pillow)
if not exist "Asher_DFM.ico" (
    echo Generating Asher_DFM icon...
    python make_icon.py
)

echo Cleaning old build (Asher_DFM only)...
if exist "build\Asher_DFM" rmdir /s /q "build\Asher_DFM"
if exist "dist\Asher_DFM"  rmdir /s /q "dist\Asher_DFM"

echo.
echo Building with PyInstaller...
python -m PyInstaller --clean --noconfirm "Asher_DFM.spec"

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo  Build SUCCESS!
    echo  Output: dist\Asher_DFM\Asher_DFM.exe
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  Build FAILED!
    echo ============================================
)
pause
