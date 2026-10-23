@echo off
REM ============================================================
REM  Evan DFM 打包脚本 (PyInstaller)
REM  用法: build.bat          打包 exe
REM        build.bat run      打包后直接运行
REM        build.bat clean    清理构建产物
REM ============================================================
setlocal
cd /d "%~dp0"

if "%1"=="clean" goto :clean

where pyinstaller >nul 2>nul
if errorlevel 1 (
  echo [INFO] 未检测到 pyinstaller, 正在安装 ...
  python -m pip install pyinstaller
  if errorlevel 1 goto :fail
)

echo [1/3] 清理旧产物 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/3] 运行测试 ...
python -m unittest discover -s tests -t . -q
if errorlevel 1 (
  echo [WARN] 测试未全部通过, 仍继续打包 (可用 build.bat 前手动检查^)
)

echo [3/3] PyInstaller 打包 ...
pyinstaller EvanDFM.spec --noconfirm --clean
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  打包完成: dist\DFM.exe
echo ============================================================
if "%1"=="run" start "" "dist\DFM.exe"
goto :eof

:clean
echo 清理 build / dist ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
for /d /r %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
goto :eof

:fail
echo.
echo [ERROR] 打包失败, 请检查上面的输出。
exit /b 1
