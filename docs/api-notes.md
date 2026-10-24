# API notes: vector stores, embeddings, datasets (us-east-1)

Research only. No AWS account was touched. Every fact below was read on **2026-10-08** unless a line says otherwise. Prices come from the public AWS Price List bulk files (`pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<offer>/current/us-east-1/index.json`, anonymous, no account), and the publication date of each file is noted.

---

## 1. DynamoDB vector search (GA 2026-08-05)

- **Launch:** GA on 2026-08-05 in all commercial Regions and GovCloud. Claims single-digit ms latency and 99%+ recall. https://aws.amazon.com/about-aws/whats-new/2026/08/amazon-dynamodb-vector-search/
- **Defining the index:** `CreateTable` takes `VectorIndexes=[...]`. `UpdateTable` takes `VectorIndexUpdates=[{"Create": {...}} | {"Delete": {"IndexName": ...}}]`. https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/VectorSearchWorkingWith.html
  - `VectorIndex` fields (required unless noted): `IndexName`, `VectorAttribute={"AttributeName": ...}`, `Dimensions` (int), `DistanceFunction` (`COSINE | EUCLIDEAN | DOT_PRODUCT`), `Projection` (`ALL | KEYS_ONLY | INCLUDE`), and optional `SearchSchema=[{"AttributeName", "SearchSchemaElementType": "HASH" | "INLINE_FILTER"}]`. Every attribute named in `SearchSchema` must also appear in `AttributeDefinitions`. Source: the botocore model `dynamodb/2012-08-10/service-2.json` (develop), https://github.com/boto/botocore, plus the CFN reference https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-dynamodb-table-vectorindex.html
  - The table **must be on-demand (`PAY_PER_REQUEST`)**. https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/VectorSearch.Requirements.html
  - Only one vector index or GSI can be created or deleted at a time per table. `CreateTable` can define up to 5 indexes at once. A new index sits in `CREATING` with `Backfilling=true`. After it reports `ACTIVE`, `SearchVectors` can still return `ValidationException` ("The table does not have the specified index") for a short time, so **retry the first searches**. (VectorSearchWorkingWith page above)
- **Vector attribute type:** a list `L` of numbers `N`. The index stores values as float32, so extra precision is silently dropped. A wrong dimension count rejects the write. If the item lacks the HASH key attribute, the write succeeds on the table but the item is **silently left out of the index**. (VectorSearchWorkingWith page above)
- **Quotas:** 4,096 max dimensions (not adjustable). Max `TopK` is **100** (not adjustable). 5 vector indexes per table (adjustable). 1 HASH key per index. 18 inline filters per index. Search rate is 1 GBps per partition-key value. Write rate is 10 MBps per partition-key value. Index creation without allowlisting is capped at a 600 GB base table. https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ServiceQuotas.html
- **Query:** boto3 `dynamodb` client, method **`search_vectors`** (API `SearchVectors`). https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/VectorSearchWorkingWith.html
  - Request: `TableName`, `IndexName`, `SearchVector` (required; a plain list `[{"N": "0.12"}, ...]` with no `L` wrapper), `TopK` (required, min 1, max 100), `SearchConditionExpression`, `ExpressionAttributeNames`, `ExpressionAttributeValues`, `ProjectionExpression`, `ReturnConsumedCapacity`. Source: botocore model.
  - Response: `{"SearchResults": [{"Item": {...}, "Score": float}], "ConsumedCapacity": {... VectorSearchRequestBytes}}`. The vector attribute is left out of results by default. Responses are capped at 16 MB, with **no pagination**.
  - Score meaning: `COSINE` runs 0..2 and `EUCLIDEAN` is a distance, so lower is better for both. For `DOT_PRODUCT`, higher is better. Source: botocore model docstring.
  - Filters: `SearchConditionExpression` supports **only `=`**. `<>`, `<`, `>`, and `IN` are "not yet available". The HASH key is mandatory in the expression when the index defines one. Inline filters are optional.
  - Searches go to a separate endpoint (`<account>.search-ddb.<region>.amazonaws.com` or `search-dynamodb.<region>.api.aws`). The SDK routes there automatically.
  - Fine-grained access control (`dynamodb:LeadingKeys`) does **not** apply. PartiQL, Query, and Scan cannot read a vector index. https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/VectorSearch.Requirements.html
- **Consistency:** search is **eventually consistent**, and there is no `ConsistentRead` parameter (absent from the model). (VectorSearchWorkingWith page above)
- **Pricing dimensions:** vector write bytes, vector search bytes processed, and index storage at the normal table storage rate. Each request has a 1 KB minimum and is billed per byte above that. https://aws.amazon.com/blogs/database/build-semantic-search-with-native-vector-support-in-amazon-dynamodb/ and https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/VectorSearchMonitoring.html
  - us-east-1 Price List (AmazonDynamoDB, published 2026-09-11):
    - `VectorWriteRequest` **$0.52/GB**
    - `VectorSearch` **$0.002/GB searched**
    - Standard-IA rates: $0.65/GB write, $0.0025/GB search
- **SDK minimum:** **boto3/botocore 1.43.64** (botocore uploaded to PyPI 2026-08-04). Changelog entry: "``dynamodb``: Vector indexes are a type of index...". https://github.com/boto/botocore/blob/develop/CHANGELOG.rst , https://github.com/boto/boto3/blob/develop/CHANGELOG.rst , https://pypi.org/project/botocore/1.43.64/
- **Terraform:** **not supported.** `aws_dynamodb_table` has no vector index argument as of provider **6.68.0 (2026-10-07)**. Open request: hashicorp/terraform-provider-aws#49350, "Amazon DynamoDB real-time vector search", open and updated 2026-10-02. Workaround: create the index with boto3 or the CLI after `terraform apply`, or use CloudFormation, which has `AWS::DynamoDB::Table VectorIndex`. https://github.com/hashicorp/terraform-provider-aws/blob/main/CHANGELOG.md , https://github.com/hashicorp/terraform-provider-aws/issues/49350

## 2. Amazon S3 Vectors

- **boto3 client:** `boto3.client("s3vectors")`. Added in botocore **1.39.5** (2025-07-15). Paginated QueryVectors up to 10,000 results arrived in **1.43.31** (2026-06-16). Metadata prefiltering arrived in **1.43.106** (2026-09-30). Sources: botocore/boto3 CHANGELOG and PyPI (links in §1).
- **CreateVectorBucket:** `vectorBucketName`, `encryptionConfiguration`, `tags`. Source: botocore model `s3vectors/2025-07-15`.
- **CreateIndex:** `vectorBucketName` or `vectorBucketArn`, `indexName` (3–63 chars), `dataType="float32"` (the only value), `dimension` **1..4096**, `distanceMetric` **`cosine | euclidean`** (no dot product), `metadataConfiguration={"nonFilterableMetadataKeys": [...]}` (max 10 keys, **immutable**), `encryptionConfiguration`, `tags`. https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_CreateIndex.html
  - Index mode `CLASSIC | ENHANCED` is a separate setting (`UpdateIndexMode`, `PutVectorBucketDefaultIndexMode` in the model). `ENHANCED` applies filters **before** the ANN search (prefiltering, better filtered recall). `QueryVectors.queryMode` can request `ENHANCED` on a CLASSIC index. https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-metadata-filtering.html
- **PutVectors:** up to **500 vectors per call** and a **20 MiB** payload. Shape: `vectors=[{"key": str, "data": {"float32": [...]}, "metadata": {...}}]`. Cast values to float32 (numpy.float32), because higher precision is converted. Combined Put and Delete calls are capped at 1,000 req/s per index, and inserted plus deleted vectors at 2,500 vectors/s per index. https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-limitations.html , https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_PutVectors.html
- **QueryVectors:** `topK` (required, min 1; the limits page says **up to 10,000**, at **100 results per page** with `nextToken`), `queryVector={"float32": [...]}`, `filter` (JSON), `returnMetadata` (default false), `returnDistance` (default false), `queryMode`. The response is `{"vectors": [{"key", "distance", "metadata"}], "distanceMetric", "nextToken"}`. A filter or `returnMetadata=True` also needs `s3vectors:GetVectors` permission, otherwise you get a 403. https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_QueryVectors.html
  - Filter syntax is Mongo-like:
    - Comparison: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`
    - Set membership: `$in`, `$nin`
    - Other: `$exists`, `$and`, `$or`
    - `$startsWith`: ENHANCED only
    - Example: `{"genre": {"$eq": "x"}}`
    - Source: the metadata filtering page above.
  - On an ENHANCED index, a query accepts up to 100 filter constraints. (limits page)
- **Metadata limits:** 40 KB total per vector, **2 KB filterable** per vector, at most 50 metadata keys per vector. Types: string, number, boolean, list. Other limits: 2 billion vectors per index, 10,000 indexes per bucket, 10,000 buckets per Region. (limits page)
- **Pricing (us-east-1, AmazonS3 Price List published 2026-09-28):**
  - Storage $0.06/GB-month
  - PUT $0.20/GB, with a 128 KB minimum per PUT (pricing page)
  - Query requests $2.50 per million ($0.0000025 each)
  - Data processed by index size, per the pricing page:
    - first 100K vectors: $0.004/TB
    - 100K–10M vectors: $0.002/TB
    - over 10M vectors: $0.0004/TB
  - Data returned: $0.01/GB
  - https://aws.amazon.com/s3/pricing/
- **Terraform:** `aws_s3vectors_vector_bucket`, `aws_s3vectors_index`, and `aws_s3vectors_vector_bucket_policy` arrived in provider **6.24.0** (2025-12-02). `encryption_configuration` and `metadata_configuration.non_filterable_metadata_keys` on the index arrived in **6.26.0** (2025-12-10). Index args: `vector_bucket_name`, `index_name`, `data_type`, `dimension`, `distance_metric`. **All of them force replacement.** Index mode is not exposed in Terraform docs. https://github.com/hashicorp/terraform-provider-aws/blob/main/website/docs/r/s3vectors_index.html.markdown , CHANGELOG link in §1

## 3. OpenSearch Serverless NextGen (GA 2026-05-28)

- **Launch:** NextGen launched with search and vector collection types (no time series). It decouples compute from storage, scales to zero, and uses resource-based endpoints. CloudFormation was "coming soon" per the launch blog. https://aws.amazon.com/about-aws/whats-new/2026/05/amazon-opensearch-serverless-next-generation-generally-available/ , https://aws.amazon.com/blogs/big-data/the-next-generation-of-amazon-opensearch-serverless-built-from-the-ground-up-for-agents/
- **Create path (API):**
  1. `CreateCollectionGroup(name, generation="NEXTGEN", standbyReplicas="ENABLED", capacityLimits={"minIndexingCapacityInOCU": 0, "maxIndexingCapacityInOCU": N, "minSearchCapacityInOCU": 0, "maxSearchCapacityInOCU": N})`. A collection group is required for NextGen, and `standbyReplicas` must be `ENABLED`. https://docs.aws.amazon.com/opensearch-service/latest/ServerlessAPIReference/API_CreateCollectionGroup.html
  2. `CreateCollection(name, type="VECTORSEARCH", collectionGroupName=..., encryptionConfig={"aWSOwnedKey": true} | {"kmsKeyArn": ...}, deletionProtection, standbyReplicas, vectorOptions={"ServerlessVectorAcceleration": "ENABLED|DISABLED|ALLOWED"})`. `type` values are `SEARCH | TIMESERIES | VECTORSEARCH`. https://docs.aws.amazon.com/opensearch-service/latest/ServerlessAPIReference/API_CreateCollection.html
  - Max OCU on a NextGen group can be 0, 2, 4, 8, 16, or multiples of 16 up to 1,696. https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cost-optimization.html
  - boto3 support for NEXTGEN groups starts at **1.43.17** (2026-05-28): "ability to create NEXTGEN collection groups". Source: botocore CHANGELOG.
- **Policies:** the launch blog CLI walkthrough creates an **encryption** security policy, a **network** security policy, and a **data access** policy before the collection. It notes "These must exist before the collection can be created", and that policies take 30–60 s to propagate (expect 403s until then). Console Express Create makes all three automatically. `CreateCollection` now also takes an inline `encryptionConfig`. Launch blog above, plus https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-create.html
- **VPC access:** NextGen endpoints live on `on.aws`, either per collection or per account. Private access is a **standard PrivateLink interface endpoint created with EC2 `CreateVpcEndpoint`**, not the AOSS-managed `CreateVpcEndpoint` (which is Classic only). https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vpc.html , https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-collection-endpoints.html
- **Scale to zero and billing:**
  - When every collection in the group has been idle **10 minutes**, indexing and search OCUs drop to 0 and billing stops.
  - Cold start takes about **10 s**. Requests are queued, not dropped.
  - Indexing and search scale independently.
  - Billing is in **OCU-hours at the collection-group level, with per-second granularity**. Storage is billed per GB-month. GPU index build appears as a separate line item.
  - Sources: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-scale-to-zero.html , https://aws.amazon.com/opensearch-service/pricing/ , launch blog.
  - us-east-1 Price List (AmazonES, published 2026-09-27): IndexingOCU, SearchOCU, and VectorIndexAccelerationOCU are each **$0.24/OCU-hour**. `StorageUsedInS3ByteHour` is $0.024/GB-month. (These SKUs are not labeled NextGen vs Classic, see Open risks.)
- **knn_vector mapping (NextGen):** `settings.index.knn=true`. The field is `{"type": "knn_vector", "dimension": N, "space_type": "l2" | "cosinesimil" | "innerproduct"}`.
  - NextGen does **not need `engine` or `mode`**. The engine field is "only configurable with Classic". The Migration Assistant maps NextGen vector fields to Faiss HNSW.
  - Default compression is **32x**; it can be overridden per field with `compression_level` set to `1x`, `2x`, `8x`, `16x`, or `32x`.
  - Radial search is not supported with 32x.
  - Read-after-write latency (`refresh_interval`) is **10 s** on NextGen versus 60 s on Classic.
  - Vectors are excluded from `_source` by default; pass `"_source": true` to get them.
  - Max dimension: "can accommodate 16,000 dimensions".
  - Classic vector collections do not support Lucene and support only Faiss/NMSLIB HNSW, never IVF.
  - Sources: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vector-search.html , https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/tdvk-serverless.html
- **Filtered k-NN:** `{"size": k, "query": {"knn": {"<field>": {"vector": [...], "k": k, "filter": {"bool": {"must": [...]}}}}}}`. (serverless-vector-search page)
- **Custom doc IDs:** NextGen supports them (`PUT index/_doc/<id>`). Classic vector collections reject a custom `id`. (serverless-vector-search page, plus https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-clients.html)
- **Python client:** `opensearch-py` (latest **3.2.0**, released 2026-04-27 on PyPI) with `AWSV4SignerAuth(credentials, region, "aoss")` and `RequestsHttpConnection`. The SigV4 service name is **`aoss`**, not `es`. https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-clients.html , https://pypi.org/project/opensearch-py/
- **Terraform:**
  - `aws_opensearchserverless_collection_group` arrived in **6.40.0** (2026-04-08). Its `generation` argument (`CLASSIC|NEXTGEN`, forces new) arrived in **6.49.0** (2026-06-04). Arguments: `name`, `standby_replicas` (must be `ENABLED` for NEXTGEN), `capacity_limits { min_/max_indexing_capacity_in_ocu, min_/max_search_capacity_in_ocu }`.
  - `aws_opensearchserverless_collection` has `type`, `collection_group_name` (forces new), `encryption_config { aws_owned_key | kms_key_arn }`, `standby_replicas`, and `vector_options.serverless_vector_acceleration` (the last one added in 6.51.0).
  - Policies: `aws_opensearchserverless_security_policy` (type encryption/network) and `aws_opensearchserverless_access_policy`.
  - **Pin `hashicorp/aws >= 6.51`.**
  - Sources: https://github.com/hashicorp/terraform-provider-aws/blob/main/website/docs/r/opensearchserverless_collection.html.markdown , .../opensearchserverless_collection_group.html.markdown , CHANGELOG

## 4. Aurora PostgreSQL Serverless v2 + pgvector

- **Latest pgvector on Aurora:** **0.8.2**, on Aurora PostgreSQL **18.4, 18.6, 17.10, 17.11, 16.14, 16.15** (and per the page also 15.18/15.19 and 14.23/14.24). Version 0.8.1 is on 18.3, 17.9, and 16.13. Version 0.8.0 is on 17.4–17.7 and 16.8–16.11. Extensions do not auto-upgrade with the engine, so run `ALTER EXTENSION vector UPDATE TO '0.8.2'`. https://docs.aws.amazon.com/AmazonRDS/latest/AuroraPostgreSQLReleaseNotes/AuroraPostgreSQL.Extensions.html
- **HNSW limits (pgvector README, master at v0.8.7):** `vector` is indexable up to **2,000** dims. `halfvec` up to **4,000**. `bit` up to 64,000. `sparsevec` up to 1,000 non-zero elements. Storage max is 16,000 dims for both vector and halfvec. For more than 2,000 dims, use an expression index on `(embedding::halfvec(N)) halfvec_cosine_ops`. https://github.com/pgvector/pgvector/blob/master/README.md
- **Iterative index scans (since 0.8.0):**
  - `SET hnsw.iterative_scan = strict_order | relaxed_order` (default off)
  - `hnsw.max_scan_tuples` (default 20000)
  - `hnsw.scan_mem_multiplier` (default 1)
  - IVFFlat equivalents: `ivfflat.iterative_scan = relaxed_order` and `ivfflat.max_probes`
  - Source: README above.
- **Scale to zero:**
  - Set `MinCapacity=0`, then `SecondsUntilAutoPause` between **300 and 86,400** (default 300). Requires Aurora PostgreSQL **≥ 16.3, 15.7, 14.12, or 13.15**.
  - Resume takes about **15 s**, or 30 s or more after more than 24 h paused. Set the client `connect_timeout` above 15 s and retry.
  - These prevent pausing: an attached RDS Proxy, logical replication, a global database primary, zero-ETL, any open user connection, and provisioned instances in the cluster.
  - `pg_cron` jobs do not wake the instance.
  - https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html
- **Terraform:** `aws_rds_cluster.serverlessv2_scaling_configuration { min_capacity = 0, max_capacity = N, seconds_until_auto_pause = 300 }`. `seconds_until_auto_pause` arrived in provider **5.81.0** (2024-12-12). A bug when removing it was fixed in 5.96.0. https://github.com/hashicorp/terraform-provider-aws/blob/release/5.x/CHANGELOG.md
- **Data API vs psycopg:**
  - Data API limits: writer instance only, no T classes, a **1 MiB response cap**, **64 KB per row**, and `scram-sha-256` required on PG 14+. A Data API call also wakes a paused writer.
  - Recommendation (my inference, not an AWS statement): use **psycopg (v3) plus the `pgvector` Python adapter over a direct TLS connection** for loading and benchmarking. Bulk COPY, server-side settings like `SET hnsw.ef_search` / `hnsw.iterative_scan` per session, and latency without an HTTP hop all need a real session. Data API requests are independent HTTP calls, so a per-session `SET` is not guaranteed to carry over (unverified). Keep the Data API only for a Lambda-without-VPC demo path.
  - https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.limitations.html , https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.troubleshooting.html

## 5. Embedding models

### Cohere Embed v4 (Bedrock)
- **Model ID:** `cohere.embed-v4:0`. Geo profile `us.cohere.embed-v4:0`, global profile `global.cohere.embed-v4:0`. Available in-Region in us-east-1. https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-embed-v4.html
- **Request (InvokeModel):** `{"input_type": "search_document" | "search_query" | "classification" | "clustering", "texts": [...≤96], "embedding_types": ["float" | "int8" | "uint8" | "binary" | "ubinary"], "output_dimension": 256 | 512 | 1024 | 1536 (default 1536), "max_tokens": ≤128000, "truncate": "NONE" | "LEFT" | "RIGHT"}`. Also takes `images` and interleaved `inputs`. The payload cap is about 20 MB. https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
- **Response:**
  - One embedding type requested: `{"id", "embeddings": [[...]], "response_type": "embeddings_floats", "texts": [...]}`
  - Several types requested: `{"embeddings": {"float": [[...]], "int8": [[...]]}, "response_type": "embeddings_by_type"}`
  - The AWS sample passes `embedding_types=["float"]` and still iterates the response as a dict. Code should handle both shapes.
- **Price (us-east-1):** **$0.12 per 1M input tokens**, from the Price List `AmazonBedrockFoundationModels` (published 2026-10-07), SKU "Cohere Embed 4 Model (Amazon Bedrock Edition)", usage type `USE1-MP:USE1_InputTokenCount-Units`, "Price per 1 million input tokens". The HTML pricing page did not render the figure. A second SKU, `USE1_Cohere_Embed_V4-Units` at $0.00000002 per "Embeddings", has an unclear meaning (see Open risks).

### Amazon Nova 2 Multimodal Embeddings (Bedrock)
- **Model ID:** `amazon.nova-2-multimodal-embeddings-v1:0`. The AWS example uses us-east-1. https://docs.aws.amazon.com/nova/latest/nova2-userguide/embeddings.html
- **Text request:** `{"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": {"embeddingPurpose": "GENERIC_INDEX", "embeddingDimension": 1024, "text": {"truncationMode": "END", "value": "..."}}}`. `schemaVersion` is optional (`"nova-multimodal-embed-v1"`). https://docs.aws.amazon.com/nova/latest/userguide/embeddings-schema.html
- **`embeddingDimension`:** **256 | 384 | 1024 | 3072**. These are MRL dimensions; there is **no 1536 or 2048**.
- **`embeddingPurpose`:** `GENERIC_INDEX`, `GENERIC_RETRIEVAL`, `TEXT_RETRIEVAL`, `IMAGE_RETRIEVAL`, `VIDEO_RETRIEVAL`, `DOCUMENT_RETRIEVAL`, `AUDIO_RETRIEVAL`, `CLASSIFICATION`, `CLUSTERING`. Index documents with `GENERIC_INDEX`. Queries use `GENERIC_RETRIEVAL` or the modality-specific `*_RETRIEVAL`.
- **Context and response:** max context is 8K tokens. The response is read as `body["embeddings"][0]["embedding"]`. https://aws.amazon.com/blogs/machine-learning/manufacturing-intelligence-with-amazon-nova-multimodal-embeddings/
- **Price (us-east-1, AmazonBedrock Price List published 2026-10-06):** text input $0.000135 per 1K tokens, which is **$0.135/1M** on demand and **$0.0675/1M** in batch.

### Qwen3-Embedding-8B (self-hosted)
- **Model card facts:**
  - 8B parameters, Apache-2.0 license.
  - **32K** sequence length. The card's Transformers example uses `max_length=8192`.
  - Output dimension up to **4096**.
  - **MRL: any user-defined dimension from 32 to 4096.**
  - Query prompt: `f"Instruct: {task_description}\nQuery:{query}"`. Note there is **no space after `Query:`**. Documents take **no instruction**.
  - Needs transformers ≥ 4.51.0, sentence-transformers ≥ 2.7.0, vLLM ≥ 0.8.5, TEI ≥ 1.7.2. Card example: `ghcr.io/huggingface/text-embeddings-inference:1.7.2 --model-id Qwen/Qwen3-Embedding-8B --dtype float16`.
  - https://huggingface.co/Qwen/Qwen3-Embedding-8B
- **SageMaker containers (us-east-1)** (`<TEI_REGISTRY>` and `<DLC_REGISTRY>` are the AWS-owned ECR accounts listed for us-east-1 on the region_availability page; they are left out here because sanitize-check flags 12-digit numbers):
  - TEI: `<TEI_REGISTRY>.dkr.ecr.us-east-1.amazonaws.com/tei:2.0.1-tei1.9.4-gpu-py310-cu129-ubuntu24.04`
  - vLLM: `<DLC_REGISTRY>.dkr.ecr.us-east-1.amazonaws.com/vllm:0.30.0-gpu-py312-cu130-ubuntu24.04-sagemaker`, or the tag `vllm:server-sagemaker-cuda-v2.6`
  - https://aws.github.io/deep-learning-containers/reference/available_images/ , https://aws.github.io/deep-learning-containers/reference/region_availability/
- **GPU instances with ≥24 GB:**
  - `ml.g5.xlarge` (A10G, 24 GiB)
  - `ml.g6.xlarge` (L4, 24 GiB)
  - `ml.g6e.xlarge` (L40S, 48 GB)
  - Sources: https://docs.aws.amazon.com/omics/latest/dev/memory-and-compute-tasks.html , https://aws.amazon.com/ec2/instance-types/g6e/ , https://docs.aws.amazon.com/sdk-for-kotlin/api/latest/deadline/aws.sdk.kotlin.services.deadline.model/-accelerator-selection/-builder/name.html
  - The fp16 weights are about 16 GB (8B × 2 bytes; inference), so a 24 GB card leaves limited room for batch/KV. **g6e.xlarge is the safe pick.**
- **Run mode (inference, not documented end-to-end by AWS for this model):** a temporary real-time endpoint with the TEI image and env `HF_MODEL_ID=Qwen/Qwen3-Embedding-8B`. Batch on the client, then delete the endpoint. Batch Transform with TEI should work with JSONL plus `SplitType=Line` against `/invocations`, but I found no official example (see Open risks).

## 6. Common dimension across the four stores

| Store | Max dims for an ANN index |
|---|---|
| DynamoDB vector index | 4,096 |
| S3 Vectors | 4,096 |
| OpenSearch Serverless knn_vector | 16,000 |
| Aurora pgvector HNSW, `vector` | **2,000** |
| Aurora pgvector HNSW, `halfvec` | 4,000 |

- **The largest dimension that all four accept with a plain `vector` HNSW index is 2,000.** With `halfvec` on pgvector it rises to 4,000. The cost is half-precision storage in Postgres only, which breaks like-for-like with the other stores.
- **1024: safe everywhere**, with plain `vector` HNSW. **1536: safe everywhere**, also with plain `vector`. **2048: not safe** with `vector` HNSW (above 2,000); it needs `halfvec(2048)` on pgvector.
- Model overlap: 1024 is the **only** dimension all three embedding models emit natively. Cohere v4 offers {256, 512, 1024, 1536}. Nova 2 offers {256, 384, 1024, 3072}. Qwen3 MRL covers 32–4096. 1536 drops Nova, and 2048 drops both Cohere and Nova. **Use 1024.**

## 7. Datasets

### es.stackoverflow.com (Stack Exchange data dump)
- **Official distribution now:**
  - Since July 2024 the dump is downloaded while logged in, from **profile, then Settings, then "Data dump access"**, separately for each site. You must accept terms saying the file is "for my own use and for projects that do not include training a large language model".
  - Stack Exchange stopped uploading to archive.org. The last official upload was April 2024.
  - Sources: https://devclass.com/2024/07/30/stack-exchange-restricts-access-to-dump-of-user-contributed-data-as-critics-complain-license-permits-reuse-for-any-purpose , https://wiki.archiveteam.org/index.php/Stack_Overflow
  - Secondary sources only; stackoverflow.com help pages are behind Cloudflare and I could not read them.
- **License:** the official archive.org item states "All user content contributed to the Stack Exchange network is cc-by-sa 4.0 licensed" and spells out the attribution rules: show the source, link to the original post, credit the author. https://archive.org/details/stackexchange
  - Historically, content was CC BY-SA 2.5 before 2011-04-08 and 3.0 before 2018-05-02, and newer dumps carry a per-post `ContentLicense` attribute (from memory, see Open risks).
- **Format:** one 7z archive per site containing XML files: **Posts, Users, Votes, Comments, Badges, Tags, PostHistory, PostLinks**. https://archive.org/details/stackexchange
  - The official April 2024 `es.stackoverflow.com.7z` is 528,092,320 bytes.
  - A community re-upload of the **2025-12-31** dump puts `es.stackoverflow.com.7z` at 547,707,624 bytes. https://archive.org/details/stackexchange_20251231 (archive.org metadata API)
- **Data poisoning:** the community uploader says Stack Exchange started "watermarking/data poisoning" with the **2025-06-30** dump, and that it did not appear in 2025-12-31. https://archive.org/details/stackexchange_20251231

### BCN Ley Chile
- **Legacy XML web service (still answers without a key, tested live on 2026-10-08):**
  - `https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma=<id>` returns the **full current version of a norm** as XML. Root is `<Norma xmlns="http://www.leychile.cl/esquemas" ...>` with `Identificador`, `Metadatos/TituloNorma`, `Encabezado/Texto`, and so on. Schema: `EsquemaIntercambioNorma-v1-0.xsd`.
  - Other options:
    - `opt=3&cantidad=N`: latest laws
    - `opt=60` or `opt=61&cadena=...&cantidad=N`: full-text search
    - `opt=4546&idNorma=`
    - `opt=30`, `31`, `36`, `37`: groupings
    - `opt=6&idCategoria=`
  - Sources: endpoint list extracted from the Ley Chile SPA bundle behind https://www.bcn.cl/leychile/consulta/legislacion_abierta_web_service , sample `idNorma=242302` (Constitución).
- **New API (replaces the above):**
  - The same page states web service access "se realiza mediante la nueva API con autenticación por API-KEY" with usage limits. Docs: https://www.bcn.cl/leychile/api/docs (SPA, not readable without JS).
  - Terms v1.1: https://www.bcn.cl/leychile/assets/doc/condiciones-de-uso-api-v1.1.pdf
    - Standard quota is **500 requests per 24 h per key**. Only public institutions with an agreement can request more.
    - **Forbidden**: "extracciones masivas, sistemáticas o automatizadas … con el objeto de replicar … una base de datos sustancialmente equivalente", and "indexación masiva, creación de repositorios estructurados de gran escala".
    - AI training or fine-tuning needs express BCN authorization. Retrieval and consultation alone "no constituye … entrenamiento".
- **License:**
  - Official legal texts are **outside copyright**, citing the principle "los textos oficiales de carácter público no son objeto de propiedad intelectual" (terms §9.d).
  - BCN metadata, schemas, and compilations are under **CC BY-NC-ND 4.0**. Attribute as "Ley Chile – Biblioteca del Congreso Nacional" plus the retrieval date.
  - Source: the terms PDF above.
- **Bulk:** no official bulk dump was found. At 500 req/day, a few hundred selected norms is feasible; "all of Ley Chile" is not, and the terms forbid it. datos.bcn.cl (RDF/SPARQL, metadata only per third-party descriptions) did not answer my probe. https://datos.bcn.cl/es/

---

## Open risks

1. **DynamoDB:** there is no Terraform support (issue #49350 is open). Index creation has to sit outside Terraform or in CloudFormation. Filters are equality-only, and `TopK` is capped at 100, so a fair top-k comparison across stores has to use k ≤ 100.
2. **OpenSearch NextGen pricing:** the Price List SKUs (IndexingOCU/SearchOCU $0.24, storage $0.024/GB-mo) are not labeled NextGen vs Classic. The pricing page shows no NextGen-specific rate. The GPU acceleration rate for NextGen is also unconfirmed: the SKU exists at $0.24/OCU-hr, and it is on by default.
3. **OpenSearch NextGen mapping:** the docs say `engine` is not configurable, but the launch blog example still passes `"engine": "faiss"`. I did not verify whether passing it is rejected or ignored. The max `k` for knn queries on NextGen is not documented in what I read. Default 32x compression changes recall vs the other stores; set `compression_level: "1x"` for a fair comparison.
4. **NextGen policies:** it is unclear whether encryption and network security policies are still mandatory when `CreateCollection` carries an inline `encryptionConfig`. The blog says they must exist; the API reference is silent.
5. **Terraform `collection_group_name`:** it appears on `aws_opensearchserverless_collection` in the current docs, but the CHANGELOG has no entry for when it was added. Pinning ≥ 6.51 is a guess that should be checked with `terraform validate`.
6. **Cohere Embed v4 price:** the $0.12/1M comes from the Price List. A second SKU (`USE1_Cohere_Embed_V4-Units`, $0.00000002 per "Embeddings") is unexplained, and the HTML pricing page did not render. The docs also show the model ID both as `cohere.embed-v4` (prose) and `cohere.embed-v4:0` (table and code). Use `cohere.embed-v4:0`.
7. **Qwen3 on SageMaker:** there is no official AWS example of Qwen3-Embedding-8B on the TEI DLC 1.9.4 or of TEI in Batch Transform. Check that TEI 1.9.4 loads Qwen3 (the card requires ≥ 1.7.2) and that the `last-token` pooling and instruction format are applied client-side. MRL truncation to 1024 means taking the first 1024 dims and **re-normalizing**. The vLLM DLC tag `0.30.0` looked odd but was copied as listed.
8. **Data API and session settings:** I did not verify whether `SET hnsw.*` persists across Data API calls. Plan to use psycopg.
9. **Stack Exchange:** the official help and meta pages (data-dump terms, schema post 2677, per-post `ContentLicense`, CC BY-SA 2.5/3.0/4.0 cut-off dates) could not be fetched because of Cloudflare. Access rules come from secondary sources (devclass, ArchiveTeam). Using a community re-upload rather than your own Settings download sits outside the official route. The no-LLM-training clause should be fine for retrieval embeddings, but that is a judgment call. Watch for poisoned rows if you use the 2025-06-30 dump.
10. **Ley Chile:** the legacy `obtxml` endpoint works without a key today but is officially superseded by the API-KEY API. Its future availability, and whether the 500/day quota applies to it, are unknown. The REST endpoint paths of the new API were not readable (JS-only docs). Building a talk corpus by systematic crawling conflicts with §3.vi/vii of the terms; keep it to a small, declared, attributed set or ask BCN.
11. **pgvector 0.8.2 on 15.x/14.x:** these come from a WebFetch summary of the extension table and were only partly cross-checked with a grep (0.8.2 is confirmed present on the page). Check the exact engine version in `us-east-1` with `describe-db-engine-versions` before creating the cluster.
