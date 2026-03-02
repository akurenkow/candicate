# test-runner

Микросервис для запуска тестов: стартует, выполняет pytest (unit + e2e с покрытием), завершается с кодом выхода pytest.

## Сборка

Из корня проекта (после сборки базового образа `rag-base:latest`):

```bash
python build.py build test-runner
```

или через Docker:

```bash
docker build -f services/test-runner/Dockerfile -t rag-test-runner:latest .
```

## Запуск

Стек должен быть поднят (для e2e). Затем:

```bash
docker compose --profile test run --rm test-runner
```

При необходимости поднять только зависимости для тестов:

```bash
docker compose up -d postgres redis qdrant api-gateway
docker compose --profile test run --rm test-runner
```

Код выхода контейнера = код выхода pytest (0 = все тесты прошли).
