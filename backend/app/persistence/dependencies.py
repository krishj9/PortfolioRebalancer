from functools import lru_cache

from app.core.config import get_settings
from app.persistence.dynamodb_store import DynamoDBWorkflowStore
from app.persistence.memory_store import InMemoryWorkflowStore, WorkflowStore


@lru_cache
def get_workflow_store() -> WorkflowStore:
    settings = get_settings()
    if settings.persistence_mode == "memory":
        return InMemoryWorkflowStore()
    if settings.persistence_mode == "firestore":
        from app.persistence.firestore_store import FirestoreWorkflowStore

        return FirestoreWorkflowStore(settings)
    return DynamoDBWorkflowStore(settings)
