output "url" {
  value = local.event ? "https://${var.event_hostname}/" : "${aws_apigatewayv2_api.event.api_endpoint}/"
}

output "table" {
  value = awscc_dynamodb_table.search.table_name
}

output "region" {
  value = var.region
}

output "table_so" {
  value = local.event ? awscc_dynamodb_table.search_so[0].table_name : ""
}

output "docs_table" {
  value = local.event ? aws_dynamodb_table.docs[0].name : ""
}
