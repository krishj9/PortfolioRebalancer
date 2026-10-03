# BigQuery Dataset and Tables for Architecture §6

resource "google_bigquery_dataset" "portfolio_analytics" {
  dataset_id                  = "portfolio_analytics"
  friendly_name               = "Portfolio Rebalancer Analytics"
  description                 = "Analytics dataset for portfolio rebalancer proposal events"
  location                    = var.region
  default_table_expiration_ms = null

  labels = {
    env = "dev"
    app = "portfolio-rebalancer"
  }
}

resource "google_bigquery_table" "proposal_events" {
  dataset_id          = google_bigquery_dataset.portfolio_analytics.dataset_id
  table_id            = "proposal_events"
  description         = "Proposal lifecycle events for analytics and reporting"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "event_ts"
  }

  schema = jsonencode([
    {
      name        = "event_ts"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when the proposal event occurred"
    },
    {
      name        = "proposal_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique identifier of the proposal / approval artifact"
    },
    {
      name        = "account_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Portfolio account ID"
    },
    {
      name        = "event_type"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Event type: PROPOSAL_CREATED, PROPOSAL_APPROVE, PROPOSAL_REJECT, etc."
    },
    {
      name        = "workflow_state"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Workflow state at the time of the event"
    },
    {
      name        = "max_abs_drift_pct"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Maximum absolute drift percentage across asset classes"
    },
    {
      name        = "trade_count"
      type        = "INTEGER"
      mode        = "NULLABLE"
      description = "Number of trades in the proposal"
    },
    {
      name        = "run_id"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Correlation run ID"
    }
  ])
}
