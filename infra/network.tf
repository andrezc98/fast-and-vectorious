data "aws_availability_zones" "up" {
  state = "available"
}

resource "aws_vpc" "lab" {
  cidr_block           = "10.43.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "fv-lab" }
}

resource "aws_internet_gateway" "lab" {
  vpc_id = aws_vpc.lab.id
}

# Runner subnet: public IP, no inbound rules, reached through SSM. Aurora sits in the private ones.
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = cidrsubnet(aws_vpc.lab.cidr_block, 4, 0)
  availability_zone       = data.aws_availability_zones.up.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "fv-public" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.lab.id
  cidr_block        = cidrsubnet(aws_vpc.lab.cidr_block, 4, count.index + 1)
  availability_zone = data.aws_availability_zones.up.names[count.index]
  tags              = { Name = "fv-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Free gateway endpoints: S3 (data bucket) and DynamoDB traffic stays on the AWS network.
resource "aws_vpc_endpoint" "gateway" {
  for_each          = toset(["s3", "dynamodb"])
  vpc_id            = aws_vpc.lab.id
  service_name      = "com.amazonaws.us-east-1.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id, aws_vpc.lab.main_route_table_id]
}

resource "aws_security_group" "runner" {
  name   = "fv-runner"
  vpc_id = aws_vpc.lab.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "aurora" {
  name   = "fv-aurora"
  vpc_id = aws_vpc.lab.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.runner.id]
  }
}

# Interface endpoints so S3 Vectors and OpenSearch NextGen traffic also stays off the
# internet. S3 Vectors is its own service (the s3 gateway does not cover it); NextGen
# uses the standard aoss-data service, not the AOSS-managed CreateVpcEndpoint
# (https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vpc.html).
# One AZ only, the runner's: USD 0.01/endpoint-hour + USD 0.01/GB (Price List, 2026-10-08).
resource "aws_security_group" "endpoints" {
  name   = "fv-endpoints"
  vpc_id = aws_vpc.lab.id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.runner.id]
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(["s3vectors", "aoss-data"])
  vpc_id              = aws_vpc.lab.id
  service_name        = "com.amazonaws.us-east-1.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.public.id]
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "fv-${each.key}" }
}
