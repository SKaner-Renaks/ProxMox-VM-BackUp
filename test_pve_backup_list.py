import paramiko

# --- НАСТРОЙКИ ПОДКЛЮЧЕНИЯ ---
HOST = "192.168.1.100"
USER = "root"
PORT = 22
PASSWORD = "your_root_password"
PKEY_PATH = None
# ------------------------------

def get_ssh_client(host, user, password=None, pkey_path=None, port=22):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if pkey_path:
        private_key = paramiko.RSAKey.from_private_key_file(pkey_path)
        client.connect(hostname=host, port=port, username=user, pkey=private_key)
    else:
        client.connect(hostname=host, port=port, username=user, password=password)
    return client

def parse_storage_cfg(config_content):
    backup_storages = {}
    current_storage = None

    lines = config_content.strip().split('\n')

    for line in lines:
        if line.strip().startswith('#'):
            continue

        # Новая секция хранилища
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
                    'nodes': '',    # на каких узлах доступно
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

    valid = {}
    for s_id, info in backup_storages.items():
        # Пропускаем отключенные хранилища
        if info['disable']:
            continue
        # Пропускаем PBS
        if info['type'] == 'pbs':
            continue
        # Только хранилища с backup в content
        if 'backup' not in info['content']:
            continue

        valid[s_id] = {
            'type': info['type'],
            'path': info['path'],
            'nodes': info['nodes']
        }

    return valid

def list_backup_files(ssh_client, path):
    full_path = f"{path.rstrip('/')}/dump"
    cmd = f"ls -lh {full_path} 2>/dev/null"
    stdin, stdout, stderr = ssh_client.exec_command(cmd)
    files = stdout.read().decode('utf-8').strip()
    return files if files else "Файлы не найдены (директория пуста или отсутствует)."

def get_node_name(ssh_client):
    """Узнаёт имя текущего узла Proxmox"""
    cmd = "hostname"
    stdin, stdout, stderr = ssh_client.exec_command(cmd)
    return stdout.read().decode('utf-8').strip()

def main():
    print(f"Подключение к {HOST}...")
    try:
        ssh = get_ssh_client(HOST, USER, PASSWORD, PKEY_PATH, PORT)
    except Exception as e:
        print(f"Ошибка подключения: {e}")
        return

    # Узнаём имя узла
    current_node = get_node_name(ssh)
    print(f"Текущий узел: {current_node}")

    print("Чтение /etc/pve/storage.cfg...")
    stdin, stdout, stderr = ssh.exec_command("cat /etc/pve/storage.cfg")
    config_content = stdout.read().decode('utf-8')

    backup_storages = parse_storage_cfg(config_content)

    if not backup_storages:
        print("Нет доступных локальных хранилищ с backup.")
        ssh.close()
        return

    print("\nДоступные хранилища бэкапов на этом узле:")
    for s_id, info in backup_storages.items():
        # Проверяем, доступно ли хранилище на текущем узле
        nodes = info['nodes']
        if nodes and current_node not in nodes.split(','):
            print(f"  {s_id}: пропущено (только для узлов: {nodes})")
            continue

        print(f"\n[{s_id}] {info['path']}/dump")
        print("-" * 50)
        print(list_backup_files(ssh, info['path']))
        print("-" * 50)

    ssh.close()
    print("\nГотово.")

if __name__ == "__main__":
    main()