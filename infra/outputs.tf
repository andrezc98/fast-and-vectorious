output "data_bucket" { value = aws_s3_bucket.data.id }
output "vector_bucket" { value = aws_s3vectors_vector_bucket.lab.vector_bucket_name }
output "aoss_endpoint" { value = aws_opensearchserverless_collection.lab.collection_endpoint }
output "aurora_host" { value = aws_rds_cluster.lab.endpoint }
output "aurora_secret_arn" { value = aws_rds_cluster.lab.master_user_secret[0].secret_arn }
output "docs_table" { value = aws_dynamodb_table.docs.name }
output "sagemaker_role_arn" { value = aws_iam_role.sagemaker.arn }
output "runner_instance_id" { value = one(aws_instance.runner[*].id) }
