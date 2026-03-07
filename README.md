# wulf-grivder-v4 -> HEADCRACKER.AI -> Кандидат

**RAG-секретарь для кандидатов и HR:** загрузка резюме, векторный поиск, генерация ответов, сопоставление с вакансиями, контроль доступа.

---

## 📋 Оглавление

- [Как это работает](#-как-это-работает)
- [Развёртывание на VPS (Docker + build.py)](#-развёртывание-на-vps-docker--buildpy)
- [Микросервисы и потоки данных](#-микросервисы-и-потоки-данных)
- [Структура проекта](#-структура-проекта)
- [Запуск и конфигурация](#-запуск-и-конфигурация)
- [Клиентская интеграция и тесты](#-клиентская-интеграция-и-тесты)

---

## 🔄 Как это работает

Система построена как набор **микросервисов** в Docker. Единая точка входа — **API Gateway** (порт 8001 на хосте). Все длительные операции (индексация резюме) идут через **RabbitMQ**; диалоги и RAG-запросы оркестрирует **Conversation Manager**.

| Поток | Описание |
|-------|----------|
| **FLOW-01** | Пользователь задаёт вопрос → нормализация (Clearing) → перевод при необходимости (Translation) → кэш ответов (Redis) → при промахе: Retrieval (Embedding + Qdrant) → Generation (LLM) → обратный перевод → ответ. |
| **FLOW-02** | Загрузка резюме: `POST /documents/upload` → очередь RabbitMQ → Ingestion: парсинг секций, поблочный перевод, чанкинг, эмбеддинги → Qdrant + PostgreSQL. |
| **FLOW-03** | Match с вакансией: `POST /match-vacancy` → Conversation Manager → Retrieval по `vacancy_text` с фильтром `owner_id` → relevance_score, matched_snippets, опционально summary (Generation). |
| **FLOW-04** | Сопроводительное письмо: `POST /cover-letter` → RAG по документам кандидата + генерация Executive Summary (PR-06). |
| **FLOW-05** | Конвертация резюме: `POST /resume/convert` (файл или raw_text) → Resume Converter → структурированный текст для последующей загрузки (FLOW-02). |
| **FLOW-06** | Telegram Bot: регистрация, роли (candidate/recruiter/admin), загрузка резюме, RAG, match, cover-letter через UI Client и API Gateway. |

**Инфраструктура:** PostgreSQL (история, документы, политики, users), Qdrant (векторный поиск), Redis (кэш эмбеддингов и ответов), RabbitMQ (очереди), Prometheus/Grafana/Loki (метрики и логи), сквозной `trace_id` (X-Request-ID).

---

## 🚀 Развёртывание на VPS (Docker + build.py)

Система **рассчитана на запуск на VPS в Docker**. Сборка и запуск выполняются через утилиту **`build.py`** в корне проекта.

### Что делает build.py

- Собирает **базовый образ** (`Dockerfile.base`) и образы **микросервисов** (контекст — корень проекта; в образ копируются `shared/` и `services/<SVC>`, используется общий `Dockerfile.service`).
- После **пересборки** (`rebuild`) автоматически: поднимает контейнеры (`docker-compose up -d`), применяет миграции PostgreSQL (`alembic upgrade head`), при опциях — очищает данные, запускает тесты, рендерит Mermaid-диаграммы в PNG.
- Учитывает наличие валидного LLM API (OpenAI/DeepSeek/Gemini): при валидном ключе подключается `lightweight-embeddings`, иначе — Ollama + `embedding-service`.
- Генерирует **Nginx IP whitelist** из `.env` (`IP_ADDR_WHITELIST`) для ограничения доступа на VPS.

### Основные команды

```bash
# Список сервисов
python build.py list

# Сборка базового образа и одного сервиса
python build.py base
python build.py service api-gateway

# Сборка всех сервисов
python build.py all

# Пересборка всего с нуля (без кэша), запуск контейнеров, миграции
python build.py rebuild all --no-cache

# Пересборка + очистка Redis/Qdrant/PostgreSQL + тесты + рендер диаграмм
python build.py rebuild all --erase-db --run-tests --render-png

# Только поднять контейнеры (без пересборки)
python build.py up

# С GPU (Ollama на GPU при fallback на local)
python build.py up --gpu
```

### Опции rebuild

| Опция | Описание |
|-------|----------|
| `--no-cache` | Сборка без кэша Docker |
| `--pull` | Подтянуть свежие базовые образы |
| `--prune` | Очистить неиспользуемые ресурсы Docker |
| `--gpu` | Подключить `docker-compose.gpu.yml` |
| `--erase-db` | Очистить Redis, Qdrant, пользовательские таблицы PostgreSQL |
| `--total-erase` | Полная очистка: контейнеры, образы проекта, build cache + erase-db |
| `--run-tests` | Запустить тесты через контейнер test-runner |
| `--render-png` | Конвертировать `*.mmd` из корня в `diagrams/*.png` (mermaid-cli) |

---

## 🧩 Микросервисы и потоки данных

### Краткая таблица сервисов

| Сервис | Назначение | Порт (хост→контейнер) |
|--------|------------|------------------------|
| **SYS-01 API Gateway** | Единая точка входа: аутентификация, валидация (Pydantic), rate limiting, маршрутизация к Conversation Manager, Ingestion (RabbitMQ), AuthZ. REST/JSON. | 8001→8000 |
| **SYS-02 Conversation Manager** | Оркестрация диалогов: история (PostgreSQL), координация Clearing → Retrieval → Generation, кэш ответов (Redis). Реализует FLOW-01, FLOW-03, FLOW-04. | 8002→8000 |
| **SYS-03 Retrieval Service** | Гибридный поиск (векторный + полнотекстовый), ранжирование. Контракт: `search(query, filters, top_k)` → чанки. Embedding Service + Qdrant. | 8003→8000 |
| **SYS-04 Generation Service** | Генерация ответов и уточняющих вопросов через LLM (Ollama/OpenAI/DeepSeek/Gemini). `generate(prompt)`, `generate_clarification(...)`. | 8004→8000 |
| **SYS-05 Embedding Service** | Векторные представления текста; кэш в Redis. `embed(text)`, `embed_batch(texts)`. Ollama или lightweight-embeddings (bge-m3). | 8005→8000 |
| **SYS-06 Ingestion Service** | Загрузка, чанкинг, индексация резюме. Семантический парсинг, поблочный перевод, версионирование. Очередь RabbitMQ → PostgreSQL + Qdrant. | — |
| **SYS-15 Translation Service** | LLM-перевод ru↔en; кэш Redis. HTTP `POST /translate`, очереди. Используется в индексации и FLOW-01. | 8008→8000 |
| **SYS-16 Clearing Service** | Нормализация текста, валидация (profanity, prompt injection). `POST /preprocess` → normalized, validation_result. | — |
| **SYS-07 AuthZ Service** | Проверка прав: `check_access(user_id, doc_id, action)`. PostgreSQL, Redis (кэш). | 8007→8000 |
| **SYS-17 Telegram Bot** | Интерфейс для кандидатов и HR: регистрация, роли, загрузка резюме, RAG, match-vacancy, cover-letter. Зависит от API Gateway, UI Client, PostgreSQL. | — |
| **SYS-18 Resume Converter** | Конвертация резюме (hh.ru и др.) в канонический структурированный текст. `POST /convert`, `POST /convert/raw`. Celery + RabbitMQ или синхронно. | — |

### Инфраструктура

- **PostgreSQL** — история диалогов, документы, чанки, политики, users (Telegram), hashes.
- **Qdrant** — векторное хранилище для поиска по эмбеддингам.
- **Redis** — кэш эмбеддингов, кэш ответов RAG, сессии.
- **RabbitMQ** — очереди: загрузка документов, конвертация резюме, переводы.
- **Prometheus / Grafana / Loki** — метрики и логи.
- **Nginx** — единая точка входа на VPS (порт 80), IP whitelist из `build.py generate-nginx-whitelist`.

### Схема потоков (упрощённо)

```
[Клиент] → [Nginx] → [API Gateway] → [Conversation Manager] → Clearing → Retrieval → Generation
                ↓                           ↓
           [RabbitMQ] ← upload_document      Redis (кэш ответов)
                ↓
           [Ingestion] → Translation → Embedding → Qdrant + PostgreSQL
```

---

## 📁 Структура проекта

### Модули и каталоги

```
wulf_grinder_arch/
├── build.py                 # Сборка образов и запуск (base, service, all, rebuild, up)
├── docker-compose.yml       # Базовый состав (инфра + сервисы)
├── docker-compose.ollama.yml / docker-compose.gpu.yml / docker-compose.vendor-embeddings.yml
├── Dockerfile.base          # Базовый образ
├── Dockerfile.service       # Общий Dockerfile для сервисов (shared + services/<SVC>)
│
├── architecture/           # Архитектурные индексы (навигация, контракты, промпты)
│   ├── KNOWLEDGE_MAP.md     # Системы (SYS-*), потоки (FLOW-*), клиентская интеграция
│   ├── CONTRACT_REGISTRY.md # Контракты между модулями (CTR-*)
│   └── PROMPT_INDEX_MAP.md  # Индекс промптов (PR-*)
│
├── services/                # Микросервисы (каждая папка — один сервис)
│   ├── api-gateway/         # SYS-01: маршрутизация, валидация, прокси
│   ├── conversation-manager/ # SYS-02: оркестрация RAG, история, match-vacancy, cover-letter
│   ├── retrieval-service/  # SYS-03: поиск по Qdrant + Embedding
│   ├── generation-service/ # SYS-04: LLM-генерация
│   ├── embedding-service/  # SYS-05: эмбеддинги (Ollama)
│   ├── lightweight-embeddings/ # Альтернатива SYS-05: bge-m3 (vendor, без GPU)
│   ├── ingestion-service/  # SYS-06: загрузка и индексация резюме
│   ├── translation-service/ # SYS-15: перевод ru↔en
│   ├── clearing-service/   # SYS-16: нормализация и валидация
│   ├── authz-service/      # SYS-07: проверка доступа
│   ├── telegram-bot/       # SYS-17: Telegram-интерфейс
│   ├── resume-converter/   # SYS-18: конвертация резюме в структурированный текст
│   ├── ui-client/          # BFF для бота: users, hashes, прокси к API Gateway
│   └── test-runner/        # Контейнер для запуска тестов
│
├── shared/                  # Общий код, подключаемый в образы сервисов
│   ├── tracing.py          # trace_id, структурированные логи (CTR-29)
│   ├── telemetry.py        # Метрики, трейсинг
│   └── company_normalize.py
│
├── migrations/              # Alembic: миграции PostgreSQL
├── scripts/                 # initdb, compose-up, check_llm_vendor, и др.
├── nginx/                   # Конфиг Nginx, conf.d/allow_ips.conf (генерируется build.py)
├── monitoring/              # Prometheus, Loki, Grafana
├── prompts/                 # Тексты промптов (перевод, структура резюме и т.д.)
├── tests/                   # E2E и диагностика
├── docs/                    # Доп. документация
├── diagrams/                # PNG из *.mmd (после build.py rebuild … --render-png)
├── *.mmd                    # Mermaid-диаграммы (SYSTEMS, FLOW-01, FLOW-02, FLOW-03)
└── external_llm/            # Адаптеры внешних LLM (копируются в образы)
```

### Назначение ключевых модулей

- **architecture/** — единый источник правды по системам, потокам и контрактам; не описывает детали реализации.
- **services/** — каждый подкаталог соответствует одному сервису; сборка через `Dockerfile.service` с `SVC=<name>`; общий код в **shared/**.
- **build.py** — единственная точка входа для сборки и запуска на VPS: образы, compose, миграции, опционально тесты и диаграммы.

---

## ⚙️ Запуск и конфигурация

### Быстрый старт

```bash
# Рекомендуется: скрипт проверяет API и решает, нужен ли Ollama
./scripts/compose-up.sh -d              # Linux/Mac
.\scripts\compose-up.ps1 -Detached     # Windows PowerShell

# С GPU
./scripts/compose-up.sh -d --gpu
.\scripts\compose-up.ps1 -Detached -Gpu

# Или через build.py (после сборки)
python build.py up
python build.py up --gpu
```

Внешний API (хост): **http://localhost:8001** (или через Nginx на VPS на порту 80).

### Примеры запросов к API

```bash
# Загрузка резюме (FLOW-02)
curl -X POST http://localhost:8001/documents/upload \
  -F "file=@resume.txt" -F "owner_id=user-123"

# Статус задачи индексации
curl http://localhost:8001/documents/tasks/{task_id}/status

# Соответствие вакансии (FLOW-03)
curl -X POST http://localhost:8001/match-vacancy \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "user-123", "vacancy_text": "Ищем Python-разработчика...", "summary": true}'

# Сопроводительное письмо (FLOW-04)
curl -X POST http://localhost:8001/cover-letter \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "user-123", "vacancy_text": "Описание вакансии..."}'
```

### Выбор LLM и эмбеддингов

| Режим | Описание |
|-------|----------|
| `LLM_PROVIDER=local` | Только Ollama (`docker-compose --profile local`) |
| `LLM_PROVIDER=openai` | OpenAI API (`OPENAI_API_KEY`) |
| `LLM_PROVIDER=deepseek` | DeepSeek API (`DEEPSEEK_API_KEY`) |
| `LLM_PROVIDER=gemini` | Google Gemini (`GEMINI_API_KEY`) |
| `LLM_PROVIDER=auto` | Сначала `FAVORITE_LLM_PROVIDER`; при отсутствии ключей → Ollama |

**Эмбеддинги:** при валидном vendor API используется **lightweight-embeddings** (bge-m3, 1024-dim); иначе — **embedding-service** с Ollama. На VPS без GPU задайте ключ в `.env` и используйте `LLM_PROVIDER=auto` или имя провайдера.

---

## 🔗 Клиентская интеграция и тесты

- **Базовый URL API:** `http://localhost:8001` (или ваш VPS).
- **CORS:** в api-gateway задаётся через `CORS_ORIGINS`. Пусто или `*` — все origins без credentials.
- **Ошибка "Failed to fetch / URL scheme must be http or https":** задайте базовый URL API явно во фронте (например `VITE_API_URL` / `NEXT_PUBLIC_API_URL`) и не вызывайте fetch при пустом значении.

### Тесты

```bash
# E2E загрузки (FLOW-02)
pytest tests/test_e2e_ingestion_flow.py -v -s

# RAG pipeline
pytest tests/test_e2e_rag_pipeline.py -v

# Через build.py (контейнер test-runner)
python build.py rebuild all --run-tests
```

Подробнее: `tests/README.md`, `tests/QUICK_START.md`.

### Архитектура и диаграммы

- Системы и потоки: **`architecture/KNOWLEDGE_MAP.md`**
- Контракты модулей: **`architecture/CONTRACT_REGISTRY.md`**
- Индекс промптов: **`architecture/PROMPT_INDEX_MAP.md`**
- Mermaid: `SYSTEMS.mmd`, `FLOW-01.mmd`, `FLOW-02.mmd`, `FLOW-03.mmd` → PNG в `diagrams/` через `build.py … --render-png`

**Стек:** Python 3.11+, Ollama / OpenAI / DeepSeek / Gemini, Docker Compose, PostgreSQL, Qdrant, Redis, RabbitMQ, Prometheus/Grafana/Loki. Сквозной `trace_id` (X-Request-ID), структурированные логи (CTR-29), сборка через `Dockerfile.service` и `shared/tracing.py`.
