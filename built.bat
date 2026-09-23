@echo off
chcp 65001 >nul
title Сборка Archive Extractor

echo ========================================
echo   Сборка Archive Extractor в EXE
echo ========================================
echo.

:: Проверка наличия Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ ОШИБКА: Python не найден!
    echo Установите Python с https://www.python.org/downloads/
    echo При установке ОБЯЗАТЕЛЬНО отметьте "Add Python to PATH"
    pause
    exit /b 1
)

:: Поиск существующего виртуального окружения
set "VENV_DIR="
set "VENV_NAMES=venv .venv env ENV virtualenv .env"

echo 🔍 Поиск виртуального окружения...
for %%n in (%VENV_NAMES%) do (
    if exist "%%n\Scripts\activate.bat" (
        echo    ✅ Найдено: %%n
        set "VENV_DIR=%%n"
        goto :venv_found
    )
)

:: Если не найдено — создаём новое
echo    ⚠️  Виртуальное окружение не найдено. Создаём...
python -m venv venv
if errorlevel 1 (
    echo ❌ Не удалось создать venv!
    pause
    exit /b 1
)
set "VENV_DIR=venv"
echo    ✅ Создано: venv
echo.

:venv_found
echo.

:: Активация найденного/созданного venv
echo 🔌 Активация виртуального окружения: %VENV_DIR%
call %VENV_DIR%\Scripts\activate.bat
if errorlevel 1 (
    echo ❌ Не удалось активировать %VENV_DIR%!
    pause
    exit /b 1
)

:: Установка зависимостей
echo 📥 Установка зависимостей...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo ❌ Ошибка установки зависимостей!
    pause
    exit /b 1
)
echo ✅ Зависимости установлены.
echo.

:: Проверка unrar.exe
set "UNRAR_ARG="
if exist "tools\unrarw64.exe" (
    echo ✅ unrar.exe найден в tools/
    set "UNRAR_ARG=--add-data tools/unrarw64.exe;."
) else (
    echo ⚠️  ВНИМАНИЕ: tools\unrar.exe не найден!
    echo    Программа будет собрана БЕЗ поддержки RAR-архивов.
    echo    Скачайте unrar.exe с https://www.rarlab.com/rar_add.htm
    echo    и положите в папку tools\, затем запустите сборку снова.
    echo.
)

:: Сборка EXE
echo 🔨 Сборка EXE (это может занять 1-3 минуты)...
echo.

python -m PyInstaller ^
    --onefile ^
    --console ^
    --name ArchiveExtractor ^
    --clean ^
    --noconfirm ^
    --hidden-import questionary ^
    --hidden-import prompt_toolkit ^
    --hidden-import prompt_toolkit.formatted_text ^
    --hidden-import prompt_toolkit.key_binding ^
    --hidden-import prompt_toolkit.layout ^
    --hidden-import prompt_toolkit.styles ^
    --hidden-import rich ^
    --hidden-import rich.console ^
    --hidden-import rich.panel ^
    --hidden-import rich.table ^
    --hidden-import rich.progress ^
    --hidden-import py7zr ^
    --hidden-import rarfile ^
    %UNRAR_ARG% ^
    src\main.py

echo.
if exist "dist\ArchiveExtractor.exe" (
    echo ========================================
    echo   ✅ СБОРКА УСПЕШНО ЗАВЕРШЕНА!
    echo ========================================
    echo.
    echo  Готовый файл: dist\ArchiveExtractor.exe
    echo.
    echo 💡 Можете скопировать его в любое место
    echo    и запускать без Python.
    echo.
) else (
    echo ❌ ОШИБКА СБОРКИ!
    echo Проверьте логи выше.
    echo.
)

pause