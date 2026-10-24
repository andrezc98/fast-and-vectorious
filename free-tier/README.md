# Busca en la Ley Chile (proyecto de ejemplo)

El proyecto de la charla *Fast & Vectorious* que puedes desplegar en tu propia cuenta de AWS con un comando.
Escribes una pregunta en lenguaje común y te devuelve los 3 artículos de la Ley Chile más cercanos.

```
Pregunta → Cohere Embed v4 (Bedrock) → búsqueda vectorial en DynamoDB → 3 artículos
```

Usa DynamoDB porque, en las mediciones de la charla, es la base sin costo mensual fijo y sin espera en la
primera consulta del día (87 ms después de 16 minutos sin tráfico).

## Qué se crea

- Una tabla de DynamoDB con índice vectorial (1.024 dimensiones, coseno), en modo bajo demanda
- Una función Lambda (Python 3.13, ARM) que sirve la página y la API
- Una HTTP API de API Gateway con límite de 5 consultas por segundo
- Una tabla pequeña que cuenta las consultas y corta en 1.000 (configurable), antes de llamar a Bedrock

## Cuánto cuesta (us-east-1, precios de lista de octubre de 2026)

| Concepto | Costo |
|---|---|
| Generar los embeddings de 8.851 artículos (una sola vez) | ~USD 0,31 |
| 1.000 preguntas al mes (embedding + búsqueda + API + Lambda) | ~USD 0,02 |
| Guardar los vectores | dentro de los 25 GB gratis de DynamoDB |

En una cuenta nueva lo cubren los créditos de la capa gratuita de AWS; después son centavos al mes.
Revisa en tu consola qué plan tiene tu cuenta. Cuando termines, borra todo con `./destroy.sh`.

## Antes de empezar

1. Una cuenta de AWS con credenciales configuradas (`aws sts get-caller-identity` debe responder)
2. [Terraform](https://developer.hashicorp.com/terraform/install) y [uv](https://docs.astral.sh/uv/getting-started/installation/)
3. Acceso al modelo **Cohere Embed v4** en Amazon Bedrock, región us-east-1. Es un modelo de AWS Marketplace:
   la primera llamada puede tardar o fallar mientras se activa la suscripción. Si falla, vuelve a intentar en
   unos minutos.
4. **Cuota de Bedrock mayor que cero.** Algunas cuentas nuevas parten con cuota 0 para los modelos de
   embeddings (lo vimos en una cuenta de prueba en octubre de 2026: el error dice *Too many tokens per day*).
   Revísalo en *Service Quotas → Amazon Bedrock → Cohere Embed V4*. Si está en 0, AWS la va subiendo con el uso
   de la cuenta, o puedes pedirla a soporte.

## Desplegar y borrar

```bash
git clone https://github.com/andrezc98/fast-and-vectorious
cd fast-and-vectorious/free-tier
./deploy.sh      # al final imprime la URL
./destroy.sh     # borra todo lo que se creó
```

La primera vez descarga las 11 normas desde la Biblioteca del Congreso Nacional (una solicitud por norma,
con pausa entre cada una) y genera los embeddings: toma unos minutos.

## AWS Academy / Learner Lab

No está probado. Los laboratorios de AWS Academy suelen restringir Bedrock y AWS Marketplace, y el rol que
entregan no se puede cambiar. Si tu laboratorio permite Bedrock con Cohere Embed v4, debería funcionar.

## Aviso

Búsqueda educativa. No es asesoría legal: revisa siempre el texto vigente en
[Ley Chile](https://www.bcn.cl/leychile) (Biblioteca del Congreso Nacional).
