#!/usr/bin/env python3
"""
Утилита для сборки Docker-образов микросервисного RAG-ассистента.
Поддерживает сборку базового образа, отдельных сервисов, всех сервисов,
а также полную пересборку с удалением старых образов.

После успешной пересборки (команда rebuild) автоматически:
  - запускаются все контейнеры (docker-compose up -d);
  - применяются миграции PostgreSQL (alembic upgrade head);
  - при --total-erase выполняется полная очистка (Docker + кэш Redis, Qdrant и PostgreSQL);
  - при --erase-db очищаются только кэш Redis, Qdrant и пользовательские таблицы PostgreSQL;
  - все файлы *.mmd из корня проекта конвертируются в PNG и сохраняются в diagrams/ (последний шаг).
"""

import argparse
import shutil
import subprocess
import sys
import os
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus

# Константы
PROJECT_ROOT = Path(__file__).parent.absolute()
BASE_DOCKERFILE = "Dockerfile.base"
BASE_IMAGE_NAME = "rag-base"
SERVICES_DIR = PROJECT_ROOT / "services"
DEFAULT_TAG = "latest"
COMPOSE_FILE = "docker-compose.yml"
COMPOSE_GPU_FILE = "docker-compose.gpu.yml"
NGINX_ALLOW_IPS_CONF = PROJECT_ROOT / "nginx" / "conf.d" / "allow_ips.conf"


def _get_python_for_tests() -> str:
    """
    Python-интерпретатор для запуска тестов.
    Порядок: (1) PYTHON_TEST_EXECUTABLE из env, если задан и исполняемый файл существует;
    (2) python3.11 из PATH (shutil.which); (3) на Windows — py -3.11;
    (4) иначе sys.executable.
    """
    exe = os.environ.get("PYTHON_TEST_EXECUTABLE", "").strip()
    if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
        return exe
    py311 = shutil.which("python3.11")
    if py311:
        return py311
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["py", "-3.11", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=PROJECT_ROOT,
            )
            if r.returncode == 0 and r.stdout and os.path.isfile(r.stdout.strip()):
                return r.stdout.strip()
        except Exception:
            pass
    return sys.executable


def run_command(cmd: List[str], description: str, env: Optional[dict] = None) -> bool:
    """Запускает команду в корне проекта и выводит результат. Возвращает True при успехе."""
    print(f"\n>>> {description}")
    print(f"Команда: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, cwd=PROJECT_ROOT, env=env if env is not None else os.environ)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при выполнении: {e}", file=sys.stderr)
        return False


def get_available_services() -> List[str]:
    """Возвращает список имён сервисов (имена папок в services/)."""
    if not SERVICES_DIR.exists():
        return []
    return [d.name for d in SERVICES_DIR.iterdir() if d.is_dir()]


def build_base(no_cache: bool = False, pull: bool = False) -> bool:
    """Сборка базового образа."""
    cmd = ["docker", "build"]
    if no_cache:
        cmd.append("--no-cache")
    if pull:
        cmd.append("--pull")
    cmd.extend(["-t", f"{BASE_IMAGE_NAME}:{DEFAULT_TAG}", "-f", BASE_DOCKERFILE, "."])
    build_env = {**os.environ, "DOCKER_BUILDKIT": "1"}
    return run_command(cmd, "Сборка базового образа", env=build_env)


def build_service(service_name: str, no_cache: bool = False, pull: bool = False) -> bool:
    """Сборка образа конкретного сервиса (с shared lib, контекст — корень проекта)."""
    service_path = SERVICES_DIR / service_name
    if not service_path.is_dir():
        print(f"Ошибка: сервис '{service_name}' не найден в {SERVICES_DIR}", file=sys.stderr)
        return False

    cmd = ["docker", "build"]
    if no_cache:
        cmd.append("--no-cache")
    if pull:
        cmd.append("--pull")
    service_dockerfile = service_path / "Dockerfile"
    if service_dockerfile.exists() and service_name in ("telegram-bot", "lightweight-embeddings", "test-runner"):
        if service_name == "test-runner":
            cmd.extend(["-f", str(service_path / "Dockerfile"), "."])
        else:
            cmd.extend(["-f", str(service_dockerfile), str(service_path)])
        if service_name == "lightweight-embeddings" and os.environ.get("USE_OFFLINE_TORCH"):
            cmd.extend(["--build-arg", f"USE_OFFLINE_TORCH={os.environ.get('USE_OFFLINE_TORCH', '0')}"])
    else:
        cmd.extend([
            "--build-arg", f"SVC={service_name}",
            "-f", str(PROJECT_ROOT / "Dockerfile.service"),
            str(PROJECT_ROOT),
        ])
    cmd.extend(["-t", f"rag-{service_name}:{DEFAULT_TAG}"])
    build_env = {**os.environ, "DOCKER_BUILDKIT": "1"}
    return run_command(cmd, f"Сборка сервиса {service_name}", env=build_env)


def build_all(no_cache: bool = False, pull: bool = False) -> bool:
    """Сборка всех сервисов. Перед сборкой убеждаемся, что базовый образ существует."""
    # Проверяем наличие базового образа
    result = subprocess.run(
        ["docker", "image", "inspect", f"{BASE_IMAGE_NAME}:{DEFAULT_TAG}"],
        capture_output=True,
    )
    if result.returncode != 0:
        print("Базовый образ не найден. Сначала собираем его...")
        if not build_base(no_cache, pull):
            return False
    else:
        print("Базовый образ уже существует.")

    services = get_available_services()
    if not services:
        print("Нет доступных сервисов для сборки.")
        return True

    success = True
    for svc in services:
        if not build_service(svc, no_cache, pull):
            success = False
    return success


def list_services() -> None:
    """Выводит список доступных сервисов."""
    services = get_available_services()
    if services:
        print("Доступные сервисы:")
        for svc in services:
            print(f"  - {svc}")
    else:
        print("В директории services/ нет поддиректорий.")


def remove_image(image_name: str, force: bool = True) -> bool:
    """
    Удаляет Docker-образ.
    При force=True использует -f для принудительного удаления.
    Возвращает True, если образ успешно удалён или отсутствовал.
    """
    cmd = ["docker", "rmi"]
    if force:
        cmd.append("-f")
    cmd.append(image_name)
    try:
        subprocess.run(cmd, check=True, capture_output=True, cwd=PROJECT_ROOT)
        return True
    except subprocess.CalledProcessError as e:
        # Если образ не найден, считаем это успехом
        if b"No such image" in e.stderr:
            return True
        print(f"Ошибка при удалении образа {image_name}: {e.stderr.decode().strip()}", file=sys.stderr)
        return False


def prune_system() -> bool:
    """Очищает неиспользуемые данные Docker (контейнеры, сети, кэш сборки)."""
    return run_command(["docker", "system", "prune", "-f"], "Очистка неиспользуемых ресурсов Docker")


def generate_nginx_whitelist() -> bool:
    """
    Генерирует nginx/conf.d/allow_ips.conf из .env (IP_ADDR_WHITELIST).
    Всегда добавляет 127.0.0.1 и ::1 для localhost/e2e с VPS на себя.
    """
    env_path = PROJECT_ROOT / ".env"
    _load_dotenv_simple(env_path)
    raw = os.environ.get("IP_ADDR_WHITELIST", "").strip()
    # Комма/пробелы; пустые и комментарии отбрасываем
    parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip() and not p.strip().startswith("#")]
    allowed = set(parts)
    allowed.add("127.0.0.1")
    allowed.add("::1")
    lines = ["# Generated by build.py from IP_ADDR_WHITELIST (.env). Do not edit by hand.\n"]
    for ip in sorted(allowed):
        lines.append(f"allow {ip};\n")
    lines.append("deny all;\n")
    NGINX_ALLOW_IPS_CONF.parent.mkdir(parents=True, exist_ok=True)
    NGINX_ALLOW_IPS_CONF.write_text("".join(lines), encoding="utf-8")
    print(f"Сгенерирован {NGINX_ALLOW_IPS_CONF} ({len(allowed)} адресов).")
    return True


def run_docker_compose_up(gpu: bool = False) -> bool:
    """Запускает docker compose up -d. Проверяет LLM API: при валидном ключе — без ollama, иначе — с ollama."""
    generate_nginx_whitelist()
    compose_cmd = ["docker", "compose", "-f", COMPOSE_FILE]
    if gpu:
        compose_cmd.extend(["-f", COMPOSE_GPU_FILE])
    # Проверка LLM vendor API (скрипт возвращает 0 при валидном ключе)
    # Выбор overlay определяет, какой embedding-сервис используется: не задаём EMBEDDING_SERVICE_URL в .env.
    # exit 0 (vendor OK) → lightweight-embeddings; exit 1 (local/fallback) → embedding-service (ollama.yml).
    check_result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "check_llm_vendor.py")],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )
    if check_result.returncode == 0:
        compose_cmd.extend(["-f", str(PROJECT_ROOT / "docker-compose.vendor-embeddings.yml")])
        desc = "Запуск Docker Compose (vendor API OK, lightweight-embeddings)"
    else:
        compose_cmd.extend(["-f", str(PROJECT_ROOT / "docker-compose.ollama.yml")])
        if gpu:
            compose_cmd.extend(["-f", str(PROJECT_ROOT / "docker-compose.ollama-gpu.yml")])
        desc = "Запуск Docker Compose (fallback на Ollama + rag-embedding-service)"
    compose_cmd.extend(["up", "-d", "--remove-orphans"])
    return run_command(compose_cmd, desc)


def _load_dotenv_simple(env_path: Path) -> None:
    """Загружает переменные из .env в os.environ (простой парсер, без экспорта/кавычек)."""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _get_postgres_network() -> Optional[str]:
    """Определяет сеть Docker, в которой запущен контейнер postgres (rag-postgres)."""
    result = subprocess.run(
        ["docker", "inspect", "rag-postgres", "--format", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip().split("\n")[0]


def _alembic_docker_base(network: str, database_url: str) -> List[str]:
    return [
        "docker", "run", "--rm",
        "--network", network,
        "-v", f"{PROJECT_ROOT}:/app",
        "-w", "/app",
        "-e", f"DATABASE_URL={database_url}",
        f"{BASE_IMAGE_NAME}:{DEFAULT_TAG}",
    ]


def _alembic_current_revs(database_url: str) -> set:
    """Возвращает множество текущих ревизий по DATABASE_URL (пустое, если БД не под миграциями)."""
    network = _get_postgres_network()
    if not network:
        return set()
    cmd = _alembic_docker_base(network, database_url) + ["python", "-m", "alembic", "current"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if r.returncode != 0:
        return set()
    revs = set()
    for line in (r.stdout or "").strip().splitlines():
        part = line.strip().split()
        if part:
            revs.add(part[0])
    return revs


def _alembic_head_revs(database_url: str) -> set:
    """Возвращает множество ревизий head по конфигу alembic."""
    network = _get_postgres_network()
    if not network:
        return set()
    cmd = _alembic_docker_base(network, database_url) + ["python", "-m", "alembic", "heads"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if r.returncode != 0:
        return set()
    return set(s.strip() for s in (r.stdout or "").strip().splitlines() if s.strip())


def _run_alembic_for_url(database_url: str, description: str) -> bool:
    """Запуск alembic upgrade head для заданного DATABASE_URL; пропуск, если уже at head."""
    network = _get_postgres_network()
    if not network:
        return False
    current = _alembic_current_revs(database_url)
    heads = _alembic_head_revs(database_url)
    if current and current <= heads:
        print(f"\n>>> {description}")
        print("Текущая ревизия уже at head — миграции не выполняются.")
        return True
    cmd = _alembic_docker_base(network, database_url) + ["python", "-m", "alembic", "upgrade", "head"]
    return run_command(cmd, description)


def run_migrations() -> bool:
    """
    Применяет миграции PostgreSQL (alembic upgrade head) для основной и тестовой БД.
    """
    env_path = PROJECT_ROOT / ".env"
    _load_dotenv_simple(env_path)
    user = os.environ.get("POSTGRES_USER", "assistant_user")
    password = os.environ.get("POSTGRES_PASSWORD", "strongpassword")
    db = os.environ.get("POSTGRES_DB", "assistant")
    database_url = "postgresql://{}:{}@postgres:5432/{}".format(user, quote_plus(password), db)

    network = _get_postgres_network()
    if not network:
        print("Ошибка: не удалось определить сеть контейнера postgres (убедитесь, что docker-compose up уже выполнен).", file=sys.stderr)
        return False

    if not _run_alembic_for_url(database_url, "Применение миграций PostgreSQL (alembic upgrade head)"):
        return False

    test_db = os.environ.get("POSTGRES_TEST_DB", "").strip()
    if test_db:
        subprocess.run(
            ["docker", "exec", "rag-postgres", "psql", "-U", user, "-d", db, "-v", "ON_ERROR_STOP=0",
             "-c", f"CREATE DATABASE {test_db};"],
            capture_output=True,
            cwd=PROJECT_ROOT,
        )
        test_db_url = "postgresql://{}:{}@postgres:5432/{}".format(user, quote_plus(password), test_db)
        if not _run_alembic_for_url(test_db_url, f"Применение миграций PostgreSQL (тестовая БД {test_db})"):
            return False
    return True


def run_unit_tests() -> bool:
    """Собирает и запускает unit-тесты в директории services (pytest по services/)."""
    py = _get_python_for_tests()
    base_cmd = [py, "-m", "pytest", "-q", str(SERVICES_DIR), "-v", "--tb=short"]
    tele_dir = SERVICES_DIR / "telegram-bot" / "tests"
    if tele_dir.exists():
        base_cmd.extend(["--ignore", str(tele_dir)])
    if not run_command(base_cmd, "Запуск unit-тестов (pytest services/, без telegram-bot)"):
        return False
    if tele_dir.exists():
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SERVICES_DIR / "telegram-bot") + (os.pathsep + env.get("PYTHONPATH", ""))
        print("\n>>> Запуск unit-тестов telegram-bot (pytest services/telegram-bot/tests/)")
        try:
            subprocess.run(
                [py, "-m", "pytest", "-q", str(tele_dir), "-v", "--tb=short"],
                check=True,
                cwd=PROJECT_ROOT,
                env=env,
            )
        except subprocess.CalledProcessError:
            return False
    return True


def _compose_cmd_with_overlay(gpu: bool = False) -> List[str]:
    """Формирует базовую команду docker compose с нужными -f (тот же выбор overlay, что и для up)."""
    cmd = ["docker", "compose", "-f", COMPOSE_FILE]
    if gpu:
        cmd.extend(["-f", COMPOSE_GPU_FILE])
    check_result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "check_llm_vendor.py")],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )
    if check_result.returncode == 0:
        cmd.extend(["-f", str(PROJECT_ROOT / "docker-compose.vendor-embeddings.yml")])
    else:
        cmd.extend(["-f", str(PROJECT_ROOT / "docker-compose.ollama.yml")])
        if gpu:
            cmd.extend(["-f", str(PROJECT_ROOT / "docker-compose.ollama-gpu.yml")])
    return cmd


def run_tests_via_runner(gpu: bool = False) -> bool:
    """Запускает тесты через контейнер test-runner (docker compose run --rm test-runner)."""
    cmd = _compose_cmd_with_overlay(gpu)
    cmd.extend(["--profile", "test", "run", "--rm", "test-runner"])
    return run_command(cmd, "Запуск тестов (test-runner контейнер)")


def run_tests_with_coverage() -> bool:
    """Запускает unit + e2e тесты с отчётом покрытия (pytest-cov)."""
    py = _get_python_for_tests()
    tele_dir = SERVICES_DIR / "telegram-bot" / "tests"
    cmd = [
        py, "-m", "pytest", "-q",
        str(SERVICES_DIR),
        "tests/test_e2e_ingestion_flow.py",
        "tests/test_e2e_rag_pipeline.py",
        "tests/test_e2e_match_vacancy.py",
        "tests/test_e2e_telegram_links_phones.py",
        "tests/test_telemetry_safeguard.py",
        "tests/test_resume_converter_format.py",
        "tests/test_e2e_resume_convert_structure.py",
        "-v", "--tb=short",
        "--cov=services", "--cov-report=term-missing",
    ]
    if tele_dir.exists():
        cmd.extend(["--ignore", str(tele_dir)])
    if not run_command(cmd, "Запуск тестов с отчётом покрытия (pytest-cov)"):
        return False
    if tele_dir.exists():
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SERVICES_DIR / "telegram-bot") + (os.pathsep + env.get("PYTHONPATH", ""))
        print("\n>>> Запуск unit-тестов telegram-bot")
        try:
            subprocess.run(
                [py, "-m", "pytest", "-q", str(tele_dir), "-v", "--tb=short", "--cov=services", "--cov-append", "--cov-report=term-missing"],
                check=True,
                cwd=PROJECT_ROOT,
                env=env,
            )
        except subprocess.CalledProcessError:
            return False
    return True


def run_e2e_tests() -> bool:
    """Запускает pytest для e2e теста инжеста (предпоследний шаг перед сборкой PNG)."""
    cmd = [_get_python_for_tests(), "-m", "pytest", "-q", "tests/test_e2e_ingestion_flow.py", "-v"]
    return run_command(cmd, "Запуск e2e тестов (pytest test_e2e_ingestion_flow)")


def run_e2e_rag_tests() -> bool:
    """Запускает pytest для e2e теста RAG pipeline через API gateway."""
    cmd = [_get_python_for_tests(), "-m", "pytest", "-q", "tests/test_e2e_rag_pipeline.py", "-v"]
    return run_command(cmd, "Запуск e2e тестов (pytest test_e2e_rag_pipeline)")


def run_e2e_match_vacancy_tests() -> bool:
    """Запускает pytest для e2e теста FLOW-03 match-vacancy через API gateway."""
    cmd = [_get_python_for_tests(), "-m", "pytest", "-q", "tests/test_e2e_match_vacancy.py", "-v"]
    return run_command(cmd, "Запуск e2e тестов (pytest test_e2e_match_vacancy)")


def process_mmd_files() -> bool:
    """
    Конвертирует все .mmd файлы из корня проекта в PNG с помощью mermaid-cli в Docker.
    Результаты сохраняются в папку diagrams/ (создаётся при необходимости).
    """
    print("\n>>> Обработка Mermaid диаграмм")
    mmd_files = list(PROJECT_ROOT.glob("*.mmd"))
    if not mmd_files:
        print("Нет .mmd файлов в корне проекта.")
        return True

    diagrams_dir = PROJECT_ROOT / "diagrams"
    diagrams_dir.mkdir(exist_ok=True)

    # Используем официальный образ mermaid-cli
    image = "minlag/mermaid-cli:latest"

    success = True
    for mmd_file in mmd_files:
        output_file = diagrams_dir / (mmd_file.stem + ".png")
        # Пути внутри контейнера относительно смонтированного /data
        in_container_input = f"/data/{mmd_file.name}"
        in_container_output = f"/data/diagrams/{output_file.name}"

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{PROJECT_ROOT}:/data",
            image,
            "-i", in_container_input,
            "-o", in_container_output
        ]
        if not run_command(cmd, f"Конвертация {mmd_file.name} в PNG"):
            success = False
    return success


def _get_public_tables(user: str, db: str) -> List[str]:
    """Возвращает список имён таблиц в схеме public (исключая alembic_version)."""
    r = subprocess.run(
        ["docker", "exec", "rag-postgres", "psql", "-U", user, "-d", db,
         "-t", "-A", "-v", "ON_ERROR_STOP=1",
         "-c", "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename != 'alembic_version';"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if r.returncode != 0:
        return []
    return [s.strip() for s in (r.stdout or "").strip().splitlines() if s.strip()]


def _ensure_containers_running() -> bool:
    """Проверяет, что контейнеры postgres, redis, qdrant запущены. Возвращает True если все на месте."""
    required = ["rag-postgres", "rag-redis", "rag-qdrant"]
    for name in required:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        if r.returncode != 0 or r.stdout.strip().lower() != "true":
            print(f"Ошибка: контейнер {name} не запущен. Выполните docker-compose up -d.", file=sys.stderr)
            return False
    return True


def run_erase() -> bool:
    """
    Очистка пользовательских данных: кэш Redis, векторная БД (Qdrant), все таблицы PostgreSQL
    схемы public (кроме alembic_version). Требует запущенные контейнеры postgres, redis, qdrant.
    """
    env_path = PROJECT_ROOT / ".env"
    _load_dotenv_simple(env_path)
    if not _ensure_containers_running():
        return False

    user = os.environ.get("POSTGRES_USER", "assistant_user")
    db = os.environ.get("POSTGRES_DB", "assistant")
    qdrant_collection = os.environ.get("QDRANT_COLLECTION", "documents")
    network = _get_postgres_network()
    if not network:
        print("Ошибка: не удалось определить сеть контейнера postgres.", file=sys.stderr)
        return False

    # 1. Redis — полная очистка кэша
    if not run_command(
        ["docker", "exec", "rag-redis", "redis-cli", "FLUSHALL"],
        "Очистка кэша Redis (FLUSHALL)",
    ):
        return False

    # 2. PostgreSQL — все таблицы схемы public (кроме alembic_version)
    tables = _get_public_tables(user, db)
    if tables:
        truncate_sql = "TRUNCATE TABLE " + ", ".join(tables) + " CASCADE;"
        if not run_command(
            ["docker", "exec", "rag-postgres", "psql", "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-c", truncate_sql],
            f"Очистка таблиц PostgreSQL ({len(tables)} таблиц)",
        ):
            return False
    else:
        print("\n>>> Очистка таблиц PostgreSQL — таблиц в public не найдено.")

    # 3. Qdrant — удаление коллекции (при следующем старте ingestion она будет создана заново)
    delete_url = f"http://qdrant:6333/collections/{qdrant_collection}"
    cmd = [
        "docker", "run", "--rm",
        "--network", network,
        "curlimages/curl:latest",
        "-s", "-w", "%{http_code}", "-o", "/dev/null",
        "-X", "DELETE", delete_url,
    ]
    print(f"\n>>> Очистка векторной БД Qdrant (удаление коллекции {qdrant_collection})")
    print(f"Команда: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
        code = result.stdout.strip() or result.stderr.strip()
        if result.returncode != 0:
            print(f"Ошибка при вызове Qdrant: {result.stderr or code}", file=sys.stderr)
            return False
        if code and code != "200" and code != "404":
            print(f"Qdrant вернул код {code} (404 = коллекция уже отсутствовала).", file=sys.stderr)
            # 404 считаем успехом (коллекции не было)
        return True
    except Exception as e:
        print(f"Ошибка при выполнении: {e}", file=sys.stderr)
        return False


def post_rebuild_steps(gpu: bool = False) -> bool:
    """Общие действия после любой пересборки: docker-compose up, миграции PostgreSQL."""
    if not run_docker_compose_up(gpu=gpu):
        return False
    if not run_migrations():
        return False
    return True


def rebuild_base(no_cache: bool = False, pull: bool = False, prune: bool = False, gpu: bool = False, total_erase: bool = False, erase_db: bool = False, run_tests: bool = False, render_png: bool = False) -> bool:
    """Пересборка базового образа: удалить старый, собрать заново, затем запустить сервисы и обработать диаграммы."""
    print("=== Пересборка базового образа ===")
    
    if total_erase:
        print("\n>>> Очистка Docker (контейнеры, образы, build cache)")
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "down", "--remove-orphans"],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        result = subprocess.run(
            ["docker", "images", "--filter", "reference=rag-*", "-q"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        image_ids = result.stdout.strip().split()
        if image_ids:
            subprocess.run(["docker", "rmi", "-f"] + image_ids, capture_output=True, cwd=PROJECT_ROOT)
            print(f"Удалено {len(image_ids)} образов проекта")
        subprocess.run(["docker", "builder", "prune", "-af"], capture_output=True, cwd=PROJECT_ROOT)
        print("Docker очищен")
    
    if not remove_image(f"{BASE_IMAGE_NAME}:{DEFAULT_TAG}"):
        return False
    if prune:
        prune_system()
    if not build_base(no_cache, pull):
        return False
    if not post_rebuild_steps(gpu=gpu):
        return False
    if (total_erase or erase_db) and not run_erase():
        return False
    if run_tests:
        if not run_tests_via_runner(gpu=gpu):
            return False
    if render_png and not process_mmd_files():
        return False
    return True


def rebuild_service(service_name: str, no_cache: bool = False, pull: bool = False, prune: bool = False, gpu: bool = False, total_erase: bool = False, erase_db: bool = False, run_tests: bool = False, render_png: bool = False) -> bool:
    """Пересборка образа сервиса: удалить старый, собрать заново, затем запустить сервисы и обработать диаграммы."""
    print(f"=== Пересборка сервиса {service_name} ===")
    
    if total_erase:
        print("\n>>> Очистка Docker (контейнеры, образы, build cache)")
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "down", "--remove-orphans"],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        result = subprocess.run(
            ["docker", "images", "--filter", "reference=rag-*", "-q"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        image_ids = result.stdout.strip().split()
        if image_ids:
            subprocess.run(["docker", "rmi", "-f"] + image_ids, capture_output=True, cwd=PROJECT_ROOT)
            print(f"Удалено {len(image_ids)} образов проекта")
        subprocess.run(["docker", "builder", "prune", "-af"], capture_output=True, cwd=PROJECT_ROOT)
        print("Docker очищен")
    
    image = f"rag-{service_name}:{DEFAULT_TAG}"
    if not remove_image(image):
        return False
    if prune:
        prune_system()
    if not build_service(service_name, no_cache, pull):
        return False
    if not post_rebuild_steps(gpu=gpu):
        return False
    if (total_erase or erase_db) and not run_erase():
        return False
    if run_tests:
        if not run_tests_via_runner(gpu=gpu):
            return False
    if render_png and not process_mmd_files():
        return False
    return True


def rebuild_all(no_cache: bool = False, pull: bool = False, prune: bool = False, gpu: bool = False, total_erase: bool = False, erase_db: bool = False, run_tests: bool = False, render_png: bool = False) -> bool:
    """Полная пересборка всех образов: удалить все, собрать заново, затем запустить сервисы и обработать диаграммы."""
    print("=== Полная пересборка всех образов ===")

    # Если --total-erase, сначала останавливаем все контейнеры и очищаем Docker
    if total_erase:
        print("\n>>> Очистка Docker (контейнеры, образы, build cache)")
        # Останавливаем все контейнеры проекта
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "down", "--remove-orphans"],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        # Удаляем все образы проекта
        result = subprocess.run(
            ["docker", "images", "--filter", "reference=rag-*", "-q"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        image_ids = result.stdout.strip().split()
        if image_ids:
            subprocess.run(["docker", "rmi", "-f"] + image_ids, capture_output=True, cwd=PROJECT_ROOT)
            print(f"Удалено {len(image_ids)} образов проекта")
        # Очищаем build cache
        subprocess.run(["docker", "builder", "prune", "-af"], capture_output=True, cwd=PROJECT_ROOT)
        print("Docker очищен")

    # Удаляем базовый образ
    if not remove_image(f"{BASE_IMAGE_NAME}:{DEFAULT_TAG}"):
        return False

    # Удаляем образы сервисов
    services = get_available_services()
    for svc in services:
        if not remove_image(f"rag-{svc}:{DEFAULT_TAG}"):
            return False

    if prune:
        prune_system()

    # Собираем всё заново
    if not build_all(no_cache, pull):
        return False

    if not post_rebuild_steps(gpu=gpu):
        return False
    if (total_erase or erase_db) and not run_erase():
        return False
    if run_tests:
        if not run_tests_via_runner(gpu=gpu):
            return False
    if render_png and not process_mmd_files():
        return False
    return True


def main():
    # Загружаем .env в начале, чтобы переменные окружения (в т.ч. опциональная PYTHON_TEST_EXECUTABLE) были доступны
    _load_dotenv_simple(PROJECT_ROOT / ".env")

    epilog = """
Команды и аргументы:
  base              Собрать базовый образ
                    --no-cache, --pull
  service NAME      Собрать сервис
                    --no-cache, --pull
  all               Собрать все сервисы
                    --no-cache, --pull
  list              Показать список сервисов
  rebuild base      Пересобрать базовый образ
                    --no-cache, --pull, --prune, --gpu, --total-erase, --erase-db, --run-tests, --render-png
  rebuild service NAME
                    Пересобрать сервис
                    --no-cache, --pull, --prune, --gpu, --total-erase, --erase-db, --run-tests, --render-png
  rebuild all       Пересобрать все образы
                    --no-cache, --pull, --prune, --gpu, --total-erase, --erase-db, --run-tests, --render-png
  up                Запустить контейнеры (docker-compose up -d)
                    --gpu
  generate-nginx-whitelist
                    Сгенерировать nginx/conf.d/allow_ips.conf из IP_ADDR_WHITELIST (.env)

Подробнее: build.py <command> --help
"""
    parser = argparse.ArgumentParser(
        description="Сборка Docker-образов для микросервисов RAG-ассистента.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Команда")

    # Команда base
    base_parser = subparsers.add_parser("base", help="Собрать базовый образ")
    base_parser.add_argument("--no-cache", action="store_true", help="Не использовать кэш при сборке")
    base_parser.add_argument("--pull", action="store_true", help="Всегда пытаться скачать свежие версии базовых образов")

    # Команда service
    service_parser = subparsers.add_parser("service", help="Собрать конкретный сервис")
    service_parser.add_argument("service_name", help="Имя сервиса (папка в services/)")
    service_parser.add_argument("--no-cache", action="store_true", help="Не использовать кэш при сборке")
    service_parser.add_argument("--pull", action="store_true", help="Всегда пытаться скачать свежие версии базовых образов")

    # Команда all
    all_parser = subparsers.add_parser("all", help="Собрать все сервисы")
    all_parser.add_argument("--no-cache", action="store_true", help="Не использовать кэш при сборке")
    all_parser.add_argument("--pull", action="store_true", help="Всегда пытаться скачать свежие версии базовых образов")

    # Команда list
    subparsers.add_parser("list", help="Показать список доступных сервисов")

    # Команда rebuild
    rebuild_parser = subparsers.add_parser("rebuild", help="Пересобрать образ(ы) с предварительным удалением старых")
    rebuild_subparsers = rebuild_parser.add_subparsers(dest="rebuild_target", required=True, help="Цель пересборки")

    # rebuild base
    rebuild_base_parser = rebuild_subparsers.add_parser("base", help="Пересобрать базовый образ")
    rebuild_base_parser.add_argument("--no-cache", action="store_true", help="Не использовать кэш при сборке")
    rebuild_base_parser.add_argument("--pull", action="store_true", help="Всегда пытаться скачать свежие версии базовых образов")
    rebuild_base_parser.add_argument("--prune", action="store_true", help="Очистить неиспользуемые ресурсы Docker после удаления")
    rebuild_base_parser.add_argument("--gpu", action="store_true", help="Запустить compose с docker-compose.gpu.yml (GPU)")
    rebuild_base_parser.add_argument("--total-erase", action="store_true", help="ПОЛНАЯ ОЧИСТКА: остановить контейнеры, удалить образы проекта и build cache, затем очистить Redis, Qdrant и PostgreSQL")
    rebuild_base_parser.add_argument("--erase-db", action="store_true", help="Очистить только кэш Redis, Qdrant и пользовательские таблицы PostgreSQL (без очистки Docker)")
    rebuild_base_parser.add_argument("--run-tests", action="store_true", help="После пересборки запустить тесты через контейнер test-runner")
    rebuild_base_parser.add_argument("--render-png", action="store_true", help="Конвертировать .mmd в PNG в diagrams/")

    # rebuild service
    rebuild_service_parser = rebuild_subparsers.add_parser("service", help="Пересобрать конкретный сервис")
    rebuild_service_parser.add_argument("service_name", help="Имя сервиса (папка в services/)")
    rebuild_service_parser.add_argument("--no-cache", action="store_true", help="Не использовать кэш при сборке")
    rebuild_service_parser.add_argument("--pull", action="store_true", help="Всегда пытаться скачать свежие версии базовых образов")
    rebuild_service_parser.add_argument("--prune", action="store_true", help="Очистить неиспользуемые ресурсы Docker после удаления")
    rebuild_service_parser.add_argument("--gpu", action="store_true", help="Запустить compose с docker-compose.gpu.yml (GPU)")
    rebuild_service_parser.add_argument("--total-erase", action="store_true", help="ПОЛНАЯ ОЧИСТКА: остановить контейнеры, удалить образы проекта и build cache, затем очистить Redis, Qdrant и PostgreSQL")
    rebuild_service_parser.add_argument("--erase-db", action="store_true", help="Очистить только кэш Redis, Qdrant и пользовательские таблицы PostgreSQL (без очистки Docker)")
    rebuild_service_parser.add_argument("--run-tests", action="store_true", help="После пересборки запустить тесты через контейнер test-runner")
    rebuild_service_parser.add_argument("--render-png", action="store_true", help="Конвертировать .mmd в PNG в diagrams/")

    # rebuild all
    rebuild_all_parser = rebuild_subparsers.add_parser("all", help="Пересобрать все образы")
    rebuild_all_parser.add_argument("--no-cache", action="store_true", help="Не использовать кэш при сборке")
    rebuild_all_parser.add_argument("--pull", action="store_true", help="Всегда пытаться скачать свежие версии базовых образов")
    rebuild_all_parser.add_argument("--prune", action="store_true", help="Очистить неиспользуемые ресурсы Docker после удаления")
    rebuild_all_parser.add_argument("--gpu", action="store_true", help="Запустить compose с docker-compose.gpu.yml (GPU)")
    rebuild_all_parser.add_argument("--total-erase", action="store_true", help="ПОЛНАЯ ОЧИСТКА: остановить контейнеры, удалить образы проекта и build cache, затем очистить Redis, Qdrant и PostgreSQL")
    rebuild_all_parser.add_argument("--erase-db", action="store_true", help="Очистить только кэш Redis, Qdrant и пользовательские таблицы PostgreSQL (без очистки Docker)")
    rebuild_all_parser.add_argument("--run-tests", action="store_true", help="После пересборки запустить тесты через контейнер test-runner (pytest с покрытием)")
    rebuild_all_parser.add_argument("--render-png", action="store_true", help="Конвертировать .mmd в PNG в diagrams/")

    # Команда up — только запуск docker-compose (без пересборки)
    up_parser = subparsers.add_parser("up", help="Запустить контейнеры (docker-compose up -d)")
    up_parser.add_argument("--gpu", action="store_true", help="Подключить docker-compose.gpu.yml для GPU")

    # Генерация Nginx IP whitelist
    subparsers.add_parser("generate-nginx-whitelist", help="Сгенерировать nginx/conf.d/allow_ips.conf из .env")

    args = parser.parse_args()

    # Обработка команд
    if args.command == "base":
        success = build_base(args.no_cache, args.pull)
    elif args.command == "service":
        success = build_service(args.service_name, args.no_cache, args.pull)
    elif args.command == "all":
        success = build_all(args.no_cache, args.pull)
    elif args.command == "list":
        list_services()
        success = True
    elif args.command == "rebuild":
        gpu = getattr(args, "gpu", False)
        total_erase = getattr(args, "total_erase", False)
        erase_db = getattr(args, "erase_db", False)
        run_tests = getattr(args, "run_tests", False)
        render_png = getattr(args, "render_png", False)
        if args.rebuild_target == "base":
            success = rebuild_base(args.no_cache, args.pull, args.prune, gpu=gpu, total_erase=total_erase, erase_db=erase_db, run_tests=run_tests, render_png=render_png)
        elif args.rebuild_target == "service":
            success = rebuild_service(args.service_name, args.no_cache, args.pull, args.prune, gpu=gpu, total_erase=total_erase, erase_db=erase_db, run_tests=run_tests, render_png=render_png)
        elif args.rebuild_target == "all":
            success = rebuild_all(args.no_cache, args.pull, args.prune, gpu=gpu, total_erase=total_erase, erase_db=erase_db, run_tests=run_tests, render_png=render_png)
        else:
            success = False
    elif args.command == "up":
        success = run_docker_compose_up(gpu=args.gpu)
    elif args.command == "generate-nginx-whitelist":
        success = generate_nginx_whitelist()
    else:
        parser.print_help()
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()