# Запуск docker-compose с авто-выбором: vendor (без ollama) или local (с ollama).
# При валидном API ключе — не стартует ollama. При невалидном — fallback на ollama.
# Использование:
#   .\scripts\compose-up.ps1 [-Detached] [-Gpu]
#   -Detached: фон
#   -Gpu: добавить docker-compose.gpu.yml

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Detached = ""
$ComposeFiles = @("-f", "docker-compose.yml")
$UseGpu = $false
foreach ($arg in $args) {
    if ($arg -eq "-Detached" -or $arg -eq "-d") { $Detached = "-d" }
    elseif ($arg -eq "-Gpu" -or $arg -eq "--gpu") { $UseGpu = $true }
}

if ($UseGpu) { $ComposeFiles += "-f"; $ComposeFiles += "docker-compose.gpu.yml" }

Write-Host ">>> Проверка LLM API (scripts/check_llm_vendor.py)..."
python scripts/check_llm_vendor.py 2>$null
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host ">>> Vendor API OK — запуск без ollama, с lightweight-embeddings"
    $ComposeFiles += "-f"; $ComposeFiles += "docker-compose.vendor-embeddings.yml"
    & docker compose $ComposeFiles up $Detached --remove-orphans
} else {
    Write-Host ">>> Fallback на Ollama — добавление docker-compose.ollama.yml"
    $ComposeFiles += "-f"; $ComposeFiles += "docker-compose.ollama.yml"
    if ($UseGpu) { $ComposeFiles += "-f"; $ComposeFiles += "docker-compose.ollama-gpu.yml" }
    & docker compose $ComposeFiles up $Detached --remove-orphans
}
