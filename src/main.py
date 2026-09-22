import logging
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ==================== СТОРОННИЕ БИБЛИОТЕКИ ====================
try:
    import py7zr
except ImportError:
    py7zr = None

try:
    import rarfile
except ImportError:
    rarfile = None

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.text import Text
import questionary
from questionary import Style

# ==================== НАСТРОЙКА ДЛЯ EXE ====================
def setup_unrar_for_exe():
    """Если скрипт запущен как EXE (PyInstaller), извлекаем unrar.exe из ресурсов."""
    if getattr(sys, 'frozen', False) and rarfile:
        base_path = sys._MEIPASS
        unrar_path = os.path.join(base_path, 'unrar.exe')
        if os.path.exists(unrar_path):
            rarfile.UNRAR_TOOL = unrar_path

setup_unrar_for_exe()

# ==================== СТИЛИ И КОНСОЛЬ ====================
console = Console()

CUSTOM_STYLE = Style([
    ('qmark', 'fg:#673ab7 bold'),
    ('question', 'bold'),
    ('answer', 'fg:#f44336 bold'),
    ('pointer', 'fg:#673ab7 bold'),
    ('highlighted', 'fg:#673ab7 bold'),
    ('selected', 'fg:#4caf50'),
    ('separator', 'fg:#6c6c6c'),
    ('instruction', 'fg:#808080'),
    ('text', ''),
])

SUPPORTED_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz'}

# ==================== УТИЛИТЫ РАСПАКОВКИ ====================

def get_unique_dir(base_path: Path) -> Path:
    """Создает уникальный путь, добавляя _1, _2, если папка уже существует."""
    if not base_path.exists():
        return base_path
    counter = 1
    while True:
        new_path = base_path.parent / f"{base_path.name}_{counter}"
        if not new_path.exists():
            return new_path
        counter += 1

def is_safe_path(base_dir: Path, target_path: Path) -> bool:
    """Защита от Path Traversal (когда в архиве пути вроде ../../etc/passwd)."""
    try:
        target_path.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False

def has_root_lua(archive_path: Path) -> bool:
    """Проверяет наличие .lua файлов строго в корне архива."""
    ext = archive_path.suffix.lower()
    try:
        if ext == '.zip':
            with zipfile.ZipFile(archive_path, 'r') as z:
                return any(n.endswith('.lua') and '/' not in n and '\\' not in n for n in z.namelist())
        elif ext in {'.tar', '.gz', '.bz2', '.xz'}:
            with tarfile.open(archive_path, 'r:*') as t:
                return any(m.name.endswith('.lua') and '/' not in m.name and '\\' not in m.name
                           for m in t.getmembers() if m.isfile())
        elif ext == '.7z' and py7zr:
            with py7zr.SevenZipFile(archive_path, 'r') as z:
                return any(n.endswith('.lua') and '/' not in n and '\\' not in n for n in z.getnames())
        elif ext == '.rar' and rarfile:
            with rarfile.RarFile(archive_path, 'r') as r:
                return any(n.endswith('.lua') and '/' not in n and '\\' not in n for n in r.namelist())
    except Exception:
        pass
    return False

def extract_to_dir(archive_path: Path, dest_dir: Path):
    """Быстрая распаковка архива в указанную папку."""
    ext = archive_path.suffix.lower()
    if ext == '.zip':
        with zipfile.ZipFile(archive_path, 'r') as z:
            z.extractall(dest_dir)
    elif ext in {'.tar', '.gz', '.bz2', '.xz'}:
        with tarfile.open(archive_path, 'r:*') as t:
            t.extractall(dest_dir)
    elif ext == '.7z' and py7zr:
        with py7zr.SevenZipFile(archive_path, 'r') as z:
            z.extractall(path=dest_dir)
    elif ext == '.rar' and rarfile:
        with rarfile.RarFile(archive_path, 'r') as r:
            r.extractall(dest_dir)
    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}")

def move_files_smart(src_dir: Path, dest_dir: Path, skip_existing: bool) -> int:
    """Переносит файлы из temp в dest. Если skip_existing=True, пропускает существующие."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for root, dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        target_root = dest_dir / rel_root if rel_root != '.' else dest_dir
        for file in files:
            src_file = Path(root) / file
            dst_file = target_root / file
            if not is_safe_path(dest_dir, dst_file):
                continue
            if skip_existing and dst_file.exists():
                continue
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_file), str(dst_file))
            moved += 1
    return moved

# ==================== ЯДРО ПРОГРАММЫ ====================

class ArchiveExtractorApp:
    def __init__(self):
        self.source_dir = ""
        self.dest_dir = ""
        self.mode = "overwrite"
        self.workers = 4

    def clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')

    def show_banner(self):
        self.clear_screen()
        banner = Text()
        banner.append("  🗜️  ", style="bold yellow")
        banner.append("Archive Extractor", style="bold cyan")
        banner.append("  v1.0  ", style="bold yellow")
        console.print(Panel(banner, border_style="cyan", expand=False))
        console.print()

    def show_main_menu(self):
        self.show_banner()

        # Показываем текущие настройки
        settings_table = Table(show_header=False, box=None, padding=(0, 2))
        settings_table.add_column(style="dim")
        settings_table.add_column(style="bold white")
        settings_table.add_row("📂 Источник:", self.source_dir or "[dim italic]не задан[/]")
        settings_table.add_row("📁 Назначение:", self.dest_dir or "[dim italic]не задан[/]")
        settings_table.add_row("🔄 Режим:", "Замена" if self.mode == "overwrite" else "Только новое")
        settings_table.add_row("⚡ Потоки:", str(self.workers))
        console.print(Panel(settings_table, title="Текущие настройки", border_style="blue", expand=False))
        console.print()

        choice = questionary.select(
            "Что хотите сделать?",
            choices=[
                "🚀 Распаковать архивы",
                "⚙️  Настройки",
                "ℹ️  Поддерживаемые форматы",
                "🚪 Выход"
            ],
            style=CUSTOM_STYLE,
            use_shortcuts=True
        ).ask()

        return choice

    def ask_path(self, message: str, default: str = "") -> str:
        """Спрашивает путь с валидацией."""
        while True:
            path = questionary.path(
                message,
                default=default,
                style=CUSTOM_STYLE
            ).ask()

            if path is None:
                return ""

            path = path.strip().strip('"').strip("'")
            if not path:
                console.print("[red]❌ Путь не может быть пустым![/]")
                continue

            p = Path(path)
            if not p.exists():
                create = questionary.confirm(
                    f"Папка '{path}' не существует. Создать?",
                    default=True,
                    style=CUSTOM_STYLE
                ).ask()
                if create:
                    try:
                        p.mkdir(parents=True, exist_ok=True)
                        return str(p.resolve())
                    except Exception as e:
                        console.print(f"[red]❌ Не удалось создать папку: {e}[/]")
                        continue
                else:
                    continue

            return str(p.resolve())

    def run_settings(self):
        self.clear_screen()
        console.print(Panel("⚙️  Настройки", border_style="yellow", expand=False))
        console.print()

        while True:
            setting = questionary.select(
                "Что изменить?",
                choices=[
                    f"📂 Папка с архивами  [dim]({self.source_dir or 'не задан'})[/]",
                    f"📁 Папка назначения  [dim]({self.dest_dir or 'не задан'})[/]",
                    f"🔄 Режим  [dim]({'Замена' if self.mode == 'overwrite' else 'Только новое'})[/]",
                    f"⚡ Кол-во потоков  [dim]({self.workers})[/]",
                    "❓ Справка по настройкам",
                    "↩️  Назад в главное меню"
                ],
                style=CUSTOM_STYLE
            ).ask()

            if setting is None or "Назад" in setting:
                break

            if "архивами" in setting:
                self.source_dir = self.ask_path("Укажите папку с архивами:", self.source_dir)

            elif "назначения" in setting:
                self.dest_dir = self.ask_path("Укажите папку назначения:", self.dest_dir)

            elif "Режим" in setting:
                self.mode = questionary.select(
                    "Выберите режим:",
                    choices=[
                        "overwrite — Заменять существующие файлы (рекомендуется)",
                        "skip — Переносить только то, чего нет"
                    ],
                    style=CUSTOM_STYLE
                ).ask()
                if self.mode and "skip" in self.mode:
                    self.mode = "skip"
                else:
                    self.mode = "overwrite"

            elif "потоков" in setting:
                w = questionary.text(
                    "Количество потоков (1-16):",
                    default=str(self.workers),
                    style=CUSTOM_STYLE,
                    validate=lambda val: val.isdigit() and 1 <= int(val) <= 16
                ).ask()
                if w:
                    self.workers = int(w)

            elif "Справка" in setting:
                self.show_settings_help()

    def show_settings_help(self):
        """Показывает подробную справку по всем пунктам настроек."""
        self.clear_screen()
        console.print(Panel("❓ Справка по настройкам", border_style="yellow", expand=False))
        console.print()

        help_content = (
            "[bold cyan]📂 Папка с архивами[/bold cyan]\n"
            "  Путь к папке, где лежат ваши архивы (.zip, .rar, .7z, .tar и т.д.).\n"
            "  Программа найдёт все архивы в этой папке и обработает их.\n"
            "  [dim]Пример: C:\\Downloads\\mods или /home/user/archives[/dim]\n"
            "\n"
            "[bold cyan]📁 Папка назначения[/bold cyan]\n"
            "  Путь к папке, куда будут распакованы файлы из архивов.\n"
            "  Если папка не существует, программа предложит её создать.\n"
            "  [dim]Пример: C:\\Games\\MyGame\\mods[/dim]\n"
            "\n"
            "[bold cyan]🔄 Режим распаковки[/bold cyan]\n"
            "  [yellow]overwrite (Замена)[/yellow] — [bold]рекомендуется[/bold]\n"
            "    Все файлы из архива заменяют существующие в папке назначения.\n"
            "    Используйте для обновления модов или полной перезаписи.\n"
            "    Гарантирует, что у вас ровно та версия, что в архиве.\n"
            "\n"
            "  [yellow]skip (Только новое)[/yellow]\n"
            "    Переносятся только те файлы, которых [bold]ещё нет[/bold] в папке назначения.\n"
            "    Существующие файлы [bold]не перезаписываются[/bold].\n"
            "    Используйте, если вы вручную редактировали файлы и не хотите их потерять,\n"
            "    или собираете коллекцию из разных источников.\n"
            "\n"
            "[bold cyan]⚡ Количество потоков[/bold cyan]\n"
            "  Сколько архивов обрабатывать одновременно.\n"
            "  [dim]Рекомендации:[/dim]\n"
            "  • [green]1-2[/green] — для медленных HDD или если система тормозит\n"
            "  • [green]4[/green] — оптимально для большинства SSD и систем\n"
            "  • [green]8-16[/green] — для быстрых NVMe SSD и мощных процессоров\n"
            "  [yellow]Внимание:[/yellow] слишком много потоков может замедлить работу\n"
            "  из-за нагрузки на диск и процессор.\n"
        )

        console.print(Panel(help_content, border_style="blue", expand=False))
        console.print()
        questionary.press_any_key_to_continue().ask()

    def show_info(self):
        self.clear_screen()
        table = Table(title="Поддерживаемые форматы архивов", border_style="green")
        table.add_column("Формат", style="cyan bold")
        table.add_column("Расширения", style="white")
        table.add_column("Библиотека", style="dim")
        table.add_column("Статус", style="bold")

        table.add_row("ZIP", ".zip", "zipfile (встроена)", "[green]✅ Готов[/]")
        table.add_row("TAR", ".tar .gz .bz2 .xz", "tarfile (встроена)", "[green]✅ Готов[/]")

        status_7z = "[green]✅ Готов[/]" if py7zr else "[red]❌ Не установлен py7zr[/]"
        table.add_row("7-Zip", ".7z", "py7zr", status_7z)

        status_rar = "[green]✅ Готов[/]" if rarfile else "[red]❌ Не установлен rarfile[/]"
        table.add_row("RAR", ".rar", "rarfile + unrar", status_rar)

        console.print(table)
        console.print()

        info_text = (
            "[bold]Логика работы:[/bold]\n"
            "• Если в корне архива есть [cyan].lua[/] файлы → архив распаковывается "
            "в отдельную папку с его именем\n"
            "• Если .lua в корне нет → содержимое распаковывается в общую папку назначения\n"
            "• При конфликте имён папок автоматически добавляется суффикс _1, _2...\n"
            "• [yellow]overwrite[/]: все файлы заменяются (рекомендуется для модов)\n"
            "• [yellow]skip[/]: переносятся только файлы, которых ещё нет в назначении"
        )
        console.print(Panel(info_text, title="Как это работает", border_style="blue"))
        console.print()
        questionary.press_any_key_to_continue().ask()

    def run_extraction(self):
        self.clear_screen()
        console.print(Panel("🚀 Распаковка архивов", border_style="green", expand=False))
        console.print()

        # Проверяем, заданы ли пути
        if not self.source_dir:
            self.source_dir = self.ask_path("Укажите папку с архивами:")
            if not self.source_dir:
                return

        if not self.dest_dir:
            self.dest_dir = self.ask_path("Укажите папку назначения:")
            if not self.dest_dir:
                return

        source = Path(self.source_dir)
        dest = Path(self.dest_dir)

        if not source.is_dir():
            console.print(f"[red]❌ Папка не найдена: {source}[/]")
            questionary.press_any_key_to_continue().ask()
            return

        dest.mkdir(parents=True, exist_ok=True)

        # Собираем архивы
        archives = sorted([
            f for f in source.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
        ])

        if not archives:
            console.print("[yellow]⚠️  В указанной папке архивов не найдено.[/]")
            questionary.press_any_key_to_continue().ask()
            return

        console.print(f"[green]Найдено архивов:[/] [bold]{len(archives)}[/]")
        console.print(f"[green]Режим:[/] [bold]{'Замена' if self.mode == 'overwrite' else 'Только новое'}[/]")
        console.print(f"[green]Потоки:[/] [bold]{self.workers}[/]")
        console.print()

        confirm = questionary.confirm("Начать распаковку?", default=True, style=CUSTOM_STYLE).ask()
        if not confirm:
            return

        console.print()

        # Запуск распаковки с прогресс-баром
        results = []
        errors = []

        def process_one(archive_path: Path) -> dict:
            result = {"file": archive_path.name, "status": "success", "message": "", "lua": False}
            try:
                lua_in_root = has_root_lua(archive_path)
                result["lua"] = lua_in_root

                if lua_in_root:
                    target_folder = get_unique_dir(dest / archive_path.stem)
                else:
                    target_folder = dest

                if self.mode == 'skip':
                    with tempfile.TemporaryDirectory() as tmpdir:
                        extract_to_dir(archive_path, Path(tmpdir))
                        moved = move_files_smart(Path(tmpdir), target_folder, skip_existing=True)
                        result["message"] = f"Новых файлов: {moved}"
                else:
                    extract_to_dir(archive_path, target_folder)
                    result["message"] = "Распаковано"

            except Exception as e:
                result["status"] = "error"
                result["message"] = str(e)

            return result

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Распаковка...", total=len(archives))

            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {
                    executor.submit(process_one, arch): arch for arch in archives
                }
                for future in as_completed(futures):
                    r = future.result()
                    results.append(r)
                    if r["status"] == "error":
                        errors.append(r)
                    progress.update(task, advance=1)

        # Итоговый отчёт
        console.print()
        success_count = len(results) - len(errors)

        report_table = Table(title="📊 Отчёт", border_style="cyan")
        report_table.add_column("Файл", style="white", max_width=40)
        report_table.add_column("Lua в корне", justify="center")
        report_table.add_column("Статус", justify="center")
        report_table.add_column("Примечание", style="dim")

        for r in sorted(results, key=lambda x: x["file"]):
            lua_icon = "[green]✔[/]" if r["lua"] else "[dim]—[/]"
            if r["status"] == "success":
                status = "[green]✅ OK[/]"
            else:
                status = "[red]❌ Ошибка[/]"
            report_table.add_row(r["file"], lua_icon, status, r["message"])

        console.print(report_table)
        console.print()
        console.print(
            f"[bold green]Успешно: {success_count}[/]  |  "
            f"[bold red]Ошибок: {len(errors)}[/]"
        )

        if errors:
            console.print()
            console.print("[bold red]Детали ошибок:[/]")
            for e in errors:
                console.print(f"  [red]•[/] {e['file']}: {e['message']}")

        console.print()
        questionary.press_any_key_to_continue().ask()

    def run(self):
        """Главный цикл приложения."""
        try:
            while True:
                choice = self.show_main_menu()

                if choice is None or "Выход" in choice:
                    console.print("\n[bold cyan]👋 До свидания![/]\n")
                    break
                elif "Распаковать" in choice:
                    self.run_extraction()
                elif "Настройки" in choice:
                    self.run_settings()
                elif "форматы" in choice or "Информация" in choice:
                    self.show_info()

        except KeyboardInterrupt:
            console.print("\n\n[bold cyan]👋 Прервано пользователем. До свидания![/]\n")
            sys.exit(0)
        except EOFError:
            console.print("\n\n[bold cyan]👋 До свидания![/]\n")
            sys.exit(0)


# ==================== ТОЧКА ВХОДА ====================
if __name__ == "__main__":
    app = ArchiveExtractorApp()
    app.run()