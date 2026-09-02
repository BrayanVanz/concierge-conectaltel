<#
.SYNOPSIS
    Script de automação para deploy da infraestrutura AWS (Terraform) e indexação no OpenSearch.
#>

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "   Iniciando Setup do Concierge ConectaTel        " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 1. Validar e Aplicar Terraform (acessa a pasta terraform no mesmo nível da scripts)
Write-Host "`n[1/4] Provisionando Infraestrutura AWS via Terraform..." -ForegroundColor Yellow
Set-Location -Path "$PSScriptRoot\..\terraform"

terraform init
if ($LASTEXITCODE -ne 0) { throw "Falha na inicialização do Terraform." }

terraform apply -auto-approve
if ($LASTEXITCODE -ne 0) { throw "Falha na aplicação do Terraform." }

# Captura os Outputs gerados pelo Terraform (com fallback de segurança)
$RAW_BUCKET = (terraform output -raw raw_bucket_name 2>$null)

$OPENSEARCH_ENDPOINT = (terraform output -raw opensearch_collection_endpoint 2>$null)
if (-not $OPENSEARCH_ENDPOINT) {
    $OPENSEARCH_ENDPOINT = (terraform output -raw opensearch_endpoint 2>$null)
}

$GUARDRAIL_ID = (terraform output -raw bedrock_guardrail_id 2>$null)
$GUARDRAIL_VERSION = (terraform output -raw bedrock_guardrail_version 2>$null)

$AUDIT_BUCKET = (terraform output -raw audit_bucket_name 2>$null)

# Retorna para a raiz do projeto (um nível acima da pasta scripts)
Set-Location -Path "$PSScriptRoot\.."

if (-not $RAW_BUCKET) {
    throw "Não foi possível obter o nome do bucket raw das saídas do Terraform."
}
if (-not $OPENSEARCH_ENDPOINT) {
    throw "Não foi possível obter o OPENSEARCH_ENDPOINT das saídas do Terraform."
}

$env:OPENSEARCH_ENDPOINT = $OPENSEARCH_ENDPOINT
Write-Host "Bucket raw obtido: $RAW_BUCKET" -ForegroundColor Green
Write-Host "OpenSearch Endpoint obtido: $env:OPENSEARCH_ENDPOINT" -ForegroundColor Green

if ($GUARDRAIL_ID) {
    $env:BEDROCK_GUARDRAIL_ID = $GUARDRAIL_ID
    $env:BEDROCK_GUARDRAIL_VERSION = if ($GUARDRAIL_VERSION) { $GUARDRAIL_VERSION } else { "1" }
    Write-Host "Bedrock Guardrail ID obtido: $env:BEDROCK_GUARDRAIL_ID (v$env:BEDROCK_GUARDRAIL_VERSION)" -ForegroundColor Green
}

if ($AUDIT_BUCKET) {
    $env:AUDIT_BUCKET_NAME = $AUDIT_BUCKET
    Write-Host "Audit Bucket obtido: $env:AUDIT_BUCKET_NAME (sync automático da trilha de auditoria habilitado)" -ForegroundColor Green
} else {
    Write-Host "Aviso: audit_bucket_name não encontrado nas saídas do Terraform. A trilha de auditoria continuará funcionando 100% local, só sem backup automático no S3." -ForegroundColor Yellow
}

# 2. Configurar Ambiente Python e Dependências
Write-Host "`n[2/4] Configurando ambiente virtual Python..." -ForegroundColor Yellow

$PYTHON_CMD = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }

if (-not (Test-Path ".venv")) {
    & $PYTHON_CMD -m venv .venv
}

$ACTIVATE_SCRIPT = if (Test-Path ".\.venv\bin\Activate.ps1") { ".\.venv\bin\Activate.ps1" } else { ".\.venv\Scripts\Activate.ps1" }
& $ACTIVATE_SCRIPT

& $PYTHON_CMD -m pip install --upgrade pip
& $PYTHON_CMD -m pip install -r src/requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Falha na instalação dos pacotes Python." }

# 3. Popular o S3 e rodar o pipeline local (ingestão -> chunking -> embeddings)
#    Antes este script pulava direto para a indexação, usando arquivos
#    pré-gerados em data/embeddings/ sem nunca subir nada pro S3 — por isso
#    os buckets ficavam vazios mesmo depois do terraform apply. Agora:
#      a) sobe o corpus local pro bucket raw (dispara também, de forma
#         assíncrona, a Lambda de ingestão -> chunking -> embeddings no S3,
#         graças às notificações configuradas em s3.tf);
#      b) roda o pipeline local de forma síncrona para regenerar
#         data/chunks/ e data/embeddings/ com o corpus atual, garantindo
#         que a indexação abaixo não dependa do tempo de execução das Lambdas.
Write-Host "`n[3/4] Enviando corpus local para o bucket raw e processando o pipeline..." -ForegroundColor Yellow

& $PYTHON_CMD src/upload_to_s3.py $RAW_BUCKET
if ($LASTEXITCODE -ne 0) { throw "Falha ao enviar o corpus para o S3 (bucket raw)." }

Push-Location "src/chunking"
& $PYTHON_CMD process_chunks.py "../../data/processed/corpus.jsonl" "../../data/chunks"
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Falha ao gerar os chunks localmente." }
Pop-Location

Push-Location "src/embeddings"
& $PYTHON_CMD main.py
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Falha ao gerar os embeddings localmente." }
Pop-Location

# 4. Indexar Embeddings nos 3 Índices do OpenSearch Serverless
Write-Host "`n[4/4] Executando carga nos 3 índices do OpenSearch..." -ForegroundColor Yellow
& $PYTHON_CMD src/embeddings/index_to_opensearch.py --input "data/embeddings" --endpoint $env:OPENSEARCH_ENDPOINT
if ($LASTEXITCODE -ne 0) { throw "Falha durante a indexação no OpenSearch." }

Write-Host "`n==================================================" -ForegroundColor Green
Write-Host "   Setup concluído com sucesso!                   " -ForegroundColor Green
Write-Host "   Execute 'python3 src/agent/cli.py' para testar no Linux/Mac." -ForegroundColor Green
Write-Host "   Execute 'python src\agent\cli.py' para testar no Windows.  " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green