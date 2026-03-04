# Lightweight Embeddings Service (для внешних LLM: DeepSeek/OpenAI/Gemini)
# Основано на https://github.com/lh0x00/lightweight-embeddings
# Модель: bge-m3 (multilingual, 1024-dim)
#
# Сборка: для кэша pip между сборками нужен BuildKit (DOCKER_BUILDKIT=1 или docker buildx).
#
# PyTorch: по умолчанию ставится из PyPI (часто быстрее из РФ чем download.pytorch.org).
# Если скачивание медленное — положите CPU wheel в ./wheels/ и соберите с:
#   docker build --build-arg USE_OFFLINE_TORCH=1 -t rag-lightweight-embeddings .
# Скачать wheel: https://download.pytorch.org/whl/cpu/torch-2.6.0%2Bcpu-cp311-cp311-linux_x86_64.whl
# (можно с другого ПК/сети; при обрывах SFTP см. docs/CHUNKED_FILE_TRANSFER.md — передать по частям и склеить на VPS).
#
# Зеркало pip для РФ: для остальных пакетов используется PIP_INDEX_URL (mirror.yandex.ru).
# torch при онлайн-установке берётся с PyPI (на зеркале Яндекса нет torch 2.6.0).

FROM python:3.11-slim

WORKDIR /app

# Опционально: установка torch из локального wheel (USE_OFFLINE_TORCH=1 и файл в wheels/)
# Кэш pip (BuildKit): сохраняет скачанные wheels между сборками — не перекачивать каждый раз.
ARG USE_OFFLINE_TORCH=0
ARG PIP_INDEX_URL=https://mirror.yandex.ru/pypi/simple/
COPY wheels/ /tmp/wheels/
RUN --mount=type=cache,target=/root/.cache/pip \
  if [ "$USE_OFFLINE_TORCH" = "1" ]; then \
      if [ -z "$(find /tmp/wheels -maxdepth 1 -type f -name '*.whl' 2>/dev/null)" ]; then \
        echo "ERROR: USE_OFFLINE_TORCH=1 but no .whl in wheels/. Add torch CPU wheel to services/lightweight-embeddings/wheels/ on this host (see docs/CHUNKED_FILE_TRANSFER.md)."; exit 1; \
      fi; \
      pip install --no-cache-dir -i https://pypi.org/simple/ /tmp/wheels/*.whl; \
    else \
      pip install --default-timeout=600 -i https://pypi.org/simple/ torch==2.6.0; \
    fi

# Остальные зависимости (sentence-transformers тянет transformers)
RUN --mount=type=cache,target=/root/.cache/pip \
  pip install -i "$PIP_INDEX_URL" \
    fastapi==0.104.1 \
    uvicorn[standard]==0.24.0 \
    sentence-transformers>=2.7.0 \
    transformers>=4.36.0 \
    pydantic==2.5.0

COPY main.py /app/main.py

ENV MODEL_NAME=BAAI/bge-m3
ENV EMBEDDING_DIMENSION=1024
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
