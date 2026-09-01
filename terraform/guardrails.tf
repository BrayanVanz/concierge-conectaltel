resource "aws_bedrock_guardrail" "concierge" {
  name                      = "${var.project_prefix}-guardrail"
  description               = "Guardrail para filtro de topicos fora de escopo e protecao contra prompt injection na ConectaTel"
  blocked_input_messaging   = "Solicitação bloqueada por violar as diretrizes de segurança e escopo da ConectaTel."
  blocked_outputs_messaging = "Como assistente da ConectaTel, só posso responder a dúvidas sobre nossos planos, faturas e serviços oficiais."

  # Proteção contra Prompt Injection / Jailbreak
  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  # Filtro Semântico de Tópicos (Concorrentes e Fora de Escopo)
  topic_policy_config {
    topics_config {
      name       = "Concorrentes"
      definition = "Dúvidas, menções, comparações ou perguntas sobre planos, serviços e ofertas de empresas concorrentes de telecomunicações, como Claro, Vivo, TIM e Oi."
      examples   = [
        "O plano da Claro é mais barato?",
        "Como mudo para a Vivo?",
        "Qual o valor da TIM Fibra?"
      ]
      type       = "DENY"
    }

    topics_config {
      name       = "ForaDoEscopo"
      definition = "Assuntos gerais não relacionados a planos, faturas ou suporte da ConectaTel, como culinária, esportes, geografia, história, curiosidades, piadas, matemática e conhecimentos gerais."
      examples   = [
        "Me dá uma receita de bolo de cenoura?",
        "Qual é a capital da França?",
        "Quem ganhou o jogo de futebol ontem?",
        "Quanto é 25 multiplicado por 14?",
        "Qual é a previsão do tempo para amanhã?"
      ]
      type       = "DENY"
    }
  }
}

resource "aws_bedrock_guardrail_version" "concierge" {
  guardrail_arn = aws_bedrock_guardrail.concierge.guardrail_arn
  description   = "Versao inicial do Guardrail do Concierge ConectaTel"
}

output "bedrock_guardrail_id" {
  description = "ID do Guardrail do Bedrock para uso no agente"
  value       = aws_bedrock_guardrail.concierge.guardrail_id
}

output "bedrock_guardrail_version" {
  description = "Versao publicada do Guardrail do Bedrock"
  value       = aws_bedrock_guardrail_version.concierge.version
}