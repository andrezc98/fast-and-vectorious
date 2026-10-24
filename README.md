# Fast & Vectorious: DynamoDB, S3, OpenSearch y Aurora, medidos

Laboratorio de la charla para el AWS Student Community Day Chile 2026. Carga la
misma colección de embeddings en DynamoDB, S3 Vectors, OpenSearch Serverless y
Aurora PostgreSQL (pgvector) y mide latencia, recall y costo con las mismas
consultas.

> Estado: mediciones del laboratorio ejecutadas el 8 y 9 de octubre de 2026.
> Fuente de cada afirmación de la charla: `docs/claims.md`

## Evento

| | |
|---|---|
| Fecha | Sábado 24 de octubre de 2026, 12:15–13:00 |
| Lugar | Duoc UC, Sede Maipú (Av. Esquina Blanca 501). Auditorio principal, piso −1, Torre 1 |
| Formato | Presencial, español, nivel intermedio |
| Perfil | https://scdchile.cl/speakers/andres-zeballos |

## Versiones probadas
(se llena con cada corrida; verificar contra la documentación del día)

Resueltas con `uv sync` (Python 3.13, `runner/uv.lock`): boto3 1.43.109, numpy 2.5.3,
opensearch-py 3.2.0, psycopg 3.3.6 (psycopg-binary 3.3.6), pgvector 0.5.0, defusedxml 0.7.1.

- Terraform 1.15.2, proveedor `hashicorp/aws` 6.68.0 (`infra/.terraform.lock.hcl`).
- Aurora PostgreSQL 17.11 (aarch64) con pgvector 0.8.2 disponible (verificado en el clúster el 8 de octubre de 2026).
- OpenSearch Serverless de nueva generación (`NEXTGEN`). La aceleración vectorial con GPU viene activa; la API no permitió desactivarla a nivel de colección (AWS documenta un ajuste por índice). Se cobra aparte.
- Preguntas de Ley Chile generadas con Claude Haiku 5.5 en Bedrock (`us.anthropic.claude-haiku-5-5`).
- Red: el runner llega a S3 Vectors y a OpenSearch por endpoints de interfaz (PrivateLink),
  y a S3 y DynamoDB por endpoints de gateway. La colección de OpenSearch no acepta tráfico público.

## Problemas conocidos

## Atribución

- Contenido de Stack Overflow en español: CC BY-SA 4.0, del volcado de datos de Stack Exchange
  del 7 de abril de 2024 (copia en archive.org, ítem `stackexchange`, archivo `es.stackoverflow.com.7z`,
  MD5 6024c3ad48832ea69d34c6ed60914903).
  Toda pregunta citada en las slides lleva el enlace a la original y el nombre de su autor.
- Ley Chile – Biblioteca del Congreso Nacional, consultado el 8 de octubre de 2026. Normas usadas:
  Constitución Política de la República y los códigos Civil, del Trabajo, Penal, de Comercio,
  Tributario, Procesal Penal, de Procedimiento Civil, Sanitario, de Aguas y de Minería
  (`data-sources/ley-norms.tsv`). Se omiten los artículos derogados, que solo dicen "Derogado".
