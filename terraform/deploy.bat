@echo off
echo Aplicando Terraform...
terraform init
terraform apply -auto-approve
echo.
echo Infraestrutura criada com sucesso!
echo.
echo Para obter o nome do bucket raw, execute:
echo terraform output raw_bucket_name