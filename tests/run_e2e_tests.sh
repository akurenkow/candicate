#!/bin/bash
# Script to run e2e ingestion tests
# Usage: ./tests/run_e2e_tests.sh [--keep-test-data]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== E2E Ingestion Tests ===${NC}"
echo ""

# Check if services are running
echo -e "${YELLOW}Checking services...${NC}"

# Check PostgreSQL
if ! docker ps | grep -q rag-postgres; then
    echo -e "${RED}ERROR: PostgreSQL is not running${NC}"
    echo "Start with: docker-compose up -d postgres"
    exit 1
fi
echo -e "${GREEN}✓ PostgreSQL is running${NC}"

# Check Qdrant
if ! docker ps | grep -q rag-qdrant; then
    echo -e "${RED}ERROR: Qdrant is not running${NC}"
    echo "Start with: docker-compose up -d qdrant"
    exit 1
fi
echo -e "${GREEN}✓ Qdrant is running${NC}"

# Check Embedding Service
if ! docker ps | grep -q embedding-service; then
    echo -e "${RED}ERROR: Embedding Service is not running${NC}"
    echo "Start with: docker-compose up -d embedding-service"
    exit 1
fi
echo -e "${GREEN}✓ Embedding Service is running${NC}"

# Check Translation Service
if ! docker ps | grep -q translation-service; then
    echo -e "${RED}ERROR: Translation Service is not running${NC}"
    echo "Start with: docker-compose up -d translation-service"
    exit 1
fi
echo -e "${GREEN}✓ Translation Service is running${NC}"

echo ""
echo -e "${YELLOW}Running tests...${NC}"
echo ""

# Run pytest with all arguments passed to script
pytest tests/test_e2e_ingestion_flow.py -v -s "$@"

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=== All tests passed! ===${NC}"
else
    echo ""
    echo -e "${RED}=== Tests failed! ===${NC}"
fi

exit $exit_code
