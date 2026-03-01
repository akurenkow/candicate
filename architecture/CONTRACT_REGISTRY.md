# CONTRACT REGISTRY

This registry defines stable architectural contracts between modules.

Rules:

* Contracts are append-only.
* Existing IDs are never modified.
* Breaking changes create new contract IDs.

---

# CONTRACTS

## CTR-01: External API (Gateway → Clients)
- **Type**: REST/JSON.
- **Provider**: API Gateway (SYS-01).
- **Consumers**: External clients (frontend, HR, candidates).
- **Spec**: Single entry point; authentication, validation (Pydantic), rate limiting, routing to internal services. Port 8001 (host) → 8000 (container).
- **Ref**: KNOWLEDGE_MAP SYS-01; docker-compose api-gateway.

## CTR-02: Gateway → Conversation Manager
- **Type**: HTTP/REST (proxy).
- **Provider**: Conversation Manager (SYS-02).
- **Consumers**: API Gateway (SYS-01).
- **Spec**: `CONVERSATION_MANAGER_URL`: http://conversation-manager:8000. Operations: enqueue, get message status, match_vacancy (see CTR-05, CTR-33).
- **Ref**: KNOWLEDGE_MAP SYS-01; docker-compose api-gateway, conversation-manager.

## CTR-03: Gateway → Ingestion (async)
- **Type**: HTTP + message queue.
- **Provider**: Ingestion Service (SYS-06) via RabbitMQ.
- **Consumers**: API Gateway (SYS-01) — publishes to queue.
- **Spec**: Gateway forwards upload requests; command `upload_document` via `RABBITMQ_URL`. Queues: `document_uploads`, `ingestion_results` (env: UPLOAD_QUEUE, RESULT_QUEUE).
- **Ref**: KNOWLEDGE_MAP SYS-01, FLOW-02; docker-compose api-gateway, ingestion-service.

## CTR-04: Gateway → AuthZ
- **Type**: HTTP/REST.
- **Provider**: AuthZ Service (SYS-07).
- **Consumers**: API Gateway (SYS-01).
- **Spec**: `AUTHZ_SERVICE_URL`: http://authz-service:8000. Used for access checks on incoming requests.
- **Ref**: KNOWLEDGE_MAP SYS-01; docker-compose api-gateway, authz-service.

## CTR-05: Conversation Manager internal API
- **Type**: REST (internal).
- **Provider**: Conversation Manager (SYS-02).
- **Consumers**: API Gateway (SYS-01).
- **Spec**: `POST /enqueue`, `GET /messages/{message_id}`, `POST /match-vacancy` (FLOW-03, see CTR-33). Port 8002 (host) → 8000 (container). Headers: X-Request-ID (trace_id).
- **Ref**: KNOWLEDGE_MAP SYS-02.

## CTR-06: Conversation Manager → Retrieval Service
- **Type**: HTTP/REST.
- **Provider**: Retrieval Service (SYS-03).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `RETRIEVAL_SERVICE_URL`: http://retrieval-service:8000. Contract: `search(query, filters, top_k)` → list of chunks.
- **Ref**: KNOWLEDGE_MAP SYS-02, SYS-03; docker-compose conversation-manager, retrieval-service.

## CTR-07: Conversation Manager → Generation Service
- **Type**: HTTP/REST.
- **Provider**: Generation Service (SYS-04).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `GENERATION_SERVICE_URL`: http://generation-service:8000. Contract: `generate(prompt)`, `generate_clarification(original_query, documents, history)`.
- **Ref**: KNOWLEDGE_MAP SYS-02, SYS-04; docker-compose conversation-manager, generation-service.

## CTR-08: Conversation Manager → AuthZ Service
- **Type**: HTTP/REST.
- **Provider**: AuthZ Service (SYS-07).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `AUTHZ_SERVICE_URL`: http://authz-service:8000. Contract: `check_access(user_id, doc_id, action)`.
- **Ref**: KNOWLEDGE_MAP SYS-02, SYS-07; docker-compose conversation-manager, authz-service.

## CTR-09: Conversation Manager → PostgreSQL
- **Type**: SQL (connection string).
- **Provider**: PostgreSQL (SYS-09).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `DATABASE_URL` (postgres:5432). Data: conversation history.
- **Ref**: KNOWLEDGE_MAP SYS-02, SYS-09; docker-compose conversation-manager, postgres.

## CTR-10: Retrieval Service search API
- **Type**: REST.
- **Provider**: Retrieval Service (SYS-03).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `POST /search` body `{ query, top_k, filters? }` → `{ chunks }`. Port 8003 (host) → 8000. Params: TOP_K (config), QDRANT_URL, QDRANT_COLLECTION.
- **Ref**: KNOWLEDGE_MAP SYS-03; docker-compose retrieval-service.

## CTR-11: Retrieval Service → Embedding Service
- **Type**: HTTP/REST.
- **Provider**: Embedding Service (SYS-05).
- **Consumers**: Retrieval Service (SYS-03).
- **Spec**: `EMBEDDING_SERVICE_URL`: http://embedding-service:8000. Contract: `embed(text)`, `embed_batch(texts)`.
- **Ref**: KNOWLEDGE_MAP SYS-03, SYS-05; docker-compose retrieval-service, embedding-service.

## CTR-12: Retrieval Service → Qdrant
- **Type**: HTTP/gRPC.
- **Provider**: Qdrant (SYS-10).
- **Consumers**: Retrieval Service (SYS-03).
- **Spec**: `QDRANT_URL`: http://qdrant:6333, `QDRANT_COLLECTION`: documents. Vector search and write.
- **Ref**: KNOWLEDGE_MAP SYS-03, SYS-10; docker-compose retrieval-service, qdrant.

## CTR-13: Retrieval Service → AuthZ Service
- **Type**: HTTP/REST.
- **Provider**: AuthZ Service (SYS-07).
- **Consumers**: Retrieval Service (SYS-03).
- **Spec**: `AUTHZ_SERVICE_URL`: http://authz-service:8000. Filter documents by access.
- **Ref**: KNOWLEDGE_MAP SYS-03; docker-compose retrieval-service, authz-service.

## CTR-14: Generation Service API
- **Type**: REST.
- **Provider**: Generation Service (SYS-04).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `generate(prompt)`, `generate_clarification(...)`. Port 8004→8000. Backend: external_llm (OpenAI, DeepSeek, or Ollama via CTR-36), models from env.
- **Ref**: KNOWLEDGE_MAP SYS-04; docker-compose generation-service.

## CTR-15: Embedding Service API
- **Type**: REST.
- **Provider**: Embedding Service (SYS-05) или Lightweight Embeddings (vendor).
- **Consumers**: Retrieval Service (SYS-03), Ingestion Service (SYS-06), Conversation Manager (post-process fidelity).
- **Spec**: `embed(text)`, `embed_batch(texts)`. Port 8005→8000. Backend: external_llm (OpenAI, DeepSeek, or Ollama), Redis cache (REDIS_URL db 4).
- **Deployment**: Хост задаётся выбором overlay (не EMBEDDING_SERVICE_URL в .env): при LLM_PROVIDER=local или fallback на local (check_llm_vendor exit 1) — docker-compose.ollama.yml → `embedding-service`; при валидном vendor API (exit 0) — docker-compose.vendor-embeddings.yml → `lightweight-embeddings`. build.py вызывает check_llm_vendor и подключает нужный overlay.
- **Ref**: KNOWLEDGE_MAP SYS-05; docker-compose embedding-service, docker-compose.vendor-embeddings.yml.

## CTR-16: Ingestion Service → RabbitMQ
- **Type**: AMQP (queues).
- **Provider**: RabbitMQ (SYS-08).
- **Consumers**: Ingestion Service (SYS-06) — consumer; API Gateway / producers — publishers.
- **Spec**: `RABBITMQ_URL` (rabbitmq:5672). Queues: `document_uploads` (commands), `ingestion_results` (events). Contract: command `upload_document`; event on indexing completion.
- **Ref**: KNOWLEDGE_MAP SYS-06, SYS-08, FLOW-02; docker-compose ingestion-service, rabbitmq.

## CTR-17: Ingestion Service → Embedding Service
- **Type**: HTTP/REST.
- **Provider**: Embedding Service (SYS-05).
- **Consumers**: Ingestion Service (SYS-06).
- **Spec**: `EMBEDDING_SERVICE_URL`: http://embedding-service:8000. Batch embedding for chunks.
- **Ref**: KNOWLEDGE_MAP SYS-06; docker-compose ingestion-service.

## CTR-18: Ingestion Service → Qdrant
- **Type**: HTTP/gRPC.
- **Provider**: Qdrant (SYS-10).
- **Consumers**: Ingestion Service (SYS-06).
- **Spec**: `QDRANT_URL`: http://qdrant:6333, `QDRANT_COLLECTION`: documents. Write vectors for indexed chunks.
- **Ref**: KNOWLEDGE_MAP SYS-06, SYS-10; docker-compose ingestion-service, qdrant.

## CTR-19: Ingestion Service → PostgreSQL
- **Type**: SQL.
- **Provider**: PostgreSQL (SYS-09).
- **Consumers**: Ingestion Service (SYS-06).
- **Spec**: `DATABASE_URL` (postgres:5432). Data: source documents (content + original_content), chunks, metadata, versioning. Table `documents` stores both translated `content` (used for chunking/indexing) and `original_content` (untranslated source text).
- **Ref**: KNOWLEDGE_MAP SYS-06, SYS-09; docker-compose ingestion-service, postgres.

## CTR-20: AuthZ Service API
- **Type**: REST.
- **Provider**: AuthZ Service (SYS-07).
- **Consumers**: API Gateway (SYS-01), Conversation Manager (SYS-02), Retrieval Service (SYS-03).
- **Spec**: `check_access(user_id, doc_id, action)`. Port 8007→8000. Storage: PostgreSQL; cache: Redis db 6.
- **Ref**: KNOWLEDGE_MAP SYS-07; docker-compose authz-service.

## CTR-21: AuthZ Service → PostgreSQL
- **Type**: SQL.
- **Provider**: PostgreSQL (SYS-09).
- **Consumers**: AuthZ Service (SYS-07).
- **Spec**: `DATABASE_URL`. Data: access policies (RBAC).
- **Ref**: KNOWLEDGE_MAP SYS-07, SYS-09; docker-compose authz-service, postgres.

## CTR-25: Ingestion Service → Translation Service
- **Type**: HTTP/REST.
- **Provider**: Translation Service (SYS-15).
- **Consumers**: Ingestion Service (SYS-06).
- **Spec**: `TRANSLATION_SERVICE_URL`: http://translation-service:8000. Contract: `POST /translate` with body `{ text, source_lang, target_lang, model?, temperature? }` → `{ translated_text }`. Used in FLOW-02 for ru→en before chunking. Ingestion Service performs parallel translation of general section and each company block separately (up to MAX_THREAD_WORKERS concurrent requests) to prevent data loss with large documents.
- **Ref**: KNOWLEDGE_MAP SYS-06, SYS-15, FLOW-02; docker-compose ingestion-service, translation-service.

## CTR-26: Translation Service → LLM
- **Type**: external_llm provider (OpenAI, DeepSeek, or Ollama).
- **Provider**: external_llm (CTR-36).
- **Consumers**: Translation Service (SYS-15).
- **Spec**: LLM-based translation via resolved provider. Prompts: TRANSLATE_PROMPT_PATH_RU_EN (ru→en), TRANSLATE_PROMPT_PATH_EN_RU (en→ru). `OLLAMA_URL` for local.
- **Ref**: KNOWLEDGE_MAP SYS-15; docker-compose translation-service.

## CTR-27: Translation Service → Redis
- **Type**: Redis protocol.
- **Provider**: Redis (SYS-11).
- **Consumers**: Translation Service (SYS-15).
- **Spec**: `REDIS_URL` db 8. Cache of translation results (key by text + lang pair + model).
- **Ref**: KNOWLEDGE_MAP SYS-15; docker-compose translation-service.

## CTR-28: Translation Service → RabbitMQ
- **Type**: AMQP (queues).
- **Provider**: RabbitMQ (SYS-08).
- **Consumers**: Translation Service (SYS-15) — consumer of `translation_tasks`, publisher to `translation_results`.
- **Spec**: Queues: `translation_tasks`, `translation_results`. Optional async path for translation tasks.
- **Ref**: KNOWLEDGE_MAP SYS-15, SYS-08; docker-compose translation-service, translation-worker.

## CTR-22: Prometheus metrics
- **Type**: HTTP scrape (Pull).
- **Provider**: All app services (api-gateway, conversation-manager, retrieval-service, generation-service, embedding-service, ingestion-service, translation-service, authz-service).
- **Consumers**: Prometheus (SYS-12).
- **Spec**: Endpoint `/metrics` on each service (METRICS_PORT 8000). Prometheus scrapes from service hostnames on backend network.
- **Ref**: KNOWLEDGE_MAP SYS-12; docker-compose prometheus, all services.

## CTR-23: Loki log ingestion
- **Type**: Log driver / push.
- **Provider**: All containers (Docker logging).
- **Consumers**: Loki (SYS-14).
- **Spec**: Logs via Docker json-file driver; Loki configured in `monitoring/loki/loki-config.yml`. Port 3100.
- **Ref**: KNOWLEDGE_MAP SYS-14; docker-compose loki.

## CTR-24: Grafana datasources
- **Type**: HTTP (datasource APIs).
- **Provider**: Prometheus (SYS-12), Loki (SYS-14).
- **Consumers**: Grafana (SYS-13).
- **Spec**: Dashboards and provisioning to Prometheus and Loki. Port 3000.
- **Ref**: KNOWLEDGE_MAP SYS-13; docker-compose grafana, prometheus, loki.

## CTR-29: Distributed tracing (trace_id, structured logs)
- **Type**: Convention + shared library.
- **Provider**: All app services (via shared/tracing.py).
- **Consumers**: Loki, Grafana, grep (по trace_id).
- **Spec**: (1) **trace_id**: передаётся как HTTP-заголовок `X-Request-ID` и в теле сообщений RabbitMQ (`trace_id`). (2) В лог пишется строка: `trace_id=... | service=... | operation=... | target_service=... | target_resource=...`. (3) **operation**: `http_in`, `http_out`, `queue_publish`, `queue_consume`, `db_write`, `db_read`, `vector_write`, `vector_read`, `cache_read`, `cache_write`, `llm_call`. (4) **target_service**: имя сервиса или внешней системы (postgres, qdrant, rabbitmq, ollama, redis, ingestion-service, embedding-service, …). (5) **target_resource**: таблица/коллекция/очередь/модель (например `documents`, `chunks`, `documents` (Qdrant), `document_uploads`, `nomic-embed-text`). Сборка сервисов: контекст корень проекта, Dockerfile.service, копируется shared/ и services/<SVC>.
- **Ref**: KNOWLEDGE_MAP ARCHITECTURAL NOTES; shared/tracing.py; build.py, Dockerfile.service.

## CTR-30: Conversation Manager → Clearing Service
- **Type**: HTTP/REST.
- **Provider**: Clearing Service (preprocessing pipeline).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `CLEARING_SERVICE_URL`: http://clearing-service:8000. Contract: `POST /preprocess` body `{ "text": "<raw>" }` → `{ "normalized": "<str>", "validation_result": "OK"|"BLOCK", "reasons": ["<reason>", ...] }`. Idempotent. Used in FLOW-01 after lock; BLOCK → message completed with fallback response.
- **Ref**: KNOWLEDGE_MAP FLOW-01; docker-compose conversation-manager, clearing-service.

## CTR-31: Retrieval Service search response (CTR-10 payload)
- **Type**: REST response schema.
- **Provider**: Retrieval Service (SYS-03).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `POST /search` request body `{ "query": "<str>", "top_k": int, "filters": <dict>? }`. Response body `{ "chunks": [ { "chunk_id", "text", "score", "metadata" } ] }`. Empty `chunks` when no documents found; consumer treats as failed_retrieval (FLOW-01 step 17) and may set status escalated.
- **Ref**: CTR-10; FLOW-01 steps 15–17, 25.

## CTR-32: Conversation Manager → Redis (response cache)
- **Type**: Redis protocol.
- **Provider**: Redis (SYS-11).
- **Consumers**: Conversation Manager (SYS-02).
- **Spec**: `REDIS_URL` db 1. Key `rag:response:{sha256(normalized_query)}`, TTL RESPONSE_CACHE_TTL_SEC. Lookup before generation; write after successful completion. Cache stores assistant_text (generation language).
- **Ref**: FLOW-01 steps 11a–11c, 32; response_cache.py.

## CTR-34: Telegram Bot → API Gateway
- **Type**: HTTP/REST.
- **Provider**: API Gateway (SYS-01).
- **Consumers**: Telegram Bot (SYS-17).
- **Spec**: `BASE_API_URL`: http://api-gateway:8000. Operations: documents/upload, match-vacancy, enqueue (FLOW-01). Bot proxies user actions to API Gateway.
- **Ref**: KNOWLEDGE_MAP SYS-17; docker-compose telegram-bot, api-gateway.

## CTR-35: Telegram Bot → PostgreSQL
- **Type**: SQL.
- **Provider**: PostgreSQL (SYS-09).
- **Consumers**: Telegram Bot (SYS-17).
- **Spec**: `DATABASE_URL`. Data: users (telegram_id, role, mode, dialog_owner_id, language), hashes.
- **Ref**: KNOWLEDGE_MAP SYS-17; docker-compose telegram-bot, postgres.

## CTR-33: Match with Vacancy (FLOW-03)
- **Type**: REST/JSON.
- **Provider**: Conversation Manager (SYS-02).
- **Consumers**: API Gateway (SYS-01), proxying external HR clients.
- **Spec**: `POST /match-vacancy` body `{ owner_id: str, vacancy_text: str, summary: bool }`. Single candidate match: retrieval filtered by owner_id; response one item `[ { owner_id, relevance_score, matched_snippets, summary? } ]`. One request at a time (in-process lock). Uses CTR-06, CTR-07. Empty list on retrieval error or no chunks.
- **Ref**: KNOWLEDGE_MAP FLOW-03; conversation-manager match_vacancy.py, main.py; api-gateway main.py.

## CTR-36: External LLM Adapter (external_llm)
- **Type**: Python module / factory.
- **Provider**: external_llm (project root).
- **Consumers**: Generation Service (SYS-04), Embedding Service (SYS-05), Translation Service (SYS-15).
- **Spec**: `resolve_provider()` / `resolve_for_chat()` / `resolve_for_embed()` select OpenAI, DeepSeek, Gemini, or Ollama from env: `LLM_PROVIDER` (auto|openai|deepseek|gemini|local), `FAVORITE_LLM_PROVIDER`, API keys. Fallback to Ollama when vendor keys invalid. `BaseLLMProvider` interface: `chat(messages)`, `embed(text)`, `embed_batch(texts)`.
- **Ref**: external_llm/factory.py; services generation-service, embedding-service, translation-service.

---

# INDEX (by system)

| System      | Provider contracts       | Consumer contracts                          |
|------------|---------------------------|---------------------------------------------|
| SYS-01     | CTR-01, CTR-34            | CTR-02, CTR-03, CTR-04                      |
| SYS-02     | CTR-02, CTR-05, CTR-33    | CTR-06, CTR-07, CTR-08, CTR-09, CTR-30, CTR-32 |
| SYS-03     | CTR-06, CTR-10, CTR-31    | CTR-11, CTR-12, CTR-13                      |
| SYS-04     | CTR-07, CTR-14            | CTR-36 (OpenAI/DeepSeek/Ollama)             |
| SYS-05     | CTR-11, CTR-15, CTR-17    | CTR-36 (OpenAI/DeepSeek/Ollama), Redis      |
| SYS-06     | CTR-03, CTR-16            | CTR-16, CTR-17, CTR-18, CTR-19, CTR-25     |
| SYS-07     | CTR-04, CTR-08, CTR-13, CTR-20 | CTR-21                                |
| SYS-08     | CTR-03, CTR-16            | —                                           |
| SYS-09     | CTR-09, CTR-19, CTR-21, CTR-35 | —                                       |
| SYS-10     | CTR-12, CTR-18            | —                                           |
| SYS-11     | CTR-27, CTR-32            | Multiple services (env REDIS_URL, db 0–8)   |
| SYS-12     | CTR-22                    | —                                           |
| SYS-15     | CTR-25                    | CTR-26, CTR-27, CTR-28, CTR-36              |
| SYS-16     | CTR-30                    | —                                           |
| SYS-17     | —                         | CTR-34, CTR-35                              |
| SYS-13     | CTR-24                    | CTR-24 (Prometheus, Loki)                   |
| SYS-14     | CTR-23                    | —                                           |
