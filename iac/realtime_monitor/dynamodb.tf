resource "aws_dynamodb_table" "this" {
  name         = var.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "QueryId"
  range_key    = "TimeStamp"

  stream_enabled = false

  attribute {
    name = "QueryId"
    type = "S"
  }

  attribute {
    name = "TimeStamp"
    type = "S"
  }

  attribute {
    name = "YearMonth"
    type = "S"
  }

  attribute {
    name = "UserName"
    type = "S"
  }

  ttl {
    attribute_name = "TTL"
    enabled        = true
  }

  global_secondary_index {
    name            = "YearMonthIndex"
    hash_key        = "YearMonth"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "UserNameIndex"
    hash_key        = "UserName"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.this.arn
  }

  deletion_protection_enabled = var.deletion_protection_enabled

  tags = local.tags
}
