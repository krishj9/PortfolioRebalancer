"""Smoke tests for BigQuery proposal events analytics (Task P2-06)."""

from datetime import UTC, datetime
import os
import uuid
import pytest

from app.adapters.analytics import BigQueryAnalyticsAdapter


@pytest.mark.gcp
@pytest.mark.skipif(
    not os.environ.get("RUN_GCP_TESTS"),
    reason="Set RUN_GCP_TESTS=1 to run smoke tests against live BigQuery",
)
def test_bigquery_proposal_events_smoke():
    """Verify live emission of proposal event to BigQuery and query execution."""
    from google.cloud import bigquery

    project_id = os.environ.get("GCP_PROJECT", "mybrightday-dev")
    dataset_id = "portfolio_analytics"
    table_id = "proposal_events"

    adapter = BigQueryAnalyticsAdapter(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
    )

    test_proposal_id = f"apr_bq_test_{uuid.uuid4().hex[:8]}"
    test_run_id = f"run_bq_test_{uuid.uuid4().hex[:8]}"

    # 1. Emit proposal event
    success = adapter.emit_proposal_event(
        event_ts=datetime.now(UTC),
        proposal_id=test_proposal_id,
        account_id="acct_demo",
        event_type="PROPOSAL_CREATED",
        workflow_state="NORMAL",
        max_abs_drift_pct=10.0,
        trade_count=2,
        run_id=test_run_id,
    )
    assert success is True, "Failed to emit proposal event to BigQuery"

    # 2. Run Query 1 from Architecture §6
    client = bigquery.Client(project=project_id)
    query = f"""
    SELECT
      workflow_state,
      COUNT(DISTINCT proposal_id) AS proposal_count,
      COUNT(DISTINCT account_id) AS accounts_affected,
      ROUND(AVG(max_abs_drift_pct), 2) AS avg_max_drift_pct,
      SUM(trade_count) AS total_trades_proposed
    FROM
      `{project_id}.{dataset_id}.{table_id}`
    WHERE
      event_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
    GROUP BY
      workflow_state
    ORDER BY
      proposal_count DESC
    """
    job = client.query(query)
    results = list(job.result())

    assert len(results) > 0, "Query returned no results after event emission"
    row = results[0]
    assert row.workflow_state == "NORMAL"
    assert row.proposal_count >= 1
    assert row.accounts_affected >= 1
    assert row.total_trades_proposed >= 2
