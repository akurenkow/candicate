@echo off
REM Script to run e2e ingestion tests on Windows
REM Usage: tests\run_e2e_tests.bat [--keep-test-data]

echo === E2E Ingestion Tests ===
echo.

echo Checking services...

REM Check PostgreSQL
docker ps | findstr rag-postgres >nul 2>&1
if errorlevel 1 (
    echo ERROR: PostgreSQL is not running
    echo Start with: docker-compose up -d postgres
    exit /b 1
)
echo [OK] PostgreSQL is running

REM Check Qdrant
docker ps | findstr rag-qdrant >nul 2>&1
if errorlevel 1 (
    echo ERROR: Qdrant is not running
    echo Start with: docker-compose up -d qdrant
    exit /b 1
)
echo [OK] Qdrant is running

REM Check Embedding Service
docker ps | findstr embedding-service >nul 2>&1
if errorlevel 1 (
    echo ERROR: Embedding Service is not running
    echo Start with: docker-compose up -d embedding-service
    exit /b 1
)
echo [OK] Embedding Service is running

REM Check Translation Service
docker ps | findstr translation-service >nul 2>&1
if errorlevel 1 (
    echo ERROR: Translation Service is not running
    echo Start with: docker-compose up -d translation-service
    exit /b 1
)
echo [OK] Translation Service is running

echo.
echo Running tests...
echo.

REM Run pytest with all arguments
pytest tests\test_e2e_ingestion_flow.py -v -s %*

if errorlevel 1 (
    echo.
    echo === Tests failed! ===
    exit /b 1
) else (
    echo.
    echo === All tests passed! ===
    exit /b 0
)
