@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "APP_PYTHON="
if defined LITERATURE_PYTHON if exist "%LITERATURE_PYTHON%" set "APP_PYTHON=%LITERATURE_PYTHON%"
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "C:\Program Files\Python313\python.exe") do if not defined APP_PYTHON if exist "%%~P" set "APP_PYTHON=%%~P"
if not defined APP_PYTHON for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined APP_PYTHON if /I not "%%~dpP"=="%LOCALAPPDATA%\Microsoft\WindowsApps\" set "APP_PYTHON=%%P"
if not defined APP_PYTHON goto install_python
"%APP_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if not errorlevel 1 goto launch
:install_python
where winget.exe >nul 2>nul
if errorlevel 1 (
  echo 请先从 https://www.python.org/downloads/windows/ 安装 Python 3.13 或更新版本，再重新运行本文件。
  echo 此电脑未提供 winget，详细步骤见 docs\安装部署.md。
  pause
  exit /b 1
)
echo 正在通过 Windows 官方包管理器安装 Python...
winget install --exact --id Python.Python.3.13 --scope user --architecture x64 --silent --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
  echo Python 自动安装未完成，请按 docs\安装部署.md 的备用步骤处理。
  pause
  exit /b 1
)
set "APP_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%APP_PYTHON%" (
  echo 请关闭并重新打开本文件，让安装后的 Python 被识别。
  pause
  exit /b 1
)
:launch
"%APP_PYTHON%" -X utf8 "%~dp0launch.py" %*
if errorlevel 1 (
  echo 未完成，可运行 doctor.cmd 或查看上方提示。不会自动购买服务或更换账号。
  pause
  exit /b 1
)
endlocal
