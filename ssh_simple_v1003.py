# -*- coding: utf-8 -*-
"""
Простой скрипт для ежедневного резервного копирования 1С-баз.
Находит на удалённом сервере самую свежую папку бэкапа,
скачивает её содержимое на локальный диск и поддерживает
не более 4 локальных копий (удаляет самую старую).
Визуализация: прогресс-бар, спиннер; защита от «зависания» консоли.
"""

import paramiko
import os
import sys
import time
import shutil
import ctypes
from datetime import datetime

VERSION = "1.0.0.3-simple"

class Color:
    """Набор ANSI-кодов для цветного вывода."""
    GREEN  = '\033[32m'
    RED    = '\033[31m'
    YELLOW = '\033[33m'
    CYAN   = '\033[36m'
    WHITE  = '\033[37m'
    RESET  = '\033[0m'
    CLEAR_LINE = '\033[K'

# ----------------------------------------------------------------------
#  НАСТРОЙКИ ПОДКЛЮЧЕНИЯ И ХРАНЕНИЯ
# ----------------------------------------------------------------------
CONFIG = {
    "host": "srv-1c",
    "username": "it-1c",
    "key_path": "C:/ARS/ssh/1c/private_simple",
    "remote_path": "/opt/pg_backup_db/",
    "local_base_path": r"D:\BackUp\1C\Base",
    "max_backups": 4
}

# ----------------------------------------------------------------------
#  ЗАЩИТА КОНСОЛИ ОТ ЗАВИСАНИЯ ПРИ ВЫДЕЛЕНИИ ТЕКСТА
# ----------------------------------------------------------------------
def enable_windows_features():
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        h_input  = kernel32.GetStdHandle(-10)
        h_output = kernel32.GetStdHandle(-11)
        kernel32.SetConsoleMode(h_output, 7)
        mode = ctypes.c_uint()
        kernel32.GetConsoleMode(h_input, ctypes.byref(mode))
        kernel32.SetConsoleMode(h_input, mode.value & ~0x0040)

# ----------------------------------------------------------------------
#  ВСПОМОГАТЕЛЬНЫЕ UI‑ФУНКЦИИ
# ----------------------------------------------------------------------
def format_time(seconds):
    """Преобразует секунды в формат ЧЧ:ММ:СС."""
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def get_spinner(step):
    spinner_chars = ['\\', '|', '/', '-']
    return spinner_chars[step % 4]

def get_progress_bar(current, total, speed_bps, start_time, finished=False):
    percent = int(100 * current / total) if total > 0 else 0
    bar_fill = int(20 * current // total) if total > 0 else 0
    bar = ("=" * max(0, bar_fill - 1) + ">").ljust(20, "-")

    speed_mbps = speed_bps / (1024 * 1024)
    current_gb = current / (1024 ** 3)
    total_gb   = total   / (1024 ** 3)

    if finished:
        elapsed = time.time() - start_time
        timer_str = f"ИТОГО: {format_time(elapsed)}"
    else:
        if speed_bps > 0:
            eta = (total - current) / speed_bps
        else:
            eta = 0
        timer_str = f"ЭТА: {format_time(eta)}"

    return (f"[{bar}] {percent}% | "
            f"{current_gb:.1f}/{total_gb:.1f} GB | "
            f"{speed_mbps:.1f} MB/s | {timer_str}")

def get_total_progress_bar(current_bytes, total_bytes, current_file_idx, total_files, avg_speed_bps):
    """Генерирует общий прогресс-бар (50 символов)."""
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
            f"{current_gb:.1f}/{total_gb:.1f} GB | ЭТА: {format_time(eta)}")

# ----------------------------------------------------------------------
#  ОСНОВНАЯ ЛОГИКА
# ----------------------------------------------------------------------
def main():
    enable_windows_features()
    start_time = datetime.now()
    print(f"{Color.CYAN}{'='*60}\n"
          f" Backup Agent {Color.YELLOW}v.{VERSION}{Color.CYAN} | {Color.WHITE}{start_time.strftime('%H:%M:%S %d-%m-%Y')}{Color.CYAN}\n"
          f" Режим: простой, загрузка последней копии 1С\n"
          f"{'='*60}{Color.RESET}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # --- 1. ПОДКЛЮЧЕНИЕ К СЕРВЕРУ ---
        print(f"Подключение к {Color.WHITE}{CONFIG['host']}{Color.RESET}...", end='', flush=True)
        ssh.connect(
            hostname=CONFIG["host"],
            username=CONFIG["username"],
            pkey=paramiko.RSAKey.from_private_key_file(CONFIG["key_path"])
        )
        print(f" {Color.GREEN}OK{Color.RESET}")
        sftp = ssh.open_sftp()

        # --- 2. ПОИСК САМОЙ СВЕЖЕЙ ПАПКИ БЭКАПА ---
        # Используем ls -1d */ чтобы получать только директории
        stdin, stdout, _ = ssh.exec_command(f"cd {CONFIG['remote_path']} && ls -1d */")
        folders = sorted([
            f.strip('/') for f in stdout.read().decode().splitlines()
            if "-" in f
        ])
        if not folders:
            raise FileNotFoundError("На удалённом сервере не найдено папок бэкапов.")

        latest_folder = folders[-1]
        remote_full_path = f"{CONFIG['remote_path']}{latest_folder}/"

        # --- 3. ПОЛУЧЕНИЕ СПИСКА ФАЙЛОВ И ОБЩЕГО РАЗМЕРА ---
        files_attr = sftp.listdir_attr(remote_full_path)
        total_bytes = sum(attr.st_size for attr in files_attr)
        total_files = len(files_attr)
        print(f"На сервере найден бэкап: {Color.YELLOW}{latest_folder}{Color.RESET}"
              f" ({total_bytes / (1024**3):.2f} GB, {total_files} файлов)")

        # --- 4. ПОДГОТОВКА ЛОКАЛЬНОГО КАТАЛОГА ---
        local_target = os.path.join(CONFIG["local_base_path"], latest_folder)
        if not os.path.exists(local_target):
            os.makedirs(local_target)

        # --- 5. ЗАГРУЗКА ФАЙЛОВ С ПРОГРЕСС-БАРОМ ---
        step_counter = 0
        total_processed_bytes = 0
        total_transferred_bytes = 0
        transfer_start_time = time.time()
        print()

        for idx, attr in enumerate(files_attr, 1):
            fname = attr.filename
            fsize = attr.st_size
            fmtime = attr.st_mtime

            print(f"Файл: {Color.YELLOW}{fname}{Color.RESET}")
            local_file_path = os.path.join(local_target, fname)

            # Проверка: существует ли файл и совпадает ли размер/время
            if os.path.exists(local_file_path):
                lstat = os.stat(local_file_path)
                if lstat.st_size == fsize and int(lstat.st_mtime) == int(fmtime):
                    print(f"{Color.GREEN}Уже существует (пропуск){Color.RESET}\n")
                    total_processed_bytes += fsize
                    sys.stdout.flush()
                    continue

            remote_file = sftp.open(remote_full_path + fname, 'rb')
            remote_file.prefetch()

            f_start_time = time.time()
            transferred = 0
            speed_f = 0

            with open(local_file_path, "wb") as local_f:
                while transferred < fsize:
                    chunk_size = 1024 * 1024
                    step = min(chunk_size, fsize - transferred)
                    local_f.write(remote_file.read(step))

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
                        f"{get_total_progress_bar(total_processed_bytes + transferred, total_bytes, idx, total_files, avg_speed)}"
                        f"{Color.CLEAR_LINE}\033[A"
                    )
                    sys.stdout.flush()
            remote_file.close()

            total_processed_bytes += fsize
            # Устанавливаем время модификации как на сервере
            os.utime(local_file_path, (fmtime, fmtime))

            elapsed_total = time.time() - transfer_start_time
            avg_speed = total_transferred_bytes / max(0.001, elapsed_total)

            sys.stdout.write(
                f"\r{Color.GREEN}OK{Color.RESET}\t\t"
                f"{get_progress_bar(transferred, fsize, speed_f, f_start_time, finished=True)}"
                f"{Color.CLEAR_LINE}\n"
                f"{Color.CLEAR_LINE}\n"
            )
            sys.stdout.flush()

        # --- 6. УДАЛЕНИЕ СТАРЫХ ЛОКАЛЬНЫХ КОПИЙ ---
        existing_backups = sorted([
            d for d in os.listdir(CONFIG["local_base_path"])
            if os.path.isdir(os.path.join(CONFIG["local_base_path"], d))
            and "-" in d
        ])
        while len(existing_backups) > CONFIG["max_backups"]:
            oldest = existing_backups[0]
            oldest_path = os.path.join(CONFIG["local_base_path"], oldest)
            print(f"{Color.YELLOW}Удаляю старую копию: {oldest}{Color.RESET}")
            shutil.rmtree(oldest_path, ignore_errors=True)
            existing_backups.pop(0)

        # --- 7. ФИНАЛЬНЫЙ ОТЧЁТ ---
        elapsed_total = datetime.now() - start_time
        total_duration_sec = elapsed_total.total_seconds()
        
        # Средняя скорость по реально скачанным байтам
        real_avg_speed_mbps = 0
        # Используем время, затраченное именно на трансфер (transfer_start_time был засечен перед циклом загрузки)
        # Однако в ТЗ не сказано вычитать время подключения. 
        # "только по реально скачанным байтам" обычно означает (байты / время_скачивания).
        # Но для "Время выполнения: 0:16:43 (ср. 62.3 MB/s)" логичнее брать общее время или время трансфера.
        # Учитывая, что transfer_start_time у нас есть, используем время с начала загрузки.
        transfer_duration = time.time() - transfer_start_time
        if transfer_duration > 0:
            real_avg_speed_mbps = (total_transferred_bytes / (1024 * 1024)) / transfer_duration

        print(f"\n{Color.CYAN}{'='*60}\n"
              f" ИТОГО: папка {Color.YELLOW}{latest_folder}{Color.CYAN} загружена\n"
              f" Время выполнения: {Color.WHITE}{str(elapsed_total).split('.')[0]} (ср. {real_avg_speed_mbps:.1f} MB/s){Color.CYAN}\n"
              f" Статус: {Color.GREEN}SUCCESS{Color.CYAN}\n"
              f"{'='*60}{Color.RESET}")

    except Exception as e:
        print(f"\n{Color.RED}Критический сбой: {e}{Color.RESET}")
    finally:
        ssh.close()

if __name__ == "__main__":
    main()
