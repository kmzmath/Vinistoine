@echo off
title Iniciar servidor FastAPI

cd /d "%~dp0"

echo.
echo === Criando ambiente virtual ===
py -m venv venv
if errorlevel 1 (
    echo.
    echo ERRO: Falha ao criar o ambiente virtual.
    echo Verifique se o Python esta instalado e disponivel no PATH.
    pause
    exit /b 1
)

echo.
echo === Ativando ambiente virtual ===
call "%~dp0venv\Scripts\activate.bat"
if errorlevel 1 (
    echo.
    echo ERRO: Falha ao ativar o ambiente virtual.
    pause
    exit /b 1
)

echo.
echo === Instalando dependencias ===
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERRO: Falha ao instalar as dependencias.
    echo Verifique se o arquivo requirements.txt esta na mesma pasta deste arquivo.
    pause
    exit /b 1
)

echo.
echo === Iniciando servidor ===
python -m uvicorn server.main:app --reload --host 127.0.0.1 --port 8000

echo.
echo Servidor encerrado.
pause
