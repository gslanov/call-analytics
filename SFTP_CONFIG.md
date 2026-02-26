# SFTP Server Configuration — call-analytics

**Дата настройки**: 2026-02-26
**Статус**: ✅ LIVE и готов к работе
**ОС**: Ubuntu 24.04 LTS на 23.94.143.122

---

## Обзор

SFTP сервер настроен на основном сервере приложения (23.94.143.122) для безопасной загрузки записей разговоров от МАНГО PBX. Используется **безопасный режим chroot** — пользователь SFTP ограничен только своей рабочей директорией.

---

## Реквизиты подключения

| Параметр | Значение |
|----------|----------|
| **Хост** | `23.94.143.122` |
| **Порт** | `22` (SSH/SFTP стандартный) |
| **Протокол** | SFTP (SSH File Transfer Protocol) |
| **Пользователь** | `mango_sftp` |
| **Пароль** | `Mango@SFTP2024!` |
| **Рабочая директория** | `/uploads` (chroot в `/app/call-analytics/data/mango_sftp`) |

---

## Конфигурация SSH (/etc/ssh/sshd_config)

```bash
Match User mango_sftp
    X11Forwarding no
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitTunnel no
    ChrootDirectory /app/call-analytics/data/mango_sftp
    ForceCommand internal-sftp -d /uploads
```

**Параметры:**
- `ChrootDirectory` — пользователь видит только `/app/call-analytics/data/mango_sftp` как корневую папку
- `ForceCommand internal-sftp -d /uploads` — автоматически перейти в `/uploads` при подключении
- `X11Forwarding no` — отключить X11 для безопасности
- `AllowTcpForwarding no` — запретить туннелирование
- `AllowAgentForwarding no` — запретить SSH agent forwarding

---

## Структура директорий

```
/app/call-analytics/data/
├── mango_sftp/                    ← ChrootDirectory
│   ├── uploads/                   ← Рабочая папка (автоматический вход)
│   │   └── call_recordings_*.wav  ← Записи разговоров
│   └── ...
├── audio/                         ← Исходные загруженные файлы
├── mango_sync/                    ← Синхронизированные файлы
└── db/                            ← БД PostgreSQL
```

**Права доступа:**
```
mango_sftp/ (root:root 755)       ← chroot должен принадлежать root
└── uploads/ (mango_sftp:mango_sftp 755) ← рабочая папка
```

---

## Подключение к SFTP серверу

### Через командную строку (Linux/Mac)

```bash
sftp -P 22 mango_sftp@23.94.143.122

# Команды внутри SFTP:
ls                    # Список файлов в текущей папке
cd uploads            # Перейти в uploads
put call_recording.wav    # Загрузить файл на сервер
get recording.wav     # Скачать файл с сервера
quit                  # Выход
```

### Через FileZilla (Windows/Mac/Linux)

1. Откройте FileZilla
2. File → Site Manager
3. New Site:
   - **Protocol**: SFTP - SSH File Transfer Protocol
   - **Host**: `23.94.143.122`
   - **Port**: `22`
   - **User**: `mango_sftp`
   - **Password**: `Mango@SFTP2024!`
4. Connect

### Через Python

```python
import paramiko

def upload_to_sftp(local_file, remote_filename):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('23.94.143.122', username='mango_sftp', password='Mango@SFTP2024!')

    sftp = ssh.open_sftp()
    sftp.put(local_file, f'/uploads/{remote_filename}')
    sftp.close()
    ssh.close()

    print(f"✅ Uploaded {remote_filename}")

# Использование
upload_to_sftp('/path/to/call_recording.wav', 'call_20260226_001.wav')
```

### Через Node.js

```javascript
const SSHClient = require('ssh2');
const fs = require('fs');

const client = new SSHClient();
client.connect({
    host: '23.94.143.122',
    username: 'mango_sftp',
    password: 'Mango@SFTP2024!',
});

client.on('ready', function() {
    client.sftp(function(err, sftp) {
        if (err) throw err;

        const readStream = fs.createReadStream('/path/to/call_recording.wav');
        const writeStream = sftp.createWriteStream('/uploads/call_20260226_001.wav');

        writeStream.on('close', function() {
            console.log('✅ File uploaded');
            client.end();
        });

        readStream.pipe(writeStream);
    });
});
```

---

## Конфигурация в приложении

### Environment Variables (.env)

```bash
# SFTP Configuration for MANGO Recordings
MANGO_FTP_HOST=23.94.143.122
MANGO_FTP_PORT=22
MANGO_FTP_USER=mango_sftp
MANGO_FTP_PASSWORD=Mango@SFTP2024!
MANGO_FTP_TYPE=sftp
MANGO_FTP_PATH=/uploads
```

### Docker Compose

MANGO sync сервис использует эти переменные окружения и синхронизирует файлы по расписанию:
- **Ежедневно** в 00:00 UTC
- **Fallback**: каждые 6 часов если основное синхро не выполнено

```yaml
mango-sync:
    image: call-analytics-mango-sync:latest
    container_name: call-analytics-mango-sync
    environment:
        - MANGO_FTP_HOST=${MANGO_FTP_HOST}
        - MANGO_FTP_PORT=${MANGO_FTP_PORT:-22}
        - MANGO_FTP_USER=${MANGO_FTP_USER}
        - MANGO_FTP_PASSWORD=${MANGO_FTP_PASSWORD}
        - MANGO_FTP_TYPE=${MANGO_FTP_TYPE:-sftp}
        - MANGO_FTP_PATH=${MANGO_FTP_PATH:-/uploads}
    volumes:
        - ./data/mango_sync:/app/mango_sync
        - ./data/uploads:/app/uploads
```

---

## Проверка работы

### 1. Проверить SSH подключение

```bash
ssh -v mango_sftp@23.94.143.122

# Ожидаемый результат:
# ...
# debug1: Sending SSH2_FXP_INIT
# ...
# Connection closed by remote host
# (это нормально - принудительно SFTP, не shell)
```

### 2. Проверить SFTP подключение

```bash
echo "ls" | sftp mango_sftp@23.94.143.122

# Ожидаемый результат:
# Changing to /uploads
# sftp> ls
# (пусто или список файлов)
```

### 3. Проверить логи mango-sync

```bash
docker-compose logs -f mango-sync

# Ожидаемый результат:
# 2026-02-26 13:15:00 INFO: Connected to SFTP server
# 2026-02-26 13:15:01 INFO: Sync completed: 0 files
```

### 4. Тестовая загрузка файла

```bash
# Локально
echo "Test audio content" > test_call.wav

# На сервер
sftp mango_sftp@23.94.143.122 << EOF
put test_call.wav
ls -la
quit
EOF

# Проверить на сервере
ssh root@23.94.143.122 "ls -la /app/call-analytics/data/mango_sftp/uploads/"
```

---

## Безопасность

### ✅ Реализованные меры безопасности

1. **Chroot окружение** — пользователь SFTP не может выйти за пределы `/app/call-analytics/data/mango_sftp`
2. **Запрещен shell доступ** — пользователь не может выполнять команды через SSH
3. **Запрещено туннелирование** — TCP forwarding и SSH agent forwarding отключены
4. **Только SFTP** — `ForceCommand internal-sftp` означает, что доступно только файловое хранилище
5. **Аутентификация по паролю** — требуется пароль для подключения
6. **Разделение директорий** — ChrootDirectory принадлежит root:root с 755 правами

### 🔄 Рекомендации для production

```bash
# 1. Смените пароль на более сложный
ssh root@23.94.143.122
passwd mango_sftp

# 2. Включите SSH ключи вместо пароля
ssh-copy-id -i ~/.ssh/id_rsa.pub mango_sftp@23.94.143.122

# 3. Отключите пароли и используйте только ключи в sshd_config:
#    PasswordAuthentication no
#    PubkeyAuthentication yes

# 4. Измените стандартный порт SSH (не 22):
#    Port 2222
#    Match User mango_sftp
#        Port 2222

# 5. Настройте fail2ban для защиты от brute-force
sudo apt install fail2ban
sudo systemctl enable fail2ban
```

---

## Troubleshooting

### ❌ "Permission denied (publickey,password)"

**Причина:** Пользователь не создан или пароль неверный.

**Решение:**
```bash
ssh root@23.94.143.122
# Переустановить пароль
passwd mango_sftp
# Или создать заново пользователя
useradd -s /sbin/nologin -m mango_sftp
```

### ❌ "Received disconnect from 23.94.143.122 port 22:11: Bye Bye [preauth]"

**Причина:** SSH конфиг некорректен.

**Решение:**
```bash
ssh root@23.94.143.122
sshd -t  # Проверить синтаксис
systemctl restart ssh
```

### ❌ "No such file or directory" при загрузке файлов

**Причина:** Неверный путь (забыли `/uploads/`)

**Решение:**
```bash
sftp mango_sftp@23.94.143.122
cd uploads  # chroot автоматически перенаправляет сюда
put myfile.wav
```

### ❌ "Read-only file system"

**Причина:** ChrootDirectory неправильно настроен (должен быть root:root)

**Решение:**
```bash
ssh root@23.94.143.122
chown root:root /app/call-analytics/data/mango_sftp
chmod 755 /app/call-analytics/data/mango_sftp
systemctl restart ssh
```

---

## Статистика и мониторинг

### Проверить размер загруженных файлов

```bash
ssh root@23.94.143.122 "du -sh /app/call-analytics/data/mango_sftp/uploads/"
```

### Архивировать старые записи

```bash
ssh root@23.94.143.122 << 'EOF'
cd /app/call-analytics/data/mango_sftp/uploads/
find . -mtime +30 -name "*.wav" -exec tar -czf ../archive_$(date +%Y%m%d).tar.gz {} \;
EOF
```

### Очистить место на диске

```bash
ssh root@23.94.143.122 << 'EOF'
cd /app/call-analytics/data/mango_sftp/uploads/
# Удалить файлы старше 90 дней
find . -mtime +90 -name "*.wav" -delete
EOF
```

---

## Связанные документы

- [HANDOFF.md](./HANDOFF.md) — Статус развертывания приложения
- [ARCHITECTURE.md](./ARCHITECTURE.md) — Архитектура системы
- [docker-compose.yml](./docker-compose.yml) — Конфигурация контейнеров
- [Инструкция по настройке FTP-сервера](./docs/FTP_SETUP_GUIDE.md) (исходная документация от МАНГО)

---

**Статус**: ✅ ГОТОВО
**Версия**: 1.0.0
**Последнее обновление**: 2026-02-26 13:14 UTC
