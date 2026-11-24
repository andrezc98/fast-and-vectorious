## Baseline (3 reps x 1,000 queries per cell; warm-up and ef sweeps excluded)

| corpus | case | store | ok | errors | p50 ms | p95 ms | recall@10 |
|---|---|---|---|---|---|---|---|
| so | plain | Aurora pgvector | 3000 | 0 | 1.31 | 1.61 | 0.9271 |
| so | plain | DynamoDB | 3000 | 0 | 12.72 | 14.60 | 0.9958 |
| so | plain | OpenSearch Serverless | 3000 | 0 | 29.95 | 41.08 | 0.9609 |
| so | plain | S3 Vectors | 3000 | 0 | 62.27 | 67.27 | 0.9613 |
| so | filter | Aurora pgvector | 3000 | 0 | 46.14 | 81.38 | 1.0000 |
| so | filter | DynamoDB | 3000 | 0 | 24.18 | 29.00 | 0.8988 |
| so | filter | OpenSearch Serverless | 3000 | 0 | 30.89 | 59.87 | 0.9996 |
| so | filter | S3 Vectors | 3000 | 0 | 62.39 | 68.06 | 0.9579 |
| so | join | Aurora pgvector | 3000 | 0 | 1.47 | 3.70 | 0.9271 |
| so | join | DynamoDB | 3000 | 0 | 17.99 | 21.77 | 0.9958 |
| so | join | OpenSearch Serverless | 3000 | 0 | 35.13 | 47.24 | 0.9609 |
| so | join | S3 Vectors | 3000 | 0 | 66.91 | 73.52 | 0.9610 |
| ley | plain | Aurora pgvector | 3000 | 0 | 1.01 | 1.18 | 0.9920 |
| ley | plain | DynamoDB | 3000 | 0 | 9.79 | 11.03 | 1.0000 |
| ley | plain | OpenSearch Serverless | 3000 | 0 | 26.50 | 42.34 | 0.9892 |
| ley | plain | S3 Vectors | 3000 | 0 | 62.56 | 67.88 | 0.9598 |
| ley | filter | Aurora pgvector | 3000 | 0 | 4.56 | 12.73 | 1.0000 |
| ley | filter | DynamoDB | 3000 | 0 | 12.06 | 15.22 | 0.9999 |
| ley | filter | OpenSearch Serverless | 3000 | 0 | 23.94 | 31.88 | 1.0000 |
| ley | filter | S3 Vectors | 3000 | 0 | 62.47 | 67.43 | 0.9593 |
| ley | join | Aurora pgvector | 3000 | 0 | 1.01 | 1.22 | 0.9920 |
| ley | join | DynamoDB | 3000 | 0 | 13.13 | 15.36 | 1.0000 |
| ley | join | OpenSearch Serverless | 3000 | 0 | 34.00 | 51.52 | 0.9892 |
| ley | join | S3 Vectors | 3000 | 0 | 66.23 | 71.88 | 0.9598 |

## Aurora ef_search sweep (so)

| case | ef_search | p50 ms | p95 ms | recall@10 |
|---|---|---|---|---|
| plain | 40 | 1.31 | 1.61 | 0.9271 |
| plain | 100 | 2.70 | 110.42 | 0.9689 |
| plain | 200 | 3.20 | 4.25 | 0.9856 |
| filter | 40 | 46.14 | 81.38 | 1.0000 |
| filter | 100 | 46.29 | 81.90 | 1.0000 |
| filter | 200 | 46.33 | 81.83 | 1.0000 |

## Filtered queries by share of the index that matches the filter (so)

| store | < 0.1% (85 q) | 0.1-1% (125 q) | 1-5% (215 q) | >= 5% (575 q) |
|---|---|---|---|---|
| Aurora pgvector | 1.000 / 0.8 ms | 1.000 / 3.5 ms | 1.000 / 15.1 ms | 1.000 / 67.6 ms |
| DynamoDB | 0.299 / 9.7 ms | 0.767 / 15.3 ms | 0.951 / 22.6 ms | 0.996 / 25.7 ms |
| OpenSearch Serverless | 1.000 / 24.5 ms | 1.000 / 25.6 ms | 1.000 / 27.5 ms | 0.999 / 33.8 ms |
| S3 Vectors | 0.984 / 62.4 ms | 0.950 / 62.6 ms | 0.957 / 62.3 ms | 0.956 / 62.4 ms |

(cell = mean recall@10 / p50 latency)

## Filtered queries by share of the index that matches the filter (ley)

| store | < 0.1% (0 q) | 0.1-1% (0 q) | 1-5% (97 q) | >= 5% (903 q) |
|---|---|---|---|---|
| Aurora pgvector | - | - | 1.000 / 1.8 ms | 1.000 / 4.8 ms |
| DynamoDB | - | - | 0.999 / 10.0 ms | 1.000 / 12.2 ms |
| OpenSearch Serverless | - | - | 1.000 / 23.3 ms | 1.000 / 24.0 ms |
| S3 Vectors | - | - | 0.943 / 62.6 ms | 0.961 / 62.4 ms |

(cell = mean recall@10 / p50 latency)

## Two tags (so, filter): mean recall@10 / p50 over every request for that tag

| store | asp.net | javascript | rare tags (< 0.1%) with >= 10 matching rows |
|---|---|---|---|
| Aurora pgvector | 1.000 / 2.1 ms (3 req) | 1.000 / 81.0 ms (468 req) | 1.000 |
| DynamoDB | 0.867 / 15.3 ms (3 req) | 0.999 / 26.0 ms (468 req) | 0.323 |
| OpenSearch Serverless | 1.000 / 27.1 ms (3 req) | 0.997 / 35.6 ms (468 req) | 1.000 |
| S3 Vectors | 1.000 / 59.4 ms (3 req) | 0.960 / 62.6 ms (468 req) | 0.977 |
