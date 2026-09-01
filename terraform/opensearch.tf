locals {
  opensearch_collection_name = var.opensearch_collection_name != "" ? var.opensearch_collection_name : "${var.project_prefix}-vectors"
}

# Encryption policy must exist BEFORE the collection can finish creating
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

# Network policy
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

data "aws_caller_identity" "current" {}

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
      # Inclui a Role da Lambda, os Principais Extras e o Caller Identity Local (AWS CLI/User)
      Principal = distinct(concat(
        [
          aws_iam_role.lambda_exec.arn,
          data.aws_caller_identity.current.arn
        ],
        var.opensearch_extra_principals
      ))
    }
  ])
}
