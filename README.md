# Concierge ConectaTel

O **Concierge ConectaTel** é um agente de atendimento baseado em **RAG (Retrieval-Augmented Generation)**, desenvolvido para consultar um corpus de conhecimento e gerar respostas relevantes por meio de diferentes estratégias de recuperação.

## Como executar

### 1. Subir a infraestrutura, o corpus e os índices

#### Windows

```powershell
.\scripts\setup.ps1
```

#### Linux/Mac

```bash
./scripts/setup.sh
```

> Caso o script não tenha permissão de execução no Linux/Mac, execute:
>
> ```bash
> chmod +x ./scripts/setup.sh
> ```

---

### 2. Ativar o ambiente virtual

#### Windows

```powershell
.\.venv\Scripts\Activate.ps1
```

#### Linux/Mac

```bash
source .venv/bin/activate
```

---

### 3. Testar o agente pelo CLI

Com o ambiente virtual ativado, execute:

```bash
python src/agent/cli.py
```

O CLI possui os seguintes parâmetros:

- `--strategy`: define a estratégia de recuperação utilizada pelo RAG.
  - `fixed_windows`
  - `full_document`
  - `hierarchical_semantic`

- `--threshold`: define o limiar mínimo de relevância para os resultados recuperados pelo RAG.

#### Exemplo

```bash
python src/agent/cli.py --strategy hierarchical_semantic --threshold 0.7
```

---

### 4. Destruir a infraestrutura ao terminar

#### Windows

```powershell
.\scripts\destroy.ps1
```

#### Linux/Mac

```bash
./scripts/destroy.sh
```

> Caso o script não tenha permissão de execução no Linux/Mac, execute:
>
> ```bash
> chmod +x ./scripts/destroy.sh
> ```

## Fluxo resumido

### Windows

```powershell
# 1. Subir infraestrutura, corpus e índices
.\scripts\setup.ps1

# 2. Ativar ambiente virtual
.\.venv\Scripts\Activate.ps1

# 3. Executar o agente
python src/agent/cli.py --strategy hierarchical_semantic --threshold 0.7

# 4. Destruir infraestrutura
.\scripts\destroy.ps1
```

### Linux/Mac

```bash
# 1. Subir infraestrutura, corpus e índices
./scripts/setup.sh

# 2. Ativar ambiente virtual
source .venv/bin/activate

# 3. Executar o agente
python src/agent/cli.py --strategy hierarchical_semantic --threshold 0.7

# 4. Destruir infraestrutura
./scripts/destroy.sh
```
