"""Sessions adapter for managing conversational sessions and events (Task P2-01).

Implements session lifecycle and event ingestion via Vertex AI Agent Platform Sessions API:
- client.sessions.create
- client.sessions.get
- client.sessions.events.append
- client.sessions.events.list
- client.sessions.delete
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import datetime
import logging
from pathlib import Path
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)


@dataclass
class SessionEvent:
    event_id: str
    author: str  # "user" | "model"
    text: str
    timestamp: datetime.datetime
    invocation_id: Optional[str] = None


class BaseSessionsAdapter(ABC):
    """Abstract interface for session and event persistence."""

    @abstractmethod
    def create_or_get_session(self, user_id: str, session_id: Optional[str] = None) -> str:
        """Create a new session or reuse an existing session. Returns session identifier/name."""
        pass

    @abstractmethod
    def append_user_event(
        self,
        session_name: str,
        text: str,
        invocation_id: Optional[str] = None,
    ) -> Any:
        """Append a user event to the session."""
        pass

    @abstractmethod
    def append_summary_event(
        self,
        session_name: str,
        text: str,
        invocation_id: Optional[str] = None,
    ) -> Any:
        """Append a model summary event to the session."""
        pass

    @abstractmethod
    def list_events(self, session_name: str) -> list[Any]:
        """List all events for a given session."""
        pass

    @abstractmethod
    def delete_session(self, session_name: str) -> bool:
        """Delete a session by name."""
        pass


class InMemorySessionsAdapter(BaseSessionsAdapter):
    """In-memory session adapter for local development, fallback, and unit tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[SessionEvent]] = {}

    def create_or_get_session(self, user_id: str, session_id: Optional[str] = None) -> str:
        if session_id and session_id in self._sessions:
            return session_id

        sid = (
            session_id
            if session_id and not session_id.startswith("ses_")
            else f"session_{uuid.uuid4().hex[:12]}"
        )
        self._sessions[sid] = {
            "user_id": user_id,
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
        if sid not in self._events:
            self._events[sid] = []
        return sid

    def append_user_event(
        self,
        session_name: str,
        text: str,
        invocation_id: Optional[str] = None,
    ) -> Any:
        now = datetime.datetime.now(datetime.timezone.utc)
        evt = SessionEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            author="user",
            text=text,
            timestamp=now,
            invocation_id=invocation_id,
        )
        self._events.setdefault(session_name, []).append(evt)
        return evt

    def append_summary_event(
        self,
        session_name: str,
        text: str,
        invocation_id: Optional[str] = None,
    ) -> Any:
        now = datetime.datetime.now(datetime.timezone.utc)
        evt = SessionEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            author="model",
            text=text,
            timestamp=now,
            invocation_id=invocation_id,
        )
        self._events.setdefault(session_name, []).append(evt)
        return evt

    def list_events(self, session_name: str) -> list[Any]:
        return list(self._events.get(session_name, []))

    def delete_session(self, session_name: str) -> bool:
        self._sessions.pop(session_name, None)
        self._events.pop(session_name, None)
        return True


class AgentPlatformSessionsAdapter(BaseSessionsAdapter):
    """Sessions adapter using Vertex AI Agent Platform Sessions API."""

    def __init__(
        self,
        project: str = "mybrightday-dev",
        location: str = "us-central1",
        runtime_name: Optional[str] = None,
        client: Any = None,
    ) -> None:
        self.project = project
        self.location = location
        self._runtime_name = runtime_name
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import agentplatform

            self._client = agentplatform.Client(project=self.project, location=self.location)
        return self._client

    @property
    def runtime_name(self) -> str:
        if not self._runtime_name:
            deployed_file = (
                Path(__file__).resolve().parent.parent
                / "agent_runtime"
                / "deployed_runtime.txt"
            )
            if deployed_file.exists():
                self._runtime_name = deployed_file.read_text().strip()
            if not self._runtime_name:
                raise ValueError(
                    "Agent Runtime resource name is required for AgentPlatformSessionsAdapter"
                )
        return self._runtime_name

    def create_or_get_session(self, user_id: str, session_id: Optional[str] = None) -> str:
        """Create or reuse a session.

        If session_id is provided, attempts to get it. If valid, returns the full session name.
        Otherwise creates a new session via client.sessions.create.
        """
        if session_id:
            # Check if it's already a full session path
            session_name = (
                session_id
                if session_id.startswith("projects/")
                else f"{self.runtime_name}/sessions/{session_id}"
            )
            try:
                self.client.sessions.get(name=session_name)
                logger.info(f"Reusing existing session: {session_name}")
                return session_name
            except Exception as e:
                logger.warning(
                    f"Session {session_name} could not be retrieved ({e}). Creating a new session."
                )

        op = self.client.sessions.create(name=self.runtime_name, user_id=user_id)
        session_name = op.response.name
        logger.info(f"Created new session: {session_name} for user: {user_id}")
        return session_name

    def append_user_event(
        self,
        session_name: str,
        text: str,
        invocation_id: Optional[str] = None,
    ) -> Any:
        now = datetime.datetime.now(datetime.timezone.utc)
        inv_id = invocation_id or f"inv-{uuid.uuid4().hex[:8]}"
        return self.client.sessions.events.append(
            name=session_name,
            author="user",
            invocation_id=inv_id,
            timestamp=now,
            config={
                "content": {
                    "role": "user",
                    "parts": [{"text": text}],
                }
            },
        )

    def append_summary_event(
        self,
        session_name: str,
        text: str,
        invocation_id: Optional[str] = None,
    ) -> Any:
        now = datetime.datetime.now(datetime.timezone.utc)
        inv_id = invocation_id or f"inv-{uuid.uuid4().hex[:8]}"
        return self.client.sessions.events.append(
            name=session_name,
            author="model",
            invocation_id=inv_id,
            timestamp=now,
            config={
                "content": {
                    "role": "model",
                    "parts": [{"text": text}],
                }
            },
        )

    def list_events(self, session_name: str) -> list[Any]:
        return list(self.client.sessions.events.list(name=session_name))

    def delete_session(self, session_name: str) -> bool:
        op = self.client.sessions.delete(name=session_name)
        return bool(getattr(op, "done", True))


def get_sessions_adapter(
    mode: Optional[str] = None,
    project: Optional[str] = None,
    location: Optional[str] = None,
    runtime_name: Optional[str] = None,
) -> BaseSessionsAdapter:
    """Get sessions adapter based on configuration mode."""
    from app.core.config import get_settings

    settings = get_settings()
    effective_mode = mode or settings.sessions_mode
    if effective_mode == "agent_platform":
        return AgentPlatformSessionsAdapter(
            project=project or settings.project_id,
            location=location or settings.gcp_location,
            runtime_name=runtime_name or settings.agent_runtime_resource_name or None,
        )
    return InMemorySessionsAdapter()
