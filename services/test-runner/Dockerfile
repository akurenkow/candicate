# Образ для запуска тестов: поднимается, выполняет pytest, завершается с кодом выхода тестов.
# Контекст сборки — корень проекта (передаётся из build.py).
# В runtime корень проекта можно смонтировать в /workspace для актуального кода.
FROM rag-base:latest

WORKDIR /workspace

# Доп. зависимости для тестов (pytest-asyncio и т.д.)
COPY services/test-runner/requirements.txt /tmp/test-runner-requirements.txt
RUN pip install --no-cache-dir -r /tmp/test-runner-requirements.txt

# Копируем всё необходимое для тестов (при сборке; при run можно перемонтировать том)
COPY shared /workspace/shared
COPY external_llm /workspace/external_llm
COPY services /workspace/services
COPY tests /workspace/tests
COPY pytest.ini /workspace/pytest.ini

# Запуск: unit-тесты по services/ + e2e, с покрытием. Игнорируем telegram-bot/tests в первом проходе.
# Выход с кодом pytest (0 = успех).
CMD ["python", "-m", "pytest", "-q", "services", "tests/test_e2e_ingestion_flow.py", "tests/test_e2e_rag_pipeline.py", "tests/test_e2e_match_vacancy.py", "-v", "--tb=short", "--cov=services", "--cov-report=term-missing", "--ignore=services/telegram-bot/tests"]
