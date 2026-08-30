"""
Benchmark de precisão de recuperação para o Concierge ConectaTel.

Objetivo:
    Medir se a busca por similaridade de embeddings coloca, entre os
    primeiros resultados (top_k), o chunk da versão VIGENTE do documento
    relevante para cada pergunta de cliente. Essa é a métrica de "o modelo
    recupera a informação certa".

    O benchmark testa três cortes — top_k em {2, 3, 4} — na mesma execução,
    para permitir comparar o efeito do corte tanto na recuperação quanto na
    contaminação de versão (abaixo). Não é necessário rodar o script mais de
    uma vez: um relatório já traz os três valores lado a lado.

Diagnóstico secundário — contaminação de versão:
    O relatório também mede com que frequência um chunk REVOGADO aparece no
    top_k quando a busca NÃO filtra por status. Esse número NÃO conta contra
    a precisão do modelo: as versões v1 (revogada) e v2 (vigente) da política
    de reembolso são textos quase idênticos, e nenhum embedding as separa
    sozinho. O diagnóstico existe para mostrar por que a camada de
    recuperação precisa filtrar por vigência ANTES de ranquear.

Critério de acerto (métrica primária):
    Uma consulta é ACERTO em um dado top_k se pelo menos um chunk com status
    "vigente" e doc_family_id igual ao da consulta aparece entre os top_k
    primeiros resultados.

Sobre as consultas de teste (TEST_QUERIES):
    Este corpus tem apenas UMA família de documentos com duas versões
    (vigente + revogada): 'pol-reembolso'. As consultas abaixo foram
    escritas manualmente, no estilo de perguntas reais de um cliente sobre
    reembolso e contestação de fatura — são as únicas queries que fazem
    sentido para medir confusão de versão neste corpus específico. Ajuste
    ou amplie esta lista livremente conforme o squad achar necessário;
    ela foi deixada explícita (em vez de gerada automaticamente) para que
    qualquer pessoa do squad possa revisar exatamente o que está sendo
    testado antes da apresentação.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("benchmark_version_accuracy")


# ---------------------------------------------------------------------------
# Configuração do benchmark
# ---------------------------------------------------------------------------

EMBEDDING_MODEL_ID = "cohere.embed-v4:0"
TOP_K_VALUES = (2, 3, 4)

# Caminhos padrão, derivados da posição deste arquivo
# (benchmarks/ -> embeddings/ -> src/ -> raiz do repositório).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_EMBEDDED_PATH = _REPO_ROOT / "data" / "embeddings" / "chunks_hierarchical_semantic_embedded.jsonl"
_DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"

# top_k recomendado para a camada de busca/agente (fora do escopo deste
# módulo). Não afeta a execução — o benchmark sempre testa os três valores
# em TOP_K_VALUES; esta constante apenas registra a conclusão.
#
# Decisão: top_k = 3. Na medição, os três cortes (2, 3 e 4) atingem 100% de
# acerto de recuperação neste corpus — todos são escolhas válidas. top_k = 3
# é adotado como margem protetiva: com apenas 2 resultados, se os dois chunks
# recuperados vierem próximos em similaridade ou trouxerem informações
# concorrentes, um terceiro chunk dá à etapa de geração um voto de desempate.
# top_k = 4 não traz ganho de acerto medido e, sem filtro de status, tende a
# puxar mais chunks revogados para o contexto (ver a contaminação de versão
# no relatório).
RECOMMENDED_TOP_K = 3

# Consultas de teste focadas na única família do corpus com versionamento
# real (vigente + revogado): 'pol-reembolso'. Ver docstring do módulo.
TEST_QUERIES: List[Dict[str, str]] = [
    {
        "query": "Quantos dias tenho para contestar um valor cobrado na minha fatura?",
        "relevant_family": "pol-reembolso",
    },
    {
        "query": "Como funciona o reembolso se a minha contestação de fatura for aceita?",
        "relevant_family": "pol-reembolso",
    },
    {
        "query": "A partir de qual valor a contestação de fatura passa por verificação antifraude?",
        "relevant_family": "pol-reembolso",
    },
    {
        "query": "Em quantos dias úteis a equipe de faturamento analisa uma contestação?",
        "relevant_family": "pol-reembolso",
    },
    {
        "query": "Posso receber o reembolso da minha fatura direto na minha conta corrente?",
        "relevant_family": "pol-reembolso",
    },
]


class BenchmarkError(Exception):
    """Exceção base para falhas previstas na execução deste benchmark."""


@dataclass
class QueryResult:
    """Resultado da avaliação de uma única consulta de teste."""

    query: str
    relevant_family: str
    ranked_chunk_ids: List[str] = field(default_factory=list)
    # Posição (1-based) do primeiro chunk vigente da família relevante no
    # ranking completo. None se nenhum chunk vigente dessa família existir.
    first_relevant_vigente_rank: Optional[int] = None
    # Métrica primária: para cada top_k, o chunk vigente relevante entrou no corte?
    hit_by_top_k: Dict[int, bool] = field(default_factory=dict)
    # Diagnóstico: chunks revogados que apareceram em cada top_k (busca sem filtro).
    revoked_chunk_ids_seen_by_top_k: Dict[int, List[str]] = field(default_factory=dict)


@dataclass
class BenchmarkReport:
    """Relatório consolidado de uma execução completa do benchmark."""

    total_queries: int
    # Métrica primária: fração de consultas com o chunk vigente relevante no top_k.
    retrieval_hit_rate_by_top_k: Dict[int, float]
    # Diagnóstico: fração de consultas com pelo menos um chunk revogado no top_k.
    version_contamination_rate_by_top_k: Dict[int, float]
    query_results: List[QueryResult]
    corpus_size: int
    elapsed_seconds: float


def parse_args() -> argparse.Namespace:
    """Define e interpreta os argumentos de linha de comando do benchmark."""
    parser = argparse.ArgumentParser(
        description=(
            "Mede a precisão de recuperação (o chunk vigente relevante aparece "
            "no top_k) da busca por similaridade de embeddings do Concierge "
            "ConectaTel, para top_k em {2, 3, 4}."
        )
    )
    parser.add_argument(
        "--embedded-chunks-path",
        type=str,
        default=str(_DEFAULT_EMBEDDED_PATH),
        help=(
            "Caminho local para o arquivo JSONL de chunks já embedados (saída "
            "do embedder.py, com o campo 'embedding' em cada linha). "
            "Padrão: data/embeddings/chunks_hierarchical_semantic_embedded.jsonl."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(_DEFAULT_RESULTS_DIR),
        help="Diretório onde o relatório JSON será salvo (padrão: benchmarks/results, ao lado deste script).",
    )
    parser.add_argument(
        "--aws-region",
        type=str,
        default="us-east-1",
        help="Região AWS usada para invocar o Bedrock (padrão: us-east-1).",
    )
    return parser.parse_args()


def load_embedded_chunks(path: Path) -> List[Dict[str, Any]]:
    """
    Lê o arquivo JSONL local de chunks já embedados.

    Args:
        path: caminho local do arquivo gerado pelo embedder.py.

    Returns:
        Lista de dicionários, um por chunk, cada um contendo pelo menos
        'chunk_id', 'metadata' e 'embedding'.

    Raises:
        BenchmarkError: se o arquivo não existir, não puder ser lido, ou
            se nenhum chunk válido (com embedding) for encontrado.
    """
    if not path.exists():
        raise BenchmarkError(
            f"Arquivo de chunks embedados não encontrado: '{path}'. "
            f"Execute o embedder.py antes de rodar este benchmark, ou "
            f"informe o caminho correto via --embedded-chunks-path."
        )

    logger.info(
        "Carregando chunks embedados | caminho=%s | motivo=início da leitura do corpus de teste",
        path,
    )

    chunks: List[Dict[str, Any]] = []
    malformed_lines = 0
    missing_embedding_lines = 0

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkError(f"Não foi possível ler o arquivo '{path}': {exc}") from exc

    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            malformed_lines += 1
            logger.warning(
                "Linha malformada ignorada | linha=%d | motivo=%s",
                line_number,
                str(exc),
            )
            continue

        if not isinstance(record, dict) or "embedding" not in record or not record.get("embedding"):
            missing_embedding_lines += 1
            logger.warning(
                "Registro sem campo 'embedding' válido ignorado | linha=%d | "
                "motivo=chunk provavelmente não passou pelo embedder.py",
                line_number,
            )
            continue

        chunks.append(record)

    if not chunks:
        raise BenchmarkError(
            f"Nenhum chunk com embedding válido encontrado em '{path}'. "
            f"Verifique se o arquivo é realmente a saída do embedder.py."
        )

    logger.info(
        "Chunks embedados carregados | total_válidos=%d | linhas_malformadas=%d | "
        "linhas_sem_embedding=%d | motivo=fim da leitura do corpus de teste",
        len(chunks),
        malformed_lines,
        missing_embedding_lines,
    )

    return chunks


def embed_query(bedrock_client: Any, query_text: str) -> List[float]:
    """
    Gera o embedding de uma única pergunta de teste via Bedrock.

    Usamos input_type='search_query' (em vez de 'search_document', usado
    para o corpus) porque o Cohere Embed v4 otimiza a representação
    vetorial de forma diferente para perguntas de busca versus documentos
    indexados — essa distinção normalmente melhora a qualidade do
    ranqueamento por similaridade.

    Args:
        bedrock_client: cliente boto3 já configurado para 'bedrock-runtime'.
        query_text: texto da pergunta a ser vetorizada.

    Returns:
        Vetor de embedding da pergunta.

    Raises:
        BenchmarkError: se a chamada ao Bedrock falhar ou a resposta vier
            em formato inesperado.
    """
    request_body = {
        "texts": [query_text],
        "input_type": "search_query",
        "embedding_types": ["float"],
    }

    try:
        response = bedrock_client.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )
    except (ClientError, BotoCoreError) as exc:
        raise BenchmarkError(
            f"Falha ao chamar o Bedrock para gerar embedding da consulta "
            f"'{query_text}': {exc}"
        ) from exc

    try:
        response_body = json.loads((response.get("body") or response["Body"]).read())
        return response_body["embeddings"]["float"][0]
    except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
        raise BenchmarkError(
            f"Resposta do Bedrock em formato inesperado ao vetorizar a "
            f"consulta '{query_text}': {exc}"
        ) from exc


def cosine_similarity(vector_a: List[float], vector_b: List[float]) -> float:
    """
    Calcula a similaridade de cosseno entre dois vetores, sem depender de
    bibliotecas externas de álgebra linear (mantém este script leve e
    portátil, já que ele roda como uma ferramenta pontual de benchmark).

    Args:
        vector_a: primeiro vetor.
        vector_b: segundo vetor.

    Returns:
        Similaridade de cosseno entre -1.0 e 1.0. Retorna 0.0 se algum
        vetor tiver norma zero (caso degenerado, evita divisão por zero).

    Raises:
        BenchmarkError: se os vetores tiverem dimensões diferentes.
    """
    if len(vector_a) != len(vector_b):
        raise BenchmarkError(
            f"Vetores com dimensões incompatíveis: {len(vector_a)} vs {len(vector_b)}. "
            f"Verifique se todos os chunks foram embedados com o mesmo modelo."
        )

    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = sum(a * a for a in vector_a) ** 0.5
    norm_b = sum(b * b for b in vector_b) ** 0.5

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def rank_chunks_by_similarity(
    query_vector: List[float], chunks: List[Dict[str, Any]]
) -> List[str]:
    """
    Ordena todos os chunks do corpus por similaridade decrescente em
    relação ao vetor da consulta.

    Args:
        query_vector: embedding da pergunta.
        chunks: lista de chunks, cada um com campo 'embedding'.

    Returns:
        Lista de chunk_id, ordenada do mais similar ao menos similar.
    """
    scored = [
        (chunk.get("chunk_id", "<sem_id>"), cosine_similarity(query_vector, chunk["embedding"]))
        for chunk in chunks
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [chunk_id for chunk_id, _score in scored]


def _is_relevant_vigente(chunk: Dict[str, Any], family: str) -> bool:
    """
    True se o chunk pertence à família de documento relevante para a
    consulta E está vigente. É o alvo que a recuperação precisa acertar.
    """
    meta = chunk.get("metadata", {})
    return meta.get("doc_family_id") == family and meta.get("status") == "vigente"


def evaluate_query(
    query_spec: Dict[str, str],
    ranked_chunk_ids: List[str],
    chunk_by_id: Dict[str, Dict[str, Any]],
) -> QueryResult:
    """
    Avalia uma consulta já ranqueada em dois eixos:

    1. Acerto de recuperação (métrica primária): para cada top_k, existe
       um chunk vigente da família relevante entre os top_k primeiros?
    2. Contaminação de versão (diagnóstico): quais chunks revogados
       aparecem em cada top_k? Não conta como falha da métrica primária —
       serve para dimensionar a necessidade de um filtro de vigência na
       camada de busca.

    Args:
        query_spec: dicionário com 'query' e 'relevant_family'.
        ranked_chunk_ids: chunk_id em ordem decrescente de similaridade.
        chunk_by_id: mapa chunk_id -> registro completo do chunk, para
            consultar 'doc_family_id' e 'status' de cada resultado.

    Returns:
        QueryResult preenchido.
    """
    family = query_spec["relevant_family"]
    result = QueryResult(
        query=query_spec["query"],
        relevant_family=family,
        ranked_chunk_ids=ranked_chunk_ids,
    )

    for position, chunk_id in enumerate(ranked_chunk_ids, start=1):
        if _is_relevant_vigente(chunk_by_id.get(chunk_id, {}), family):
            result.first_relevant_vigente_rank = position
            break

    for top_k in TOP_K_VALUES:
        top_k_ids = ranked_chunk_ids[:top_k]

        result.hit_by_top_k[top_k] = any(
            _is_relevant_vigente(chunk_by_id.get(cid, {}), family) for cid in top_k_ids
        )

        result.revoked_chunk_ids_seen_by_top_k[top_k] = [
            cid
            for cid in top_k_ids
            if chunk_by_id.get(cid, {}).get("metadata", {}).get("status") == "revogado"
        ]

    return result


def run_benchmark(embedded_chunks_path: Path, aws_region: str) -> BenchmarkReport:
    """
    Executa o benchmark completo: carrega os chunks, avalia cada consulta
    de teste e consolida os resultados.

    Args:
        embedded_chunks_path: caminho local do JSONL de chunks embedados.
        aws_region: região AWS para o cliente Bedrock.

    Returns:
        BenchmarkReport com a taxa de acerto e a contaminação por top_k.

    Raises:
        BenchmarkError: se qualquer etapa falhar de forma não recuperável.
    """
    start_time = time.monotonic()

    chunks = load_embedded_chunks(embedded_chunks_path)
    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks if "chunk_id" in chunk}

    logger.info(
        "Inicializando cliente Bedrock | região=%s | motivo=vetorização das consultas de teste",
        aws_region,
    )
    bedrock_client = boto3.client("bedrock-runtime", region_name=aws_region)

    query_results: List[QueryResult] = []

    for query_index, query_spec in enumerate(TEST_QUERIES, start=1):
        logger.info(
            "Avaliando consulta %d/%d | texto='%s' | motivo=início da avaliação",
            query_index,
            len(TEST_QUERIES),
            query_spec["query"],
        )

        query_vector = embed_query(bedrock_client, query_spec["query"])
        ranked_ids = rank_chunks_by_similarity(query_vector, chunks)
        result = evaluate_query(query_spec, ranked_ids, chunk_by_id)
        query_results.append(result)

        for top_k in TOP_K_VALUES:
            veredito = "ACERTO" if result.hit_by_top_k[top_k] else "ERRO"
            logger.info(
                "Resultado da consulta | top_k=%d | recuperou_vigente_relevante=%s | "
                "revogados_no_top_k=%s | motivo=avaliação concluída para este top_k",
                top_k,
                veredito,
                result.revoked_chunk_ids_seen_by_top_k[top_k] or "nenhum",
            )
        logger.info(
            "Posição do primeiro chunk vigente relevante | consulta=%d | rank=%s | "
            "motivo=diagnóstico de ranqueamento",
            query_index,
            result.first_relevant_vigente_rank,
        )

    total = len(query_results)
    retrieval_hit_rate_by_top_k = {
        top_k: sum(1 for r in query_results if r.hit_by_top_k[top_k]) / total
        for top_k in TOP_K_VALUES
    }
    version_contamination_rate_by_top_k = {
        top_k: sum(1 for r in query_results if r.revoked_chunk_ids_seen_by_top_k[top_k]) / total
        for top_k in TOP_K_VALUES
    }

    elapsed = time.monotonic() - start_time

    report = BenchmarkReport(
        total_queries=total,
        retrieval_hit_rate_by_top_k=retrieval_hit_rate_by_top_k,
        version_contamination_rate_by_top_k=version_contamination_rate_by_top_k,
        query_results=query_results,
        corpus_size=len(chunks),
        elapsed_seconds=elapsed,
    )

    logger.info(
        "Benchmark concluído | total_consultas=%d | tamanho_corpus=%d | "
        "tempo_segundos=%.2f | motivo=fim da execução",
        report.total_queries,
        report.corpus_size,
        report.elapsed_seconds,
    )
    for top_k in TOP_K_VALUES:
        logger.info(
            "Acerto de recuperação | top_k=%d | taxa=%.1f%% | motivo=métrica primária",
            top_k,
            retrieval_hit_rate_by_top_k[top_k] * 100,
        )
    for top_k in TOP_K_VALUES:
        logger.info(
            "Contaminação de versão (busca sem filtro de status) | top_k=%d | taxa=%.1f%% | "
            "motivo=diagnóstico que motiva o filtro de vigência",
            top_k,
            version_contamination_rate_by_top_k[top_k] * 100,
        )

    return report


def report_to_serializable_dict(report: BenchmarkReport) -> Dict[str, Any]:
    """Converte o relatório em um dicionário puro, pronto para json.dumps."""
    return {
        "total_queries": report.total_queries,
        "corpus_size": report.corpus_size,
        "elapsed_seconds": round(report.elapsed_seconds, 3),
        "retrieval_hit_rate_by_top_k": {
            str(top_k): round(rate, 4)
            for top_k, rate in report.retrieval_hit_rate_by_top_k.items()
        },
        "version_contamination_rate_by_top_k": {
            str(top_k): round(rate, 4)
            for top_k, rate in report.version_contamination_rate_by_top_k.items()
        },
        "recommended_top_k": RECOMMENDED_TOP_K,
        "recommended_top_k_rationale": (
            "Os três cortes testados (2, 3, 4) atingem 100% de acerto de "
            "recuperação neste corpus — todos são válidos. top_k=3 é adotado "
            "como margem protetiva: se os 2 primeiros chunks vierem próximos "
            "em similaridade ou com informações concorrentes, o terceiro dá à "
            "etapa de geração um voto de desempate. top_k=4 não melhora o "
            "acerto medido e tende a puxar mais chunks revogados para o "
            "contexto quando a busca não filtra por status."
        ),
        "notes": (
            "retrieval_hit_rate = fração de consultas em que um chunk com "
            "status 'vigente' e doc_family_id da consulta aparece no top_k. "
            "version_contamination_rate = fração de consultas em que um chunk "
            "'revogado' aparece no top_k quando a busca NÃO filtra por status; "
            "não conta contra a precisão — dimensiona a necessidade do filtro "
            "de vigência na camada de recuperação, antes do ranqueamento."
        ),
        "query_results": [
            {
                "query": r.query,
                "relevant_family": r.relevant_family,
                "first_relevant_vigente_rank": r.first_relevant_vigente_rank,
                "top_5_ranked_chunk_ids": r.ranked_chunk_ids[:5],
                "hit_by_top_k": {str(k): v for k, v in r.hit_by_top_k.items()},
                "revoked_chunk_ids_seen_by_top_k": {
                    str(k): v for k, v in r.revoked_chunk_ids_seen_by_top_k.items()
                },
            }
            for r in report.query_results
        ],
    }


def save_report(report: BenchmarkReport, output_dir: Path) -> Path:
    """
    Salva o relatório em JSON no diretório de saída informado.

    Args:
        report: relatório consolidado da execução.
        output_dir: diretório local de destino (criado se não existir).

    Returns:
        Caminho completo do arquivo gravado.

    Raises:
        BenchmarkError: se o diretório não puder ser criado ou o arquivo
            não puder ser escrito.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BenchmarkError(
            f"Não foi possível criar o diretório de saída '{output_dir}': {exc}"
        ) from exc

    output_path = output_dir / "version_accuracy_result.json"

    try:
        output_path.write_text(
            json.dumps(report_to_serializable_dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise BenchmarkError(
            f"Não foi possível escrever o relatório em '{output_path}': {exc}"
        ) from exc

    logger.info(
        "Relatório salvo | caminho=%s | motivo=persistência do resultado do benchmark",
        output_path,
    )

    return output_path


def main() -> int:
    """Ponto de entrada de linha de comando deste benchmark."""
    args = parse_args()

    try:
        report = run_benchmark(
            embedded_chunks_path=Path(args.embedded_chunks_path),
            aws_region=args.aws_region,
        )
        save_report(report, Path(args.output_dir))
    except BenchmarkError as exc:
        logger.error("Benchmark interrompido | motivo=%s", str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - barreira final intencional
        logger.exception("Erro inesperado no benchmark | motivo=%s", str(exc))
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
