# Инициализация схемы PostgreSQL

Скрипт `01_ingestion_schema.sql` создаёт таблицы FLOW-02: `documents`, `chunks`, `ingestion_jobs`.

Он автоматически выполняется при **первом** запуске контейнера Postgres (когда том данных пустой), т.к. смонтирован в `/docker-entrypoint-initdb.d/`.

Если контейнер Postgres уже существовал и таблицы не созданы (ошибка `relation "documents" does not exist`), выполните один из вариантов:

```bash
# Вариант 1: применить SQL вручную
docker exec -i rag-postgres psql -U assistant_user -d assistant < scripts/initdb/01_ingestion_schema.sql

# Вариант 2: миграции Alembic (из корня проекта)
python build.py migrate
```

После этого перезапустите сервисы, использующие БД (ingestion-service, api-gateway и т.д.).
