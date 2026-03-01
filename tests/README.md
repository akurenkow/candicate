# Tests Directory

Директория с тестами для проекта wulf_grinder_arch.

## Содержание

### E2E Tests (End-to-End)

**`test_e2e_ingestion_flow.py`** - Сквозные тесты полного цикла загрузки документа (FLOW-02)

Тестирует весь пайплайн:
- Загрузка документа
- Перевод (ru→en) через Translation Service
- Семантическое разбиение на чанки с извлечением метаданных
- Получение эмбеддингов через Embedding Service
- Сохранение в PostgreSQL (documents, chunks) и Qdrant (vectors)

**Критерии успеха**:
- ✅ Документ разбит на >50 чанков
- ✅ Есть чанки с `section_kind="general"` и `section_kind="project"`
- ✅ У всех project-чанков заполнены: role, date_range, company_name, project_domain
- ✅ Данные корректно сохранены в PostgreSQL и Qdrant
- ✅ Метаданные в обеих БД совпадают

## Быстрый старт

```bash
# 1. Установить зависимости
pip install -r tests/requirements-test.txt

# 2. Применить миграцию БД
docker exec -i rag-postgres psql -U assistant_user -d assistant < scripts/initdb/02_add_chunks_metadata.sql

# 3. Запустить инфраструктуру
docker-compose up -d postgres qdrant embedding-service translation-service

# 4. Запустить тесты
pytest tests/test_e2e_ingestion_flow.py -v -s
```

**Подробнее**: См. `QUICK_START.md`

## Документация

| Файл | Описание |
|------|----------|
| **QUICK_START.md** | Быстрая инструкция для запуска тестов (4 шага) |
| **README_E2E_TESTS.md** | Полное руководство по e2e тестам |
| **E2E_TEST_SUMMARY.md** | Резюме реализации e2e тестов |
| **E2E_TEST_CHECKLIST.md** | Чеклист готовности к запуску тестов |
| **EXAMPLES.md** | Примеры использования и расширения тестов |
| **FILES_CREATED.md** | Список всех созданных файлов |

## Структура

```
tests/
├── test_e2e_ingestion_flow.py      # E2E тесты загрузки документов
├── requirements-test.txt            # Зависимости для тестов
├── README.md                        # Этот файл
├── QUICK_START.md                  # Быстрый старт
├── README_E2E_TESTS.md             # Полное руководство
├── E2E_TEST_SUMMARY.md             # Резюме реализации
├── E2E_TEST_CHECKLIST.md           # Чеклист готовности
├── EXAMPLES.md                     # Примеры использования
├── FILES_CREATED.md                # Список созданных файлов
├── run_e2e_tests.sh                # Скрипт запуска (Linux/Mac)
├── run_e2e_tests.bat               # Скрипт запуска (Windows)
└── data/
    └── Куренков Антон Андреевич Резюме.txt  # Тестовый файл
```

## Запуск тестов

### Все тесты

```bash
# Через pytest
pytest tests/test_e2e_ingestion_flow.py -v -s

# Через скрипт (Linux/Mac)
./tests/run_e2e_tests.sh

# Через скрипт (Windows)
tests\run_e2e_tests.bat
```

### Конкретный тест

```bash
pytest tests/test_e2e_ingestion_flow.py::TestE2EIngestion::test_chunks_count_greater_than_50 -v -s
```

### С сохранением данных

```bash
pytest tests/test_e2e_ingestion_flow.py -v -s --keep-test-data
```

## Требования

### Инфраструктура

- Docker и Docker Compose
- Ollama с моделями:
  - `snowflake-arctic-embed:latest` (для эмбеддингов)
  - `qwen2.5:7b` (для перевода)

### Сервисы

- PostgreSQL (порт 5432)
- Qdrant (порт 6335)
- Embedding Service (порт 8005)
- Translation Service (порт 8008)

### Python

- Python 3.11+
- Зависимости из `requirements-test.txt`:
  - pytest>=7.4.0
  - pytest-asyncio>=0.21.0
  - httpx>=0.24.0
  - asyncpg>=0.28.0
  - qdrant-client>=1.7.0

## Переменные окружения

```bash
# PostgreSQL
DATABASE_URL=postgresql://assistant_user:strongpassword@localhost:5432/assistant

# Qdrant
QDRANT_URL=http://localhost:6335
QDRANT_COLLECTION=documents
EMBEDDING_DIMENSION=1024

# Services
EMBEDDING_SERVICE_URL=http://localhost:8005
TRANSLATION_SERVICE_URL=http://localhost:8008
```

## Troubleshooting

### Сервисы не запущены

```bash
docker-compose up -d postgres qdrant embedding-service translation-service
docker-compose ps
```

### Колонка metadata не существует

```bash
docker exec -i rag-postgres psql -U assistant_user -d assistant < scripts/initdb/02_add_chunks_metadata.sql
```

### Файл резюме не найден

Проверить: `ls tests/data/`

Если файл в `.cursorignore`, временно убрать или скопировать в другое место.

### Таймаут при переводе

Проверить Ollama:
```bash
curl http://localhost:11434/api/tags
ollama list
```

**Подробнее**: См. `E2E_TEST_CHECKLIST.md` раздел Troubleshooting

## Расширение тестов

### Добавить новый тест

```python
# В test_e2e_ingestion_flow.py, в класс TestE2EIngestion

async def test_my_check(self, test_db_pool, uploaded_document):
    """Test: My custom validation."""
    document_id = uploaded_document["document_id"]
    
    async with test_db_pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM chunks WHERE document_id = $1 AND ...",
            document_id,
        )
    
    assert result > 0, "Check failed"
```

### Параметризовать тест

```python
@pytest.mark.parametrize("section_kind", ["general", "project", "company"])
async def test_by_section(self, test_db_pool, uploaded_document, section_kind):
    # ...
```

**Подробнее**: См. `EXAMPLES.md`

## CI/CD Integration

Примеры конфигурации для GitHub Actions и GitLab CI см. в `EXAMPLES.md`.

## Архитектурное соответствие

Тесты соответствуют архитектурным правилам проекта:

- ✅ **BOOTSTRAP RULES**: Не изобретает структуру вне определённых секций
- ✅ **INDEX DISCIPLINE**: Использует существующие ID систем
- ✅ **CONTRACT DISCIPLINE**: Следует контрактам CTR-19, CTR-18, CTR-25, CTR-17
- ✅ **NAVIGATION RULES**: Консультируется с индексами
- ✅ **TOKEN DISCIPLINE**: Минимальные изменения в существующем коде

**Подробнее**: См. `E2E_TEST_SUMMARY.md` раздел "Архитектурное соответствие"

## Связанные файлы

### Миграции

- `scripts/initdb/01_ingestion_schema.sql` - Основная схема БД
- `scripts/initdb/02_add_chunks_metadata.sql` - Добавление колонки metadata

### Конфигурация

- `pytest.ini` - Конфигурация pytest для проекта

### Архитектура

- `architecture/KNOWLEDGE_MAP.md` - FLOW-02, SYS-06
- `architecture/CONTRACT_REGISTRY.md` - CTR-19, CTR-18, CTR-25, CTR-17

### Реализация

- `services/ingestion-service/pipeline.py` - Пайплайн обработки
- `services/ingestion-service/semantic_resume/` - Парсер резюме
- `services/ingestion-service/db.py` - Работа с PostgreSQL

## Дополнительные ресурсы

- **pytest**: https://docs.pytest.org/
- **pytest-asyncio**: https://pytest-asyncio.readthedocs.io/
- **asyncpg**: https://magicstack.github.io/asyncpg/
- **qdrant-client**: https://qdrant.tech/documentation/

## Поддержка

При возникновении проблем:

1. Проверить `E2E_TEST_CHECKLIST.md`
2. Посмотреть `EXAMPLES.md` (раздел Troubleshooting)
3. Проверить логи сервисов: `docker-compose logs embedding-service translation-service`
4. Проверить архитектурные документы в `architecture/`

---

**Последнее обновление**: 2026-02-15

**Версия**: 1.0.0

**Автор**: Создано для проекта wulf_grinder_arch
