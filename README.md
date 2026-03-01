# HEADCRACKER.AI

RAG-секретарь кандидатов: загрузка резюме, векторный поиск, генерация ответов, контроль доступа.

## Текущее состояние

- **Реализовано**: FLOW-01 (Query Flow), FLOW-02 (Ingestion Flow), FLOW-03 (Match with Vacancy) — по архитектуре.
- **Сервисы**: API Gateway (SYS-01), Conversation Manager (SYS-02), Retrieval (SYS-03), Generation (SYS-04), Embedding (SYS-05), Ingestion (SYS-06), Translation (SYS-15), Clearing (SYS-16), AuthZ (SYS-07), Telegram Bot (SYS-17); инфра: PostgreSQL, Qdrant, Redis, RabbitMQ, Prometheus, Grafana, Loki.
- **Контракты**: см. `architecture/CONTRACT_REGISTRY.md`. Навигация по системам и потокам — `architecture/KNOWLEDGE_MAP.md`.

---

## Микросервисы

### SYS-01: API Gateway
Единая точка входа: аутентификация, валидация (Pydantic), rate limiting, маршрутизация к Conversation Manager, Ingestion (через RabbitMQ), AuthZ. REST/JSON; порт хоста 8001→8000 в контейнере.

### SYS-02: Conversation Manager
Оркестрация диалогов: история (PostgreSQL), координация Clearing → Retrieval → Generation, кэш ответов (Redis). Реализует FLOW-01 и FLOW-03. Зависит от Clearing, Retrieval, Generation, AuthZ, Translation, PostgreSQL, Redis.

### SYS-03: Retrieval Service
Гибридный поиск (векторный + полнотекстовый), ранжирование и фильтрация. Контракт: `search(query, filters, top_k)` → чанки. Использует Embedding Service, Qdrant; опционально Document Storage.

### SYS-04: Generation Service
Генерация ответов и уточняющих вопросов через LLM (Ollama). Контракты: `generate(prompt)`, `generate_clarification(original_query, documents, history)`. Не хранит данные.

### SYS-05: Embedding Service
Векторные представления текста; кэш в Redis. Контракты: `embed(text)`, `embed_batch(texts)`. Зависит от Ollama, Redis.

### SYS-06: Ingestion Service
Загрузка, чанкинг, индексация резюме. Семантический парсинг (company_name, date_range, project_name, project_domain, role), поблочный перевод (general + по компаниям), версионирование. Принимает команды через RabbitMQ (`upload_document`), пишет в PostgreSQL и Qdrant. Зависит от Embedding, Translation, Qdrant, PostgreSQL, RabbitMQ.

### SYS-15: Translation Service
LLM-перевод (Ollama), ru↔en; кэш в Redis. HTTP `POST /translate` и очереди `translation_tasks` / `translation_results`. Используется в индексации и в FLOW-01.

### SYS-16: Clearing Service
Нормализация текста (пробелы, мусор), валидация (profanity, prompt injection). `POST /preprocess` → normalized, validation_result (OK/BLOCK), reasons. Используется в FLOW-01 до поиска и генерации.

### SYS-07: AuthZ Service
Проверка прав доступа по ролям. `check_access(user_id, doc_id, action)`. PostgreSQL, Redis (кэш).

### SYS-17: Telegram Bot
Telegram-интерфейс для кандидатов и HR. Регистрация, роли (candidate, recruiter, admin), загрузка резюме, RAG-запросы, match-vacancy. Зависит от API Gateway, PostgreSQL.

### Мониторинг (Prometheus, Grafana, Loki)

---

## Пользовательские сценарии

- **Вопрос по резюме (FLOW-01)**  
  Пользователь отправляет вопрос через API Gateway → создаётся запись диалога, задача в RabbitMQ → Conversation Manager: нормализация (Clearing), определение языка и при необходимости перевод (Translation), проверка кэша ответов → при промахе: декомпозиция подвопросов, Retrieval (Embedding + Qdrant), при низком score — уточняющий диалог или эскалация → Generation (Ollama) → постобработка и обратный перевод → сохранение в историю и кэш, ответ клиенту.

- **Загрузка резюме (FLOW-02)**  
  Клиент отправляет файл на `POST /documents/upload` (multipart: file, owner_id [, external_id]) → API Gateway публикует команду в RabbitMQ → Ingestion Service: транзакция в PostgreSQL, удаление старой версии документа, разбиение на секции (general + experience по компаниям), поблочный перевод через Translation Service, чанкинг с метаданными, Embedding → запись в Qdrant и PostgreSQL, публикация события. Статус задачи: `GET /documents/tasks/{task_id}/status`.

- **Соответствие вакансии (FLOW-03)**  
  HR отправляет `POST /match-vacancy` с телом `{ owner_id, vacancy_text, summary }` → Conversation Manager захватывает in-process lock → Retrieval по vacancy_text с фильтром owner_id → расчёт relevance_score и matched_snippets → при summary=true генерация отчёта (Generation) → ответ `[{ owner_id, relevance_score, matched_snippets, summary? }]`.

---

## Telegram Bot (SYS-17)

Telegram-бот для кандидатов и HR. Вызов API Gateway (documents/upload, chat, match-vacancy).

### Use cases

| Роль | Сценарий |
|------|----------|
| **Кандидат** | Регистрация (`/start`), выбор языка, загрузка резюме (.txt до 1 МБ), генерация токена для связи с HR |
| **Рекрутер** | Связь с кандидатом по токену (`/link`), вопросы по резюме кандидата (RAG), сопоставление резюме с вакансией (`/match`) |
| **Админ** | Переключение режима кандидат ↔ рекрутер (`/switch`), доступ ко всем функциям в зависимости от режима |

### Команды

| Команда | Доступ | Описание |
|---------|--------|----------|
| `/start` | все | Регистрация, выбор языка (ru/en), главное меню |
| `/lang [ru\|en]` | все | Смена языка интерфейса |
| `/help` | все | Справка по роли |
| `/update` | candidate, admin (candidate) | Запрос на загрузку резюме (.txt, макс. 1 МБ) |
| `/hash` | candidate, admin (candidate) | Генерация токена для связи с HR |
| `/link [токен]` | recruiter, admin (hr) | Связь с кандидатом по токену; без аргумента — ввод токена в следующем сообщении |
| `/match` | recruiter, admin (hr) | Сопоставление резюме привязанного кандидата с вакансией. После вызова — отправка текста вакансии или файла (doc/docx/pdf/txt до 5 МБ) |
| `/switch` | admin | Переключение режима candidate ↔ hr |
| `/ping` | все | Ответ `pong` (проверка) |

### Логика

- **Загрузка резюме**: `/update` → ожидание файла .txt → `POST /documents/upload` (owner_id = telegram_id) → polling статуса задачи.
- **RAG-вопросы (HR)**: HR, связанный с кандидатом (`/link`), отправляет текстовое сообщение → `POST /chat` (owner_id = dialog_owner_id) → polling ответа.
- **Match-vacancy**: `/match` → текст или файл вакансии → `POST /match-vacancy` → вывод relevance_score и matched_snippets.
- **Токены**: `/hash` создаёт одноразовый токен в таблице `hashes`. HR использует `/link <токен>` для привязки `dialog_owner_id` к кандидату.
- **Разрешения**: `ALLOWED_UPLOAD_USER_IDS` — список telegram_id, допущенных к загрузке резюме. Роли candidate/recruiter/admin задаются вручную в БД или через хэш-привязку.

### Конфигурация

- `TELEGRAM_BOT_TOKEN` — токен бота (обязательно).
- `BASE_API_URL` — URL API Gateway (например `http://api-gateway:8000`).
- `ALLOWED_UPLOAD_USER_IDS` — telegram_id через запятую для доступа candidate.
- `POSTGRES_*` — подключение к PostgreSQL.

---

## Утилита build.py

Сборка Docker-образов микросервисов (контекст — корень проекта, `Dockerfile.service`, копируются `shared/` и `services/<SVC>`).

- **Команды**: `base` — базовый образ; `service <name>` — один сервис; `all` — все сервисы; `list` — список сервисов; `up` — `docker-compose up -d` (опция `--gpu` для docker-compose.gpu.yml).
- **Пересборка**: `rebuild base | service <name> | all` — удаление старых образов, сборка, затем автоматически: `docker-compose up -d`, миграции PostgreSQL (`alembic upgrade head`). Опции: `--no-cache`, `--pull`, `--prune`, `--gpu`, `--erase` (очистка Redis, Qdrant, пользовательских таблиц PostgreSQL), `--run-tests` (pytest unit + e2e с покрытием), `--render-png` (конвертация `*.mmd` из корня в `diagrams/*.png` через mermaid-cli).

Примеры:
```bash
python build.py list
python build.py service api-gateway
python build.py rebuild all --no-cache --render-png
python build.py up --gpu
python build.py rebuild all --erase --run-tests --render-png
```

---

## Запуск

```bash
# Рекомендуется: скрипт проверяет API и автоматически решает, нужен ли Ollama
./scripts/compose-up.sh -d              # Linux/Mac
.\scripts\compose-up.ps1 -Detached     # Windows PowerShell

# С GPU (Ollama на GPU при fallback на local):
./scripts/compose-up.sh -d --gpu
.\scripts\compose-up.ps1 -Detached -Gpu

# Вручную (без проверки API):
docker compose up -d                           # только vendor (ollama не в проекте)
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up -d   # с Ollama

# API (хост): http://localhost:8001
# Загрузка резюме: POST http://localhost:8001/documents/upload (multipart: file, owner_id [, external_id])
# Статус задачи: GET http://localhost:8001/documents/tasks/{task_id}/status
```

Порты сервисов — в `docker-compose.yml` (API Gateway 8001→8000, Conversation Manager 8002→8000, Retrieval 8003→8000, Generation 8004→8000, Embedding 8005→8000, AuthZ 8007→8000, Translation 8008→8000).

### Выбор LLM-провайдера

| Режим | Описание |
|-------|----------|
| `LLM_PROVIDER=local` | Только Ollama. Запуск: `docker-compose --profile local up -d` |
| `LLM_PROVIDER=openai` | OpenAI API. `OPENAI_API_KEY` обязателен. При невалидном ключе → Ollama |
| `LLM_PROVIDER=deepseek` | DeepSeek API. `DEEPSEEK_API_KEY` обязателен. При невалидном ключе → Ollama |
| `LLM_PROVIDER=gemini` | Google Gemini API. `GEMINI_API_KEY` обязателен. При невалидном ключе → Ollama |
| `LLM_PROVIDER=auto` | `FAVORITE_LLM_PROVIDER` (openai/deepseek/gemini) сначала; при отсутствии ключей → Ollama |

**Embeddings:**
- **local**: `rag-embedding-service` + Ollama (модель `OLLAMA_EMBED_MODEL`)
- **vendor (OpenAI/DeepSeek/Gemini)**: `lightweight-embeddings` с моделью `bge-m3` (1024-dim, multilingual)
  - DeepSeek не имеет собственного embedding API → для него используется `lightweight-embeddings`
  - Основано на https://github.com/lh0x00/lightweight-embeddings
  - Модель настраивается через `LIGHTWEIGHT_EMBED_MODEL` в `.env`

Для cloud/VPS без GPU: задайте `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` или `GEMINI_API_KEY` в `.env`, `LLM_PROVIDER=auto` или имя провайдера, запускайте `docker-compose up -d` (без `--profile local` — Ollama не стартует).

**Важно при пересборке lightweight-embeddings:**
- Если модуль `sentence_transformers` не найден, выполните полную очистку:
  ```bash
  python build.py rebuild all --erase --gpu
  ```
- Это остановит контейнеры, удалит все образы проекта, очистит build cache и пересоберёт с нуля
- **Медленная загрузка PyTorch (из РФ):** по умолчанию torch ставится из PyPI (таймаут 10 мин). Если всё равно не успевает — скачайте CPU wheel вручную ([torch 2.6.0+cpu cp311 linux](https://download.pytorch.org/whl/cpu/torch-2.6.0%2Bcpu-cp311-cp311-linux_x86_64.whl)), положите в `services/lightweight-embeddings/wheels/`, в `.env` задайте `USE_OFFLINE_TORCH=1` и пересоберите образ

## Клиентская интеграция

- Базовый URL API (локально): `http://localhost:8001`.
- CORS: в api-gateway задаётся через `CORS_ORIGINS`. Пусто или `*` — все origins без credentials.
- При "Failed to fetch / URL scheme must be http or https": задать базовый URL API явно во фронте (например `VITE_API_URL`/`NEXT_PUBLIC_API_URL` = `http://localhost:8001`) и не вызывать fetch при пустом значении.

## Тесты

- E2E загрузки (FLOW-02): `pytest tests/test_e2e_ingestion_flow.py -v -s`. Перед запуском: миграция `scripts/initdb/02_add_chunks_metadata.sql`, подняты postgres, qdrant, embedding-service, translation-service. Подробно: `tests/README.md`, `tests/QUICK_START.md`.
- RAG pipeline: `tests/test_e2e_rag_pipeline.py`.

## Архитектура

- Системы и потоки: `architecture/KNOWLEDGE_MAP.md`.
- Контракты модулей: `architecture/CONTRACT_REGISTRY.md`.
- Индекс промптов: `architecture/PROMPT_INDEX_MAP.md`.
- Диаграммы: `SYSTEMS.mmd`, `FLOW-01.mmd`, `FLOW-02.mmd` (Mermaid).

Технологии: Python 3.11+, Ollama, Docker Compose, PostgreSQL, Qdrant, Redis, RabbitMQ, Prometheus/Grafana/Loki. Сквозной trace_id (X-Request-ID), структурированные логи (CTR-29), сборка через `Dockerfile.service` и `shared/tracing.py`.
