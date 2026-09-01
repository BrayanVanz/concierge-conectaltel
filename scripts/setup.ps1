<#
.SYNOPSIS
    Script de automação para deploy da infraestrutura AWS (Terraform) e indexação no OpenSearch.
#>

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "   Iniciando Setup do Concierge ConectaTel        " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 1. Validar e Aplicar Terraform (acessa a pasta terraform no mesmo nível da scripts)
Write-Host "`n[1/3] Provisionando Infraestrutura AWS via Terraform..." -ForegroundColor Yellow
Set-Location -Path "$PSScriptRoot\..\terraform"

terraform init
if ($LASTEXITCODE -ne 0) { throw "Falha na inicialização do Terraform." }

terraform apply -auto-approve
if ($LASTEXITCODE -ne 0) { throw "Falha na aplicação do Terraform." }

# Capturar os Outputs gerados pelo Terraform (com fallback de segurança)
$OPENSEARCH_ENDPOINT = (terraform output -raw opensearch_collection_endpoint 2>$null)
if (-not $OPENSEARCH_ENDPOINT) {
    $OPENSEARCH_ENDPOINT = (terraform output -raw opensearch_endpoint 2>$null)
}

$GUARDRAIL_ID = (terraform output -raw bedrock_guardrail_id 2>$null)
$GUARDRAIL_VERSION = (terraform output -raw bedrock_guardrail_version 2>$null)

# Retornar para a raiz do projeto (um nível acima da pasta scripts)
Set-Location -Path "$PSScriptRoot\.."

if (-not $OPENSEARCH_ENDPOINT) {
    throw "Não foi possível obter o OPENSEARCH_ENDPOINT das saídas do Terraform."
}

$env:OPENSEARCH_ENDPOINT = $OPENSEARCH_ENDPOINT
Write-Host "OpenSearch Endpoint obtido: $env:OPENSEARCH_ENDPOINT" -ForegroundColor Green

if ($GUARDRAIL_ID) {
    $env:BEDROCK_GUARDRAIL_ID = $GUARDRAIL_ID
    $env:BEDROCK_GUARDRAIL_VERSION = if ($GUARDRAIL_VERSION) { $GUARDRAIL_VERSION } else { "1" }
    Write-Host "Bedrock Guardrail ID obtido: $env:BEDROCK_GUARDRAIL_ID (v$env:BEDROCK_GUARDRAIL_VERSION)" -ForegroundColor Green
}

# 2. Configurar Ambiente Python e Dependências
Write-Host "`n[2/3] Configurando ambiente virtual Python..." -ForegroundColor Yellow

# Detectar comando Python disponível (python3 no Linux / python no Windows)
$PYTHON_CMD = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }

if (-not (Test-Path ".venv")) {
    & $PYTHON_CMD -m venv .venv
}

# Detectar script de ativação (.venv/bin no Linux / .venv/Scripts no Windows)
$ACTIVATE_SCRIPT = if (Test-Path ".\.venv\bin\Activate.ps1") { ".\.venv\bin\Activate.ps1" } else { ".\.venv\Scripts\Activate.ps1" }
& $ACTIVATE_SCRIPT

# Instalar requisitos
& $PYTHON_CMD -m pip install --upgrade pip
& $PYTHON_CMD -m pip install -r src/requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Falha na instalação dos pacotes Python." }

# 3. Indexar Embeddings nos 3 Índices do OpenSearch Serverless
Write-Host "`n[3/3] Executando carga nos 3 índices do OpenSearch..." -ForegroundColor Yellow
& $PYTHON_CMD src/embeddings/index_to_opensearch.py --input "data/embeddings" --endpoint $env:OPENSEARCH_ENDPOINT
if ($LASTEXITCODE -ne 0) { throw "Falha durante a indexação no OpenSearch." }

Write-Host "`n==================================================" -ForegroundColor Green
Write-Host "   Setup concluído com sucesso!                   " -ForegroundColor Green
Write-Host "   Execute 'python3 src/agent/cli.py' para testar no Linux/Mac." -ForegroundColor Green
Write-Host "   Execute 'python src\agent\cli.py' para testar no Windows.  " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green