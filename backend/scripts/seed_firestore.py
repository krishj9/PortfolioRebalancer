"""Seed Firestore database with default portfolios and sample client profiles (Task P1-11)."""

import json
import logging
import os
import sys
from pathlib import Path

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.config import Settings
from app.persistence.firestore_store import FirestoreWorkflowStore
from app.persistence.memory_store import default_portfolios

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def seed_firestore(
    project_id: str | None = None,
    database: str = "portfolio-rebalancer",
) -> None:
    """Seed Firestore database with default portfolios and seed data."""
    project = project_id or os.environ.get("GCP_PROJECT", "mybrightday-dev")
    logger.info(f"Connecting to Firestore: project={project}, database={database}")

    settings = Settings(
        persistence_mode="firestore",
        firestore_project_id=project,
        firestore_database=database,
        seed_default_portfolios=False,  # We explicitly seed below
    )

    store = FirestoreWorkflowStore(settings=settings)

    # 1. Seed default domain portfolios (acct_demo, acct_aggressive, acct_conservative)
    portfolios = default_portfolios()
    logger.info(f"Seeding {len(portfolios)} default portfolios into Firestore...")
    for portfolio in portfolios:
        store.save_portfolio(portfolio)
        logger.info(
            f"  - Saved portfolio: account_id={portfolio.account_profile.account_id}, "
            f"client_id={portfolio.client_profile.client_id}, "
            f"total_value=${portfolio.portfolio_snapshot.total_value}"
        )

    # 2. Check for optional seed files in seeds/ directory
    repo_root = backend_dir.parent
    seeds_dir = repo_root / "seeds"

    if seeds_dir.exists():
        client_file = seeds_dir / "client_profiles.jsonl"
        if client_file.exists():
            logger.info(f"Found client profiles seed: {client_file}")
            with open(client_file) as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        logger.info(f"  - Client Profile: {data.get('client_id')} ({data.get('display_label')})")

        account_file = seeds_dir / "account_profiles.jsonl"
        if account_file.exists():
            logger.info(f"Found account profiles seed: {account_file}")
            with open(account_file) as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        logger.info(f"  - Account Profile: {data.get('account_id')} (client={data.get('client_id')})")

    existing_portfolios = store.list_portfolios()
    logger.info(f"\nFirestore seeding completed successfully! Total portfolios in store: {len(existing_portfolios)}")


if __name__ == "__main__":
    project_arg = sys.argv[1] if len(sys.argv) > 1 else None
    seed_firestore(project_id=project_arg)
