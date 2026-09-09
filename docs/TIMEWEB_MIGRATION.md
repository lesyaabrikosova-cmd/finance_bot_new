# Безопасный перенос «Богатого Алхимика» на Timeweb Cloud Server

Эта инструкция рассчитана на текущую SQLite-версию. Railway не отключается,
пока тестовая копия на Timeweb не прошла проверку.

## 1. Создание сервера

В Timeweb Cloud создайте облачный сервер Ubuntu 24.04 LTS в ближайшем регионе.
Для первого запуска достаточно минимальной конфигурации с 1 CPU и 1–2 ГБ RAM.
Добавьте свой SSH-ключ. Вход только по паролю менее безопасен.

## 2. Подготовка сервера

Подключитесь по SSH и выполните:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

Завершите SSH-сессию и подключитесь снова, чтобы применилось членство в группе
`docker`. Затем:

```bash
sudo mkdir -p /opt/rich-alchemist
sudo chown "$USER":"$USER" /opt/rich-alchemist
cd /opt/rich-alchemist
git clone https://github.com/lesyaabrikosova-cmd/finance_bot_new.git app
cd app
```

Для приватного репозитория используйте GitHub Deploy Key с правом только на
чтение. Не сохраняйте пароль GitHub в командной строке.

## 3. Секреты

Создайте файл непосредственно на сервере:

```bash
cd /opt/rich-alchemist/app
cp .env.production.example .env.production
nano .env.production
chmod 600 .env.production
```

Вставьте тестовый `BOT_TOKEN`. Настоящий токен на этом этапе использовать
нельзя: Railway пока работает.

## 4. Тестовый запуск

```bash
docker compose -f compose.timeweb.yml build
docker compose -f compose.timeweb.yml up -d
docker compose -f compose.timeweb.yml ps
docker compose -f compose.timeweb.yml logs --tail=100 bot
curl http://127.0.0.1:8080/health
```

Ожидаемый ответ healthcheck:

```json
{"status":"ok"}
```

После перезапуска данные тестового профиля должны сохраниться:

```bash
docker compose -f compose.timeweb.yml restart bot
```

## 5. Резервная копия базы

Согласованная копия создаётся через SQLite Backup API:

```bash
docker compose -f compose.timeweb.yml exec bot \
  python scripts/backup_sqlite.py --source /data/allocator.db --destination /data/backups
docker cp rich-alchemist-bot:/data/backups ./backups
```

Файл появится в `/opt/rich-alchemist/app/backups`. Скопируйте его также на
другое устройство или в объектное хранилище: копия на том же сервере не
защищает от потери всего сервера.

## 6. Перенос рабочей базы

1. Получите согласованную копию `allocator.db` из действующего Railway.
2. Проверьте её локально командой `PRAGMA integrity_check` или скриптом backup.
3. Остановите бот на Railway.
4. Убедитесь, что в логах Railway больше нет polling-процесса.
5. Остановите тестовый контейнер Timeweb.
6. Скопируйте рабочую базу в постоянный Docker-том:

   ```bash
   docker compose -f compose.timeweb.yml create bot
   docker cp allocator.db rich-alchemist-bot:/data/allocator.db
   docker compose -f compose.timeweb.yml run --rm --user root bot \
     chown allocator:allocator /data/allocator.db
   ```

7. Убедитесь, что файл появился: `docker compose -f compose.timeweb.yml run --rm bot ls -l /data`.
8. Замените тестовый токен настоящим в `.env.production`.
9. Запустите контейнер Timeweb.

Одновременно держать два polling-процесса с одним токеном нельзя.

## 7. Проверка после переключения

Проверьте существующий профиль, главное меню, запись поступления, налоги,
долги и повторный перезапуск. Railway не удаляйте несколько дней: держите его
остановленным как возможность быстрого возврата.

## 8. Обновления

До настройки автоматического деплоя обновляйте вручную:

```bash
cd /opt/rich-alchemist/app
git pull --ff-only origin main
docker compose -f compose.timeweb.yml build
docker compose -f compose.timeweb.yml up -d
docker compose -f compose.timeweb.yml logs --tail=100 bot
```

Перед каждым обновлением сначала создавайте резервную копию базы.

## 9. Следующая архитектурная ступень

Перед запуском Telegram Mini App база будет отдельно перенесена в PostgreSQL.
Python-движок и накопленные данные сохранятся, а бот и Mini App будут работать
с одной серверной базой через API.
