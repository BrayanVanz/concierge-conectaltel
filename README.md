# Concierge ConectaTel

Este repositório já gera embeddings com Amazon Bedrock e salva os artefatos em S3.

Para persistir esses vetores em um índice AWS de busca semântica, o serviço recomendado é Amazon OpenSearch Serverless (vector search). O módulo de embeddings agora inclui um adapter para indexar arquivos `*_embedded.jsonl` nesse serviço.

Fluxo recomendado:

1. Gerar os embeddings com `python src/embeddings/main.py`
2. Criar/criar o endpoint do Amazon OpenSearch Serverless
3. Configurar `OPENSEARCH_ENDPOINT` e `OPENSEARCH_INDEX_NAME`
4. Rodar:

   python src/embeddings/index_to_opensearch.py --input data/embeddings --endpoint "$OPENSEARCH_ENDPOINT" --region us-east-1 --index-name "$OPENSEARCH_INDEX_NAME"

Observação: o bucket S3 continua sendo o "artefato de saída" do pipeline, enquanto o OpenSearch Serverless passa a ser o "índice vetorial consultável".
