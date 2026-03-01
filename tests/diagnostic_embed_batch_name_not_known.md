# Diagnostic: embed_batch failed — Name or service not known (conversation-manager)

## Симптом

В логах **conversation-manager** (CM):

```
embedding_client - WARNING - embed_batch failed: [Errno -2] Name or service not known
```

Ошибка возникает при post-process (fidelity check): CM вызывает Embedding Service для сравнения эмбеддингов оригинала и перевода; запрос к хосту не выполняется из-за неудачного DNS-разрешения.

---

## Причина

- **Код:** `services/conversation-manager/embedding_client.py` выполняет `POST {EMBEDDING_SERVICE_URL}/embed_batch`. URL берётся из `config.EMBEDDING_SERVICE_URL`.
- **Конфиг:** `services/conversation-manager/config.py`: по умолчанию `http://embedding-service:8000` (имя сервиса Docker).
- **Ошибка:** Хост `embedding-service` не резолвится → **[Errno -2] Name or service not known** (EAI_NONAME).

В **базовом** `docker-compose.yml` сервис с именем `embedding-service` **не объявлен**. Он подключается только через override:

| Файл | Сервис embedding | Имя хоста в сети |
|------|-------------------|-------------------|
| `docker-compose.ollama.yml` | `embedding-service` (rag-embedding-service) | `embedding-service` |
| `docker-compose.vendor-embeddings.yml` | `lightweight-embeddings` (rag-lightweight-embeddings) | `lightweight-embeddings` |

При запуске в **vendor-режиме** (валидный API ключ OpenAI/DeepSeek/Gemini) `build.py` подключает `docker-compose.vendor-embeddings.yml`. В этом режиме:

- Запускается только **lightweight-embeddings** (имя сервиса и хоста — `lightweight-embeddings`).
- Контейнера/сервиса **embedding-service** нет.
- В `docker-compose.vendor-embeddings.yml` изначально были переопределены только **retrieval-service** и **ingestion-service** (`EMBEDDING_SERVICE_URL: http://lightweight-embeddings:8000`).
- **conversation-manager** не переопределялся и продолжал использовать `http://embedding-service:8000` из базового compose → резолв имени `embedding-service` падает → ошибка в логе.

Итого: при vendor-режиме CM обращается к несуществующему хосту `embedding-service`, тогда как реальный сервис эмбеддингов доступен по имени `lightweight-embeddings`.

---

## Исправление

В **docker-compose.vendor-embeddings.yml** для сервисов, использующих embedding (в т.ч. **conversation-manager**), задаётся `EMBEDDING_SERVICE_URL: http://lightweight-embeddings:8000` (без подстановки из .env).

**Какой embedding-сервис используется**, определяется не переменной `EMBEDDING_SERVICE_URL` в .env, а **LLM_PROVIDER и результатом проверки API** (скрипт `scripts/check_llm_vendor.py`). При запуске через `build.py`:
- если vendor API валиден (exit 0) — подключается overlay **vendor-embeddings** → все сервисы идут в **lightweight-embeddings**;
- если local или fallback (exit 1) — подключается **ollama.yml** → в сети есть **embedding-service**, базовый compose уже задаёт `http://embedding-service:8000`.

Для **conversation-manager** в этом override дополнительно в **depends_on** добавлена зависимость от `lightweight-embeddings` с `condition: service_healthy`.

---

## Проверка

1. Убедиться, что используется vendor-режим: при запуске через `build.py` в логе должно быть что-то вроде «Запуск Docker Compose (vendor API OK, lightweight-embeddings)».
2. Проверить переменную в контейнере CM:
   ```bash
   docker exec rag-conversation-manager env | grep EMBEDDING_SERVICE_URL
   ```
   При overlay vendor-embeddings ожидается: `EMBEDDING_SERVICE_URL=http://lightweight-embeddings:8000`.
3. Из контейнера CM проверить доступность сервиса:
   ```bash
   docker exec rag-conversation-manager python -c "
   import urllib.request
   u = 'http://lightweight-embeddings:8000/health'
   r = urllib.request.urlopen(u, timeout=5)
   print(r.read())
   "
   ```

---

## Связанные компоненты

- **post_process** (fidelity check): `services/conversation-manager/post_process.py` → `_validate_fidelity()` → `embed_batch_client([original, translated], ...)`. При недоступности embedding-сервиса fidelity падает → эскалация `response_translation_failed:fidelity_check_failed`.
- **CONTRACT_REGISTRY:** `EMBEDDING_SERVICE_URL` для CM по спецификации — `http://embedding-service:8000`; в vendor-режиме фактический хост задаётся override и должен быть `lightweight-embeddings`.
