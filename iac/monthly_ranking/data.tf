terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }

    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = "${path.module}/../../script/monthly_ranking/src"
  output_path = "${path.module}/package/monthly-ranking-lambda.zip"
}

data "aws_dynamodb_table" "source" {
  name = var.dynamodb_table_name
}
