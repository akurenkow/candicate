# Diagnostic: no_documents_found (retrieval-service)

## Code paths that yield empty chunks → no_documents_found

### 1. Retrieval service uses StubVectorSearch (always returns [])
- **Where:** `services/retrieval-service/routers/search.py` → `_get_vector_search()`
- **Condition:** `QDRANT_URL` is empty or only whitespace → `StubVectorSearch()` → `search()` returns `[]`
- **Config:** `services/retrieval-service/config.py`: `QDRANT_URL = get_env("QDRANT_URL", "http://localhost:6333")`
- **Logs:** No specific message; retrieval-service does not log "using Stub". Check env of retrieval-service process: `QDRANT_URL` must be set and non-empty.

### 2. Empty embedding → search_flow returns [] before vector search
- **Where:** `services/retrieval-service/retrieval/search_flow.py` lines 31–33
- **Condition:** `embedding_adapter.embed(query)` returns `[]` or None → `return []`
- **Logs (retrieval-service):**  
  `"Empty embedding for query; skipping vector search"`
- **Upstream:** `services/retrieval-service/retrieval/adapters/embedding.py` — `HttpEmbeddingAdapter.embed()` on exception or missing `vector` in JSON returns `[]`; logs:  
  `"Embedding service embed failed: %s"`

### 3. QdrantVectorSearch: empty embedding
- **Where:** `services/retrieval-service/retrieval/vector_search.py` lines 84–86
- **Condition:** `not embedding or len(embedding) == 0` → `return []`
- **Logs (retrieval-service):**  
  `"QdrantVectorSearch: empty embedding, returning no chunks"`

### 4. QdrantVectorSearch: vector search exception (scroll still runs after our fix)
- **Where:** `services/retrieval-service/retrieval/vector_search.py` — first `try/except` (vector search)
- **Condition:** `search_points` throws (e.g. dimension mismatch, connection, collection not found)
- **Logs (retrieval-service):**  
  `"QdrantVectorSearch vector search failed: %s"`  
  After that, scroll for general chunks still runs. If scroll also fails or returns 0 points → merged list can be [].

### 5. Scroll returns 0 points (no general chunks for this owner_id)
- **Condition:** Collection has no points with `owner_id=<requested>` AND `section_kind=general` (e.g. different collection, ingestion wrote to other collection/owner_id, or no general chunks indexed).
- **Logs:** No "scroll failed" if scroll succeeds but returns empty. Only if scroll throws:  
  `"QdrantVectorSearch scroll general failed (non-fatal): %s"` (logger.debug — may be disabled).

### 6. Conversation-manager: retrieval HTTP call fails → []
- **Where:** `services/conversation-manager/retrieval_client.py` lines 46–47
- **Condition:** Any exception (connection, timeout, 4xx/5xx) → `return []`  
- **Logs (conversation-manager):**  
  `"Retrieval search failed: %s"`

### 7. filter_metadata empty or wrong → no match in Qdrant
- **Where:** CM builds `filters` in `subtask_retrieval.py`: `filters["owner_id"] = owner_id.strip()` only if `(owner_id or "").strip()` truthy. If `owner_id` is empty, `use_filters = None` → retrieval gets `body.filters == None` → QdrantVectorSearch builds no filter → search by vector only; scroll block runs only `if query_filter is not None and filter_metadata` → scroll is skipped, so no general merge. If owner_id in request differs from ingestion (e.g. encoding, extra spaces), filter may match 0 points.
- **Logs:** No direct log. Check CM: what `owner_id` is in `lifecycle` (from `rag_messages.owner_id`). Check retrieval request body: `filters` must contain correct `owner_id`.

---

## What to search in logs

| Where              | Search string / check |
|--------------------|------------------------|
| retrieval-service  | `Empty embedding for query` → embedding returned [] |
| retrieval-service  | `Embedding service embed failed` → embedding-service error |
| retrieval-service  | `QdrantVectorSearch: empty embedding` → empty vector reached vector search |
| retrieval-service  | `QdrantVectorSearch vector search failed` → Qdrant search exception (scroll still runs) |
| retrieval-service  | `QdrantVectorSearch scroll general failed` → scroll exception (debug level) |
| conversation-manager | `Retrieval search failed` → HTTP/connection to retrieval failed |
| Env (retrieval-service) | `QDRANT_URL` set and non-empty (if empty → Stub → []) |
| Request (retrieval) | POST /search body: `filters` present and `owner_id` equals the one used at ingestion |

---

## Diagnostic logging added (what to search after deploy)

| Service              | Log message pattern | Meaning |
|----------------------|---------------------|--------|
| conversation-manager | `retrieval_search outbound query=... filters=` | Exact query and filters CM sends to retrieval |
| retrieval-service   | `POST /search request_id=... query=... filters=` | Request body received by retrieval |
| retrieval-service   | `retrieval backend: StubVectorSearch` or `QdrantVectorSearch` | Which backend is used |
| retrieval-service   | `POST /search response ... chunks_count=` | Number of chunks returned (0 = no_documents_found) |
| retrieval-service   | `QdrantVectorSearch result filter_metadata=... vector_chunks=... general_chunks=... total=` | If filter is None, scroll is skipped; vector_chunks=0 and general_chunks=0 → no points for this owner_id in collection |

---

## Quick checks

1. **Same request from test vs CM**  
   E2E test calls `_search_chunks(RETRIEVAL_URL, owner_id, "What salary range do you expect?")` with same `owner_id` as chat. If direct POST /search returns chunks but chat gets fallback, compare: query string (working_query after translate/decompose), `filters.owner_id`, and that the same retrieval-service instance is hit (same host/port).

2. **Collection / dimension**  
   Ingestion and retrieval must use same `QDRANT_COLLECTION` (default `documents`) and same embedding dimension; mismatch can cause vector search to throw (see log "vector search failed").

3. **Add temporary log in retrieval-service** (if needed)  
   In `routers/search.py` after `chunks = await retrieve(...)`: log `len(chunks)` and whether `vs` is `StubVectorSearch` (e.g. `type(vs).__name__`); and in `vector_search.py` after scroll, log `len(general_chunks)` and `len(vector_chunks)` to see which branch returned empty.
