"""
CLI de consulta da trilha de auditoria — usado na "pergunta de auditoria"
da banca (reconstrução em até 60 segundos a partir de um trace_id).

Uso:
    python -m src.audit.lookup_cli cct-8f2a1b3c

Sem depender de nenhum serviço externo: lê diretamente o audit_log.jsonl
local (ou o caminho definido em AUDIT_LOG_PATH).
"""

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.audit.audit_log import get_trace, format_trace


def main():
    if len(sys.argv) != 2:
        print("Uso: python -m src.audit.lookup_cli <trace_id>")
        sys.exit(1)

    trace_id = sys.argv[1].strip()
    record = get_trace(trace_id)

    if not record:
        print(f"[NÃO ENCONTRADO] Nenhum registro com trace_id={trace_id}")
        sys.exit(1)

    print("=" * 60)
    print(f"REGISTRO DE AUDITORIA — {trace_id}")
    print("=" * 60)
    print(format_trace(record))


if __name__ == "__main__":
    main()