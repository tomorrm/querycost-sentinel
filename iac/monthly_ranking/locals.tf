locals {
  name = "querycost-sentinel-monthly-ranking"

  tags = {
    Application = "querycost-sentinel"
    Module      = "monthly-ranking"
    ManagedBy   = "terraform"
  }
}
