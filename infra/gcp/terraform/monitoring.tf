# ==============================================================================
# Cloud Monitoring and Logging: Log-Based Metrics & Observability Dashboard
# Task P4-02
# ==============================================================================

# 1. Log-Based Metric: Content Blocked (Model Armor & Prompt Injection Screening)
resource "google_logging_metric" "content_blocked" {
  name        = "rebalancer/content_blocked_count"
  project     = var.project_id
  description = "Count of requests blocked by Model Armor content safety and prompt injection guardrails"

  filter = "resource.type=\"cloud_run_revision\" AND (jsonPayload.event_type=\"CONTENT_BLOCKED\" OR jsonPayload.guardrail_result.action=\"BLOCKED\" OR textPayload=~\"CONTENT_BLOCKED\")"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "environment"
      value_type  = "STRING"
      description = "Deployment environment"
    }
  }

  label_extractors = {
    "environment" = "EXTRACT(jsonPayload.environment)"
  }
}

# 2. Log-Based Metric: Tool Denied (Direct Bypass & Unauthorized Tool Invocations)
resource "google_logging_metric" "tool_denied" {
  name        = "rebalancer/tool_denied_count"
  project     = var.project_id
  description = "Count of unauthorized direct tool invocation and bypass attempts blocked by caller check"

  filter = "resource.type=\"cloud_run_revision\" AND (jsonPayload.event_type=\"TOOL_DENIED\" OR textPayload=~\"TOOL_DENIED\" OR (httpRequest.status=403 AND httpRequest.requestUrl=~\"/tools/\"))"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "environment"
      value_type  = "STRING"
      description = "Deployment environment"
    }
  }

  label_extractors = {
    "environment" = "EXTRACT(jsonPayload.environment)"
  }
}

# 3. Log-Based Metric: Rebalance Requests Total
resource "google_logging_metric" "rebalance_requests" {
  name        = "rebalancer/rebalance_requests_total"
  project     = var.project_id
  description = "Total number of portfolio rebalance requests received"

  filter = "resource.type=\"cloud_run_revision\" AND (jsonPayload.event_type=\"REQUEST_RECEIVED\" OR httpRequest.requestUrl=~\"/api/v1/rebalance\")"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# 4. Log-Based Metric: Token Usage Total
resource "google_logging_metric" "tokens_total" {
  name        = "rebalancer/tokens_total"
  project     = var.project_id
  description = "Cumulative LLM tokens consumed across Gemini invocations"

  filter = "resource.type=\"cloud_run_revision\" AND jsonPayload.tokens_total > 0"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }

  value_extractor = "EXTRACT(jsonPayload.tokens_total)"
}

# 5. Cloud Monitoring Dashboard
resource "google_monitoring_dashboard" "rebalancer_dashboard" {
  project = var.project_id
  dashboard_json = jsonencode({
    displayName = "Portfolio Rebalancer - Operational & Observability Dashboard (${var.environment})"
    gridLayout = {
      columns = "2"
      widgets = [
        # Widget 1: Cloud Run API Latency (p50, p95, p99)
        {
          title = "API Request Latency (p50, p95, p99)"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"run.googleapis.com/request_latencies\" resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"rebalancer-api\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_PERCENTILE_50"
                      crossSeriesReducer = "REDUCE_NONE"
                    }
                  }
                }
                plotType   = "LINE"
                targetAxis = "Y1"
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"run.googleapis.com/request_latencies\" resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"rebalancer-api\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_PERCENTILE_95"
                      crossSeriesReducer = "REDUCE_NONE"
                    }
                  }
                }
                plotType   = "LINE"
                targetAxis = "Y1"
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"run.googleapis.com/request_latencies\" resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"rebalancer-api\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_PERCENTILE_99"
                      crossSeriesReducer = "REDUCE_NONE"
                    }
                  }
                }
                plotType   = "LINE"
                targetAxis = "Y1"
              }
            ]
            yAxis = {
              label = "Latency (ms)"
              scale = "LINEAR"
            }
          }
        },
        # Widget 2: Request Count & HTTP Status Breakdown
        {
          title = "API Request Volume by Status Code"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"run.googleapis.com/request_count\" resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"rebalancer-api\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                      groupByFields      = ["metric.label.\"response_code_class\""]
                    }
                  }
                }
                plotType   = "STACKED_BAR"
                targetAxis = "Y1"
              }
            ]
            yAxis = {
              label = "Requests / sec"
              scale = "LINEAR"
            }
          }
        },
        # Widget 3: Security & Governance Events (Content Blocked & Tool Denied)
        {
          title = "Security & Governance Events (Model Armor & Tool Denial)"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/rebalancer/content_blocked_count\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                    }
                  }
                }
                plotType   = "LINE"
                targetAxis = "Y1"
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/rebalancer/tool_denied_count\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                    }
                  }
                }
                plotType   = "LINE"
                targetAxis = "Y1"
              }
            ]
            yAxis = {
              label = "Events / sec"
              scale = "LINEAR"
            }
          }
        },
        # Widget 4: LLM Token Consumption
        {
          title = "LLM Token Consumption Rate"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/rebalancer/tokens_total\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                    }
                  }
                }
                plotType   = "LINE"
                targetAxis = "Y1"
              }
            ]
            yAxis = {
              label = "Tokens / sec"
              scale = "LINEAR"
            }
          }
        }
      ]
    }
  })
}
