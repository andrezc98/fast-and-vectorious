# HTTP API in front of the Lambda in both modes: throttling for everyone (a bare Function URL has none), about
# USD 1 per million requests. Event mode adds the custom domain (DNS in Cloudflare) and the event code.

resource "aws_apigatewayv2_api" "event" {
  name          = var.name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "event" {
  api_id                 = aws_apigatewayv2_api.event.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "event" {
  for_each  = toset(["GET /", "GET /ask", "GET /top", "GET /state", "GET /bet", "GET /wake", "GET /admin", "GET /admin/api", "POST /admin/api"])
  api_id    = aws_apigatewayv2_api.event.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.event.id}"
}

resource "aws_apigatewayv2_stage" "event" {
  depends_on  = [aws_apigatewayv2_route.event]
  api_id      = aws_apigatewayv2_api.event.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_rate_limit  = local.event ? 20 : 5 # event: a room of ~100 phones tapping at once
    throttling_burst_limit = local.event ? 50 : 20
  }
  # giveaway: every screen checks the round every ~3 s (100 screens ~ 35 req/s); cheap reads, no Bedrock
  dynamic "route_settings" {
    for_each = local.event ? ["GET /state", "GET /bet"] : []
    content {
      route_key              = route_settings.value
      throttling_rate_limit  = 100
      throttling_burst_limit = 200
    }
  }
}

resource "aws_lambda_permission" "event" {
  statement_id  = "http-api"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.event.execution_arn}/*/*"
}

# --- certificate validated through Cloudflare, then the custom domain ---
resource "aws_acm_certificate" "event" {
  count             = local.event ? 1 : 0
  domain_name       = var.event_hostname
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_dns_record" "validation" {
  count   = local.event ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = trimsuffix(one(aws_acm_certificate.event[0].domain_validation_options).resource_record_name, ".")
  type    = one(aws_acm_certificate.event[0].domain_validation_options).resource_record_type
  content = trimsuffix(one(aws_acm_certificate.event[0].domain_validation_options).resource_record_value, ".")
  ttl     = 60
  proxied = false
}

resource "aws_acm_certificate_validation" "event" {
  count                   = local.event ? 1 : 0
  certificate_arn         = aws_acm_certificate.event[0].arn
  validation_record_fqdns = [cloudflare_dns_record.validation[0].name]
}

resource "aws_apigatewayv2_domain_name" "event" {
  count       = local.event ? 1 : 0
  domain_name = var.event_hostname
  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.event[0].certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "event" {
  count       = local.event ? 1 : 0
  api_id      = aws_apigatewayv2_api.event.id
  domain_name = aws_apigatewayv2_domain_name.event[0].id
  stage       = aws_apigatewayv2_stage.event.id
}

resource "cloudflare_dns_record" "event" {
  count   = local.event ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = var.event_hostname
  type    = "CNAME"
  content = aws_apigatewayv2_domain_name.event[0].domain_name_configuration[0].target_domain_name
  ttl     = 60
  proxied = false # TLS terminates at API Gateway with the ACM certificate
}
