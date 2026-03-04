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
# (можно с другого ПК/сети и скопировать в services/lightweight-embeddings/wheels/)

FROM python:3.11-slim

WORKDIR /app

# Опционально: установка torch из локального wheel (USE_OFFLINE_TORCH=1 и файл в wheels/)
# Кэш pip (BuildKit): сохраняет скачанные wheels между сборками — не перекачивать каждый раз.
ARG USE_OFFLINE_TORCH=0
COPY wheels/ /tmp/wheels/
RUN --mount=type=cache,target=/root/.cache/pip \
  if [ "$USE_OFFLINE_TORCH" = "1" ] && [ -n "$(find /tmp/wheels -maxdepth 1 -type f -name '*.whl' 2>/dev/null)" ]; then \
      pip install --no-cache-dir /tmp/wheels/*.whl; \
    else \
      pip install --default-timeout=600 torch==2.6.0; \
    fi

# Остальные зависимости (sentence-transformers тянет transformers)
RUN --mount=type=cache,target=/root/.cache/pip \
  pip install \
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
