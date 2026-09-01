<#
.SYNOPSIS
    Script de desmontagem e destruição de todos os recursos AWS provisionados.
#>

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Red
Write-Host "   Destruindo Infraestrutura do Concierge          " -ForegroundColor Red
Write-Host "==================================================" -ForegroundColor Red

# Define o caminho absoluto para a pasta terraform na raiz do projeto
$TerraformDir = Join-Path $PSScriptRoot "../terraform"

if (Test-Path $TerraformDir) {
    Set-Location -Path $TerraformDir

    # Confirmar se o diretório foi inicializado antes de destruir
    if (Test-Path ".terraform") {
        Write-Host "`n[1/1] Executando terraform destroy..." -ForegroundColor Yellow
        terraform destroy -auto-approve
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "`nRecursos destruídos com sucesso." -ForegroundColor Green
        } else {
            Write-Host "`nOcorreu um erro ao tentar destruir os recursos via Terraform." -ForegroundColor Red
        }
    } else {
        Write-Host "`nPasta .terraform não encontrada. Nada para destruir." -ForegroundColor Yellow
    }

    # Retorna para a raiz do projeto
    Set-Location -Path "$PSScriptRoot/.."
} else {
    Write-Host "`nDiretório 'terraform' não encontrado em: $TerraformDir" -ForegroundColor Red
}

Write-Host "`n==================================================" -ForegroundColor Red
Write-Host "   Processo de destruição finalizado.              " -ForegroundColor Red
Write-Host "==================================================" -ForegroundColor Red