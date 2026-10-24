# Precios us-east-1

Leídos desde la Price List API (`pricing:GetProducts`, us-east-1, on-demand) el 8 de octubre de 2026.

| item | usd | source |
|---|---|---|
| ddb_vector_write_gb | 0.52 | AmazonDynamoDB VectorWriteRequest ($0.52 per GB) |
| ddb_vector_search_gb | 0.002 | AmazonDynamoDB VectorSearch ($0.002 per GB searched) |
| ddb_storage_gb_month | 0.25 | AmazonDynamoDB TimedStorage-ByteHrs ($0.25/GB-mo beyond the first 25 free GB-mo) |
| s3v_query_request | 0.0000025 | AmazonS3 Vectors-Request-Tier3 ($0.0000025 per QueryVectors request) |
| s3v_processed_tb_first_100k | 0.0039997 | AmazonS3 Vectors-Query-ProcessedBytes-Tier1 ($0.000003906 per GB x 1024) |
| s3v_processed_tb_100k_10m | 0.002 | aws.amazon.com/s3/pricing, read 2026-10-14 ($0.002/TB for vectors 100K–10M of an index) |
| s3v_processed_tb_over_10m | 0.0004 | aws.amazon.com/s3/pricing, read 2026-10-14 ($0.0004/TB for vectors beyond 10M of an index) |
| s3v_put_gb | 0.20 | aws.amazon.com/s3/pricing, read 2026-10-14 ($0.20 per GB uploaded) |
| s3v_returned_gb | 0.01 | AmazonS3 Vectors-Query-Returned-Bytes ($0.01 per GB) |
| s3v_storage_gb_month | 0.06 | AmazonS3 Vectors-TimedStorage-ByteHrs ($0.06 per GB-mo) |
| aoss_ocu_hour | 0.24 | AmazonES USE1-SearchOCU / IndexingOCU / VectorIndexAccelerationOCU ($0.24 per OCU-hour each) |
| aoss_storage_gb_month | 0.024 | AmazonES USE1-StorageUsedInS3ByteHour ($0.024 per GB-mo) |
| aurora_acu_hour | 0.12 | AmazonRDS Aurora:ServerlessV2Usage, Aurora PostgreSQL ($0.12 per ACU-hour, standard storage) |
| aurora_storage_gb_month | 0.10 | AmazonRDS Aurora:StorageUsage ($0.10 per GB-mo) |
| cohere_v4_1m_tokens | 0.12 | AmazonBedrockFoundationModels Cohere Embed 4, USE1_InputTokenCount ($0.12 per 1M input tokens; no separate cross-region SKU) |
| nova2_1m_tokens | 0.135 | AmazonBedrock USE1-NovaMultiModalEmbeddings-input-tokens ($0.000135 per 1K tokens) |
| sagemaker_g6e_xlarge_hour | 2.6054 | AmazonSageMaker USE1-Host:ml.g6e.xlarge ($2.6054 per hour) |
| sagemaker_g6_2xlarge_hour | 1.222 | AmazonSageMaker USE1-Host:ml.g6.2xlarge ($1.222 per hour; instance used for Qwen) |
