import os
import sys
import json
import argparse

# Adiciona a raiz do projeto ao PATH (subindo dois níveis a partir de src/agent/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.agent.agent import ConciergeAgent

def main():
    parser = argparse.ArgumentParser(description="CLI Interativo - Concierge ConectaTel")
    parser.add_argument(
        "--strategy",
        type=str,
        default="full_document",
        choices=["fixed_windows", "full_document", "hierarchical_semantic"],
        help="Estratégia de chunking/índice no OpenSearch (padrão: full_document)"
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default="cli_user_01",
        help="ID do usuário para simulação de atendimento"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.68,
        help="Limiar de relevância mínima para o RAG (padrão: 0.68)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("        CONCIERGE CONECTATEL - ATENDIMENTO INTERATIVO        ")
    print("=" * 60)
    print(f"Estratégia de Busca : {args.strategy}")
    print(f"ID do Usuário        : {args.user_id}")
    print(f"Limiar do OpenSearch : {args.threshold}")
    print("Digite 'sair' ou 'exit' para encerrar o chat.\n")

    try:
        agent = ConciergeAgent(
            chunk_strategy=args.strategy,
            score_threshold=args.threshold
        )
    except Exception as e:
        print(f"Erro ao inicializar o agente: {e}")
        sys.exit(1)

    conversation_history = []

    while True:
        try:
            user_input = input("\nVocê > ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["sair", "exit", "quit", "q"]:
                print("\nSessão encerrada com sucesso.")
                break

            result = agent.process_message(
                user_id=args.user_id,
                query=user_input,
                conversation_history=conversation_history
            )

            status = result.get("status", "UNKNOWN")
            response = result.get("response", "")
            trace_id = result.get("trace_id", "N/A")

            print(f"\n[Status: {status}] [trace_id: {trace_id}]")
            print(f"Concierge > {response}")

            if status == "ANSWERED" and result.get("sources"):
                print(f"   [Fontes: {', '.join(result['sources'])}]")

            elif status == "ESCALATED" and result.get("handoff"):
                print("\n[Payload de Escalonamento Humano]:")
                print(json.dumps(result["handoff"], indent=2, ensure_ascii=False))

            conversation_history.append({"role": "user", "content": user_input})
            conversation_history.append({"role": "assistant", "content": response})

        except KeyboardInterrupt:
            print("\n\nSessão encerrada.")
            break
        except Exception as e:
            print(f"\nErro ao processar mensagem: {e}")

if __name__ == "__main__":
    main()