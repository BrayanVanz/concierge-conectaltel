locals {
  opensearch_collection_name = var.opensearch_collection_name != "" ? var.opensearch_collection_name : "${var.project_prefix}-vectors"
}

# Encryption policy must exist BEFORE the collection can finish creating,
# otherwise the collection gets stuck forever in "CREATING" state.
resource "aws_opensearchserverless_security_policy" "encryption" {
  count = var.enable_opensearch_vector_search ? 1 : 0

  name = "${substr(var.project_prefix, 0, 20)}-vec-enc"
  type = "encryption"

  policy = jsonencode({
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${local.opensearch_collection_name}"]
      }
    ]
    AWSOwnedKey = true
  })
}

# Network policy also must be based on the collection NAME (not id), since
# it must exist before/while the collection is being created.
resource "aws_opensearchserverless_security_policy" "network" {
  count = var.enable_opensearch_vector_search ? 1 : 0

  name = "${substr(var.project_prefix, 0, 20)}-vec-net"
  type = "network"

  policy = jsonencode([
    {
      Rules = [
        {
          ResourceType = "collection"
          Resource     = ["collection/${local.opensearch_collection_name}"]
        }
      ]
      AllowFromPublic = true
    }
  ])
}

resource "aws_opensearchserverless_collection" "vector" {
  count = var.enable_opensearch_vector_search ? 1 : 0

  name = local.opensearch_collection_name
  type = "VECTORSEARCH"

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
  ]
}

resource "aws_opensearchserverless_access_policy" "data" {
  count = var.enable_opensearch_vector_search ? 1 : 0

  name = "${substr(var.project_prefix, 0, 21)}-vec-data"
  type = "data"

  policy = jsonencode([
    {
      Rules = [
        {
          ResourceType = "collection"
          Resource     = ["collection/${local.opensearch_collection_name}"]
          Permission = [
            "aoss:CreateCollectionItems",
            "aoss:DeleteCollectionItems",
            "aoss:UpdateCollectionItems",
            "aoss:DescribeCollectionItems"
          ]
        },
        {
          ResourceType = "index"
          Resource     = ["index/${local.opensearch_collection_name}/*"]
          Permission = [
            "aoss:CreateIndex",
            "aoss:DeleteIndex",
            "aoss:DescribeIndex",
            "aoss:UpdateIndex",
            "aoss:ReadDocument",
            "aoss:WriteDocument"
          ]
        }
      ]
      Principal = concat(
        [aws_iam_role.lambda_exec.arn],
        var.opensearch_extra_principals
      )
    }
  ])
}
