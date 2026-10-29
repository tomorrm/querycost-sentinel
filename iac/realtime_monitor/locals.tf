locals {
  name = "querycost-sentinel-realtime-monitor"

  tags = {
    Application = "querycost-sentinel"
    Module      = "realtime-monitor"
    ManagedBy   = "terraform"
  }
}
