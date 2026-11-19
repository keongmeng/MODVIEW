@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 安装依赖...
python -m pip install -r requirements.txt pyinstaller || goto :fail
echo [2/2] 打包...
python -m PyInstaller MODVIEW.spec --noconfirm || goto :fail
echo.
echo 完成：dist\MODVIEW.exe
goto :eof
:fail
echo.
echo 失败，检查上方的报错。
exit /b 1
