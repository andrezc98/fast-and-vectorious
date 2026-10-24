data "aws_ssm_parameter" "al2023_arm" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runner" {
  name               = "fv-runner"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  # required: fv-gha may only CreateRole/PutRolePolicy with this boundary (Task 15)
  permissions_boundary = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:policy/fv-boundary"
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "runner" {
  statement {
    sid       = "DynamoLab"
    actions   = ["dynamodb:*"]
    resources = ["arn:aws:dynamodb:us-east-1:${data.aws_caller_identity.me.account_id}:table/fv-*"]
  }
  statement {
    sid       = "DynamoList"
    actions   = ["dynamodb:ListTables"]
    resources = ["*"]
  }
  statement {
    sid       = "S3Vectors"
    actions   = ["s3vectors:*"]
    resources = [aws_s3vectors_vector_bucket.lab.vector_bucket_arn, "${aws_s3vectors_vector_bucket.lab.vector_bucket_arn}/*"]
  }
  statement {
    sid       = "DataBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
  }
  statement {
    sid       = "OpenSearch"
    actions   = ["aoss:APIAccessAll"]
    resources = [aws_opensearchserverless_collection.lab.arn]
  }
  statement {
    sid       = "Bedrock"
    actions   = ["bedrock:InvokeModel"] # Converse is authorized by InvokeModel; there is no bedrock:Converse action
    resources = ["*"]
  }
  statement {
    sid       = "AuroraSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_rds_cluster.lab.master_user_secret[0].secret_arn]
  }
  statement {
    sid = "QwenEndpoint"
    actions = ["sagemaker:CreateModel", "sagemaker:DeleteModel", "sagemaker:CreateEndpointConfig", "sagemaker:DeleteEndpointConfig",
      "sagemaker:CreateEndpoint", "sagemaker:DeleteEndpoint", "sagemaker:DescribeEndpoint", "sagemaker:InvokeEndpoint",
    "sagemaker:AddTags"] # qwen.py passes Tags= on every Create*, which needs AddTags
    resources = ["arn:aws:sagemaker:us-east-1:${data.aws_caller_identity.me.account_id}:*/fv-*"]
  }
  statement {
    sid       = "PassSageMakerRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.sagemaker.arn]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "runner" {
  role   = aws_iam_role.runner.id
  policy = data.aws_iam_policy_document.runner.json
}

resource "aws_iam_instance_profile" "runner" {
  name = "fv-runner"
  role = aws_iam_role.runner.name
}

data "aws_iam_policy_document" "sm_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker" {
  name               = "fv-sagemaker"
  assume_role_policy = data.aws_iam_policy_document.sm_assume.json
  # required: fv-gha may only CreateRole/PutRolePolicy with this boundary (Task 15)
  permissions_boundary = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:policy/fv-boundary"
}

data "aws_iam_policy_document" "sagemaker" {
  statement {
    actions   = ["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = ["*"]
  }
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sagemaker" {
  role   = aws_iam_role.sagemaker.id
  policy = data.aws_iam_policy_document.sagemaker.json
}

resource "aws_instance" "runner" {
  count                  = var.runner_enabled ? 1 : 0
  ami                    = data.aws_ssm_parameter.al2023_arm.value
  instance_type          = "m7g.2xlarge" # 8 vCPU / 32 GiB: exact top-k over 100k x 1024 floats in memory
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.runner.id]
  iam_instance_profile   = aws_iam_instance_profile.runner.name
  root_block_device {
    volume_size = 60
    volume_type = "gp3"
  }
  metadata_options {
    http_tokens = "required"
  }
  user_data = <<-EOF
    #!/bin/bash
    dnf install -y git tmux
    cat > /etc/profile.d/fv.sh <<'ENV'
    export FV_RUNNER=1 AWS_REGION=us-east-1
    export FV_DATA_BUCKET=${aws_s3_bucket.data.id}
    export FV_VECTOR_BUCKET=${aws_s3vectors_vector_bucket.lab.vector_bucket_name}
    export FV_AOSS_ENDPOINT=${aws_opensearchserverless_collection.lab.collection_endpoint}
    export FV_AURORA_HOST=${aws_rds_cluster.lab.endpoint}
    export FV_AURORA_SECRET_ARN=${aws_rds_cluster.lab.master_user_secret[0].secret_arn}
    export FV_DOCS_TABLE=${aws_dynamodb_table.docs.name}
    export FV_SAGEMAKER_ROLE_ARN=${aws_iam_role.sagemaker.arn}
    export FV_TEI_IMAGE=${var.tei_image}
    ENV
    su - ec2-user -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  EOF
}
