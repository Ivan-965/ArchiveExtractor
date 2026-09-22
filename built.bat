@echo off
echo ========================================
echo   Сборка Archive Extractor в EXE
echo ========================================
echo.

call venv\Scripts\activate

echo Установка зависимостей...
pip install -r requirements.txt -q

echo.
echo Сборка EXE (это может занять 1-2 минуты)...
pyinstaller ^
    --onefile ^
    --console ^
    --name ArchiveExtractor ^
    --add-data "tools/unrar.exe;." ^
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
    src/main.py

echo.
if exist dist\ArchiveExtractor.exe (
    echo ========================================
    echo   ✅ Готово!
    echo   Файл: dist\ArchiveExtractor.exe
    echo ========================================
) else (
    echo ❌ Ошибка сборки! Проверьте логи выше.
)
echo.
pause