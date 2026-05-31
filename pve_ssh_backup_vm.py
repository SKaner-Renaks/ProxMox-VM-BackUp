# -*- coding: utf-8 -*-
# ======================================================================
# Блок заголовок: Метаданные, авторство, версия
# ======================================================================
# Скрипт: pve_ssh_backup_vm.py
# Назначение: Резервное копирование последних архивных копий VM Proxmox
# Автор: Jules (AI Assistant)
# Версия: v 0.01.00
# ======================================================================


# ======================================================================
# Блок переменных: Импорт библиотек, инициализация служебных переменных
# ======================================================================
import os
import sys
import time
import shutil
import ctypes
import importlib.util
from datetime import datetime
from pathlib import Path

# Попытка импорта сторонних библиотек
try:
    import paramiko
except ImportError:
    print("Ошибка: Библиотека 'paramiko' не найдена. Установите её командой: pip install paramiko")
    sys.exit(1)

VERSION = "v 0.01.00"
CONFIG_FILE_NAME = "pve_ssh_backup_vm_config.py"

class Color:
    """Набор ANSI-кодов для цветного вывода."""
    GREEN  = '\033[32m'
    RED    = '\033[31m'
    YELLOW = '\033[33m'
    CYAN   = '\033[36m'
    WHITE  = '\033[37m'
    RESET  = '\033[0m'
    CLEAR_LINE = '\033[K'


# ======================================================================
# Блок функций: Отдельные изолированные функции
# ======================================================================


def log_stub(message, level="INFO"):
    """Заглушка для будущей системы логирования."""
    pass


def enable_windows_features():
    """Включение поддержки ANSI и отключение выделения в консоли Windows."""
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        h_input  = kernel32.GetStdHandle(-10)
        h_output = kernel32.GetStdHandle(-11)
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        # ENABLE_PROCESSED_OUTPUT = 0x0001
        kernel32.SetConsoleMode(h_output, 7)
        mode = ctypes.c_uint()
        kernel32.GetConsoleMode(h_input, ctypes.byref(mode))
        # Отключаем QuickEdit Mode (выделение мышкой, которое вешает консоль)
        kernel32.SetConsoleMode(h_input, mode.value & ~0x0040)


def create_default_config(config_path):
    """Создание файла конфигурации по умолчанию."""
    content = f'''# -*- coding: utf-8 -*-
"""
Конфигурация для pve_ssh_backup_vm.py
"""

# Список серверов Proxmox для бэкапа
SERVERS = [
    {{
        "name": "PVE-01",
        "host": "192.168.1.100",
        "port": 22,
        "username": "root",
        "key_path": "C:/path/to/private_key", # Путь к приватному ключу SSH
        "local_base_path": "D:/BackUp/Proxmox", # Локальная папка для этого сервера
        # Словарь VM: "ID": количество_копий
        # Если количество 0 - удалить все локальные копии этой VM
        "vm_config": {{
            "101": 3,
            "102": 5,
        }}
    }},
]

# Глобальные настройки
TIMEOUT = 30  # Таймаут сетевых операций в секундах
'''
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)


def load_config():
    """Динамическая загрузка конфигурации."""
    config_path = Path(__file__).parent / CONFIG_FILE_NAME
    if not config_path.exists():
        create_default_config(config_path)
        print(f"{Color.YELLOW}Файл конфигурации {CONFIG_FILE_NAME} не найден и был успешно создан.")
        print(f"Пожалуйста, заполните его перед следующим запуском.{Color.RESET}")
        sys.exit(0)

    spec = importlib.util.spec_from_file_location("config", config_path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    return config


def format_time_delta(seconds):
    """Преобразует секунды в формат ЧЧ:ММ:СС."""
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_spinner(step):
    """Возвращает символ спиннера для индикации активности."""
    spinner_chars = ['\\', '|', '/', '-']
    return spinner_chars[step % 4]


def get_progress_bar(current, total, speed_bps, start_time, finished=False):
    """Генерирует строку прогресс-бара для файла."""
    percent = int(100 * current / total) if total > 0 else 0
    bar_fill = int(20 * current // total) if total > 0 else 0
    bar = ("=" * max(0, bar_fill - 1) + ">").ljust(20, "-")

    speed_mbps = speed_bps / (1024 * 1024)
    current_gb = current / (1024 ** 3)
    total_gb   = total   / (1024 ** 3)

    if finished:
        elapsed = time.time() - start_time
        timer_str = f"ИТОГО: {format_time_delta(elapsed)}"
    else:
        if speed_bps > 0:
            eta = (total - current) / speed_bps
        else:
            eta = 0
        timer_str = f"ЭТА: {format_time_delta(eta)}"

    return (f"[{bar}] {percent}% | "
            f"{current_gb:.2f}/{total_gb:.2f} GB | "
            f"{speed_mbps:.1f} MB/s | {timer_str}")


def get_ssh_client(server_config, timeout=30):
    """Создает и возвращает SSH-клиент для подключения к серверу."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    key_path = server_config.get("key_path")
    try:
        if key_path and os.path.exists(key_path):
            # Paramiko сам определит тип ключа при использовании key_filename
            client.connect(
                hostname=server_config["host"],
                port=server_config.get("port", 22),
                username=server_config["username"],
                key_filename=key_path,
                timeout=timeout
            )
        else:
            client.connect(
                hostname=server_config["host"],
                port=server_config.get("port", 22),
                username=server_config["username"],
                password=server_config.get("password"),
                timeout=timeout
            )
        return client
    except Exception as e:
        log_stub(f"Ошибка подключения к {server_config['host']}: {e}", "ERROR")
        raise


def parse_storage_cfg(config_content):
    """Парсит содержимое /etc/pve/storage.cfg и находит хранилища для бэкапов."""
    backup_storages = {}
    current_storage = None
    lines = config_content.strip().split('\n')

    for line in lines:
        if line.strip().startswith('#'):
            continue

        if not line.startswith((' ', '\t')):
            line = line.strip()
            if not line:
                continue

            parts = line.split(':', 1)
            if len(parts) == 2:
                storage_type = parts[0].strip()
                storage_id = parts[1].strip()
                current_storage = storage_id
                backup_storages[current_storage] = {
                    'type': storage_type,
                    'content': '',
                    'path': f"/mnt/pve/{storage_id}",
                    'nodes': '',
                    'disable': False
                }
        else:
            if current_storage:
                kv_parts = line.split(maxsplit=1)
                if len(kv_parts) == 2:
                    key = kv_parts[0].strip()
                    val = kv_parts[1].strip()
                    if key == 'content':
                        backup_storages[current_storage]['content'] = val
                    elif key == 'path':
                        backup_storages[current_storage]['path'] = val
                    elif key == 'nodes':
                        backup_storages[current_storage]['nodes'] = val
                    elif key == 'disable':
                        backup_storages[current_storage]['disable'] = True

    valid_paths = []
    for s_id, info in backup_storages.items():
        if info['disable'] or info['type'] == 'pbs':
            continue
        if 'backup' in info['content']:
            # Пути к бэкапам в Proxmox обычно дополняются /dump
            dump_path = f"{info['path'].rstrip('/')}/dump"
            valid_paths.append(dump_path)

    return valid_paths


def get_total_progress_bar(current_bytes, total_bytes, current_file_idx, total_files, avg_speed_bps):
    """Генерирует общий прогресс-бар."""
    percent = int(100 * current_bytes / total_bytes) if total_bytes > 0 else 0
    bar_fill = int(50 * current_bytes // total_bytes) if total_bytes > 0 else 0
    bar = ("=" * max(0, bar_fill - 1) + ">").ljust(50, "-")

    current_gb = current_bytes / (1024 ** 3)
    total_gb   = total_bytes   / (1024 ** 3)

    if avg_speed_bps > 0:
        eta = (total_bytes - current_bytes) / avg_speed_bps
    else:
        eta = 0

    return (f"Прогресс:\t[{Color.CYAN}{bar}{Color.RESET}] {percent}% | {current_file_idx}/{total_files} | "
            f"{current_gb:.2f}/{total_gb:.2f} GB | ЭТА: {format_time_delta(eta)}")


def get_latest_backups(ssh_client, dump_paths, vm_ids):
    """Находит последние файлы бэкапов для каждой VM."""
    latest_backups = {} # {vm_id: {datetime: dt, path: p, base_prefix: s, files: {}}}

    sftp = ssh_client.open_sftp()
    try:
        for path in dump_paths:
            try:
                files_attr = sftp.listdir_attr(path)
            except Exception:
                continue # Директория может не существовать на этом узле

            for attr in files_attr:
                filename = attr.filename
                # Формат: vzdump-qemu-XXX-2024_05_31-12_00_00.vma.zst (или .log, .notes)
                parts = filename.split('-')
                if len(parts) >= 5 and parts[2] in vm_ids:
                    vm_id = parts[2]

                    # Извлекаем дату и время
                    try:
                        # parts[3] = "2024_05_31", parts[4] = "12_00_00.vma.zst"
                        time_part = parts[4].split('.')[0]
                        dt_str = parts[3] + '-' + time_part
                        file_dt = datetime.strptime(dt_str, '%Y_%m_%d-%H_%M_%S')
                    except Exception:
                        continue

                    # Префикс для группы файлов: vzdump-qemu-101-2024_05_31-12_00_00
                    base_prefix = f"{parts[0]}-{parts[1]}-{parts[2]}-{parts[3]}-{time_part}"

                    if vm_id not in latest_backups or file_dt > latest_backups[vm_id]['datetime']:
                        latest_backups[vm_id] = {
                            'datetime': file_dt,
                            'path': path,
                            'base_prefix': base_prefix,
                            'files': {}
                        }

        # После того как нашли самые свежие версии, собираем ВСЕ файлы для них (архив, log, notes)
        for vm_id, info in latest_backups.items():
            path = info['path']
            base_prefix = info['base_prefix']
            files_attr = sftp.listdir_attr(path)
            for attr in files_attr:
                if attr.filename.startswith(base_prefix):
                    info['files'][attr.filename] = attr

    finally:
        sftp.close()

    return latest_backups


def download_backups(ssh_client, server_name, latest_backups, local_base_path):
    """Скачивает файлы бэкапов на локальный диск."""
    sftp = ssh_client.open_sftp()

    total_bytes = 0
    total_files = 0
    for vm_id, info in latest_backups.items():
        for fname, attr in info['files'].items():
            total_bytes += attr.st_size
            total_files += 1

    processed_bytes = 0
    processed_files = 0
    total_transferred_bytes = 0
    transfer_start_time = time.time()

    for vm_id, info in latest_backups.items():
        # Директория: server_name / vm_id / backup_date
        backup_date_str = info['datetime'].strftime('%Y-%m-%d_%H-%M-%S')
        local_dir = Path(local_base_path) / server_name / vm_id / backup_date_str
        local_dir.mkdir(parents=True, exist_ok=True)

        remote_path = info['path']

        for fname, attr in info['files'].items():
            processed_files += 1
            fsize = attr.st_size
            fmtime = attr.st_mtime

            local_file_path = local_dir / fname

            print(f"Файл: {Color.YELLOW}{fname}{Color.RESET}")

            # Проверка: существует ли и совпадает ли размер/время
            if local_file_path.exists():
                lstat = local_file_path.stat()
                if lstat.st_size == fsize and int(lstat.st_mtime) == int(fmtime):
                    print(f"{Color.GREEN}Уже существует (пропуск){Color.RESET}\n")
                    processed_bytes += fsize
                    continue

            # Скачивание файла
            remote_file = sftp.open(f"{remote_path}/{fname}", 'rb')
            remote_file.prefetch()

            f_start_time = time.time()
            transferred = 0
            step_counter = 0

            with open(local_file_path, "wb") as local_f:
                while transferred < fsize:
                    chunk_size = 1024 * 1024
                    step = min(chunk_size, fsize - transferred)
                    data = remote_file.read(step)
                    if not data:
                        break
                    local_f.write(data)

                    transferred += step
                    total_transferred_bytes += step
                    step_counter += 1

                    elapsed_f = time.time() - f_start_time
                    speed_f = transferred / max(0.001, elapsed_f)

                    elapsed_total = time.time() - transfer_start_time
                    avg_speed = total_transferred_bytes / max(0.001, elapsed_total)

                    # Вывод двух строк прогресса
                    sys.stdout.write(
                        f"\r{Color.CYAN}{get_spinner(step_counter)}{Color.RESET}\t\t"
                        f"{get_progress_bar(transferred, fsize, speed_f, f_start_time)}"
                        f"{Color.CLEAR_LINE}\n"
                        f"{get_total_progress_bar(processed_bytes + transferred, total_bytes, processed_files, total_files, avg_speed)}"
                        f"{Color.CLEAR_LINE}\033[A"
                    )
                    sys.stdout.flush()

            remote_file.close()
            processed_bytes += fsize

            # Устанавливаем время модификации
            os.utime(local_file_path, (fmtime, fmtime))

            elapsed_f = time.time() - f_start_time
            speed_f = transferred / max(0.001, elapsed_f)

            sys.stdout.write(
                f"\r{Color.GREEN}OK{Color.RESET}\t\t"
                f"{get_progress_bar(transferred, fsize, speed_f, f_start_time, finished=True)}"
                f"{Color.CLEAR_LINE}\n"
                f"{Color.CLEAR_LINE}\n"
            )
            sys.stdout.flush()

    sftp.close()
    return total_transferred_bytes


def rotate_local_backups(server_name, vm_config, local_base_path):
    """Выполняет ротацию локальных копий бэкапов согласно конфигурации."""
    server_path = Path(local_base_path) / server_name
    if not server_path.exists():
        return

    for vm_id, max_copies in vm_config.items():
        vm_path = server_path / vm_id
        if not vm_path.exists():
            continue

        # Получаем список всех папок бэкапов для этой VM, сортируем по имени (в имени дата)
        backups = sorted([d for d in vm_path.iterdir() if d.is_dir()])

        if max_copies == 0:
            print(f"{Color.YELLOW}Удаление всех копий для VM {vm_id} (лимит 0)...{Color.RESET}")
            shutil.rmtree(vm_path, ignore_errors=True)
            continue

        while len(backups) > max_copies:
            oldest = backups.pop(0)
            print(f"{Color.YELLOW}Ротация: удаление старой копии VM {vm_id}: {oldest.name}{Color.RESET}")
            shutil.rmtree(oldest, ignore_errors=True)


# ======================================================================
# Точка входа
# ======================================================================
def main():
    """Основная функция выполнения скрипта."""
    enable_windows_features()
    start_time = datetime.now()

    print(f"{Color.CYAN}{'='*70}")
    print(f" PVE SSH Backup VM Agent {Color.YELLOW}{VERSION}{Color.CYAN} | {Color.WHITE}{start_time.strftime('%d-%m-%Y %H:%M:%S')}{Color.RESET}")
    print(f" Управление: {Color.YELLOW}Ctrl+C{Color.RESET} для прерывания")
    print(f"{Color.CYAN}{'='*70}{Color.RESET}")

    config = load_config()
    timeout = getattr(config, "TIMEOUT", 30)
    total_downloaded = 0

    try:
        for server in config.SERVERS:
            server_name = server["name"]
            print(f"\n{Color.CYAN}>>> Обработка сервера: {Color.WHITE}{server_name} ({server['host']}){Color.RESET}")

            ssh = None
            try:
                ssh = get_ssh_client(server, timeout=timeout)
                print(f"Подключение: {Color.GREEN}OK{Color.RESET}")

                print(f"Чтение конфигурации хранилищ...")
                stdin, stdout, stderr = ssh.exec_command("cat /etc/pve/storage.cfg", timeout=timeout)
                storage_cfg_content = stdout.read().decode('utf-8')
                dump_paths = parse_storage_cfg(storage_cfg_content)

                if not dump_paths:
                    print(f"{Color.YELLOW}На сервере не найдено хранилищ с контентом 'backup'.{Color.RESET}")
                    continue

                vm_ids = list(server["vm_config"].keys())
                print(f"Поиск бэкапов для VM: {', '.join(vm_ids)}...")
                latest_backups = get_latest_backups(ssh, dump_paths, vm_ids)

                if not latest_backups:
                    print(f"{Color.YELLOW}Бэкапы для указанных VM не найдены.{Color.RESET}")
                else:
                    print(f"Найдено бэкапов для {len(latest_backups)} VM.")
                    downloaded = download_backups(ssh, server_name, latest_backups, server["local_base_path"])
                    total_downloaded += downloaded

                print(f"Выполнение ротации локальных копий...")
                rotate_local_backups(server_name, server["vm_config"], server["local_base_path"])
                print(f"{Color.GREEN}Сервер {server_name} обработан успешно.{Color.RESET}")

            except Exception as e:
                print(f"{Color.RED}Ошибка при работе с сервером {server_name}: {e}{Color.RESET}")
            finally:
                if ssh:
                    ssh.close()

    except KeyboardInterrupt:
        print(f"\n{Color.RED}Процесс прерван пользователем (Ctrl+C).{Color.RESET}")
    except Exception as e:
        print(f"\n{Color.RED}Критическая ошибка: {e}{Color.RESET}")
    finally:
        end_time = datetime.now()
        duration = end_time - start_time
        print(f"\n{Color.CYAN}{'='*70}")
        print(f" Завершено: {Color.WHITE}{end_time.strftime('%d-%m-%Y %H:%M:%S')}{Color.CYAN}")
        print(f" Общее время работы: {Color.WHITE}{str(duration).split('.')[0]}{Color.CYAN}")

        # Расчет средней скорости
        if duration.total_seconds() > 0:
            avg_speed_mbps = (total_downloaded / (1024 * 1024)) / duration.total_seconds()
            print(f" Средняя скорость: {Color.WHITE}{avg_speed_mbps:.1f} MB/s{Color.RESET}")

        print(f"{Color.CYAN}{'='*70}{Color.RESET}")


if __name__ == "__main__":
    main()
