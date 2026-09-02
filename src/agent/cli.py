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
        default=None,
        choices=["fixed_windows", "full_document", "hierarchical_semantic"],
        help="Estratégia de chunking/índice no OpenSearch (se omitido, usa o padrão do agent.py)"
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
        default=None,
        help="Limiar de relevância mínima para o RAG (se omitido, usa o padrão do agent.py)"
    )
    args = parser.parse_args()

    # Passa os parâmetros para o agente apenas se forem informados explicitamente
    agent_kwargs = {}
    if args.strategy:
        agent_kwargs["chunk_strategy"] = args.strategy
    if args.threshold is not None:
        agent_kwargs["score_threshold"] = args.threshold

    try:
        agent = ConciergeAgent(**agent_kwargs)
    except Exception as e:
        print(f"Erro ao inicializar o agente: {e}")
        sys.exit(1)

    print("=" * 60)
    print("        CONCIERGE CONECTATEL - ATENDIMENTO INTERATIVO        ")
    print("=" * 60)
    # Reflete os valores reais atribuídos dentro da instância do agente
    print(f"Estratégia de Busca : {agent.chunk_strategy}")
    print(f"ID do Usuário        : {args.user_id}")
    print(f"Limiar do OpenSearch : {agent.score_threshold}")
    print("Digite 'sair' ou 'exit' para encerrar o chat.\n")

    conversation_history = []

    while True:
        try:
            user_input = input("Você > ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["sair", "exit"]:
                print("\nEncerrando o atendimento. Até logo!")
                break

            result = agent.process_message(
                user_id=args.user_id,
                query=user_input,
                conversation_history=conversation_history
            )

            status = result.get("status", "UNKNOWN")
            response = result.get("response", "")
            sources = result.get("sources", [])

            print(f"\n[Status: {status}]")
            print(f"Concierge > {response}\n")

            if sources and status == "ANSWERED":
                print(f"   [Fontes: {', '.join(sources)}]\n")

            # Atualiza o histórico local da conversa
            conversation_history.append({"role": "user", "content": user_input})
            conversation_history.append({"role": "assistant", "content": response})

        except KeyboardInterrupt:
            print("\n\nAtendimento encerrado pelo usuário.")
            break
        except Exception as e:
            print(f"\nErro ao processar mensagem: {e}\n")

if __name__ == "__main__":
    main()