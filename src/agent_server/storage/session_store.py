import json
from pathlib import Path
from typing import Any

from interop_router.types import ChatMessage
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, UniqueConstraint, create_engine, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from agent_server.schemas.activity import SessionActivity, TaskPermission
from agent_server.schemas.session import MessageOrigin, SessionActivityRecord, SessionChatMessage, SessionConfig

metadata = MetaData()
session_activity_adapter: TypeAdapter[SessionActivity] = TypeAdapter(SessionActivity)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("message_origin", String, nullable=False),
    Column("permission", String, nullable=True),
    Column("agent_id", String, nullable=False),
    Column("chat_message", JSON, nullable=False),
    UniqueConstraint("agent_id", "position"),
)

activities = Table(
    "activities",
    metadata,
    Column("id", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("type", String, nullable=False),
    Column("state", String, nullable=False),
    Column("agent_id", String, nullable=False),
    Column("activity_json", JSON, nullable=False),
    UniqueConstraint("agent_id", "position"),
)

# The config is a per-session singleton, so it always lives in a single row under this fixed key.
SESSION_CONFIG_ID = 1

session_config = Table(
    "session_config",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tool_preset", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("model", String, nullable=False),
)


class SessionStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parents[0].mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{self.database_path.resolve().as_posix()}"
        self.engine: Engine = create_engine(database_url, future=True)
        metadata.create_all(self.engine)

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.engine.dispose()

    def add_chat_message(
        self,
        position: int,
        message: ChatMessage,
        message_origin: MessageOrigin,
        permission: TaskPermission | None = None,
        agent_id: str = "main",
    ) -> None:
        chat_message: Any = json.loads(message.model_dump_json())
        if not isinstance(chat_message, dict):
            raise TypeError("Serialized ChatMessage must be a JSON object.")

        with self.engine.begin() as connection:
            connection.execute(
                chat_messages.insert().values(
                    id=message.id,
                    position=position,
                    timestamp=message.timestamp.isoformat(),
                    created_by=message.created_by,
                    message_origin=message_origin,
                    permission=permission,
                    agent_id=agent_id,
                    chat_message=chat_message,
                )
            )

    def update_chat_message_permission(
        self,
        message_id: str,
        permission: TaskPermission,
    ) -> None:
        statement = update(chat_messages).where(chat_messages.c.id == message_id).values(permission=permission)

        with self.engine.begin() as connection:
            result = connection.execute(statement)

        if result.rowcount != 1:
            raise ValueError(f"Chat message does not exist: {message_id}")

    def save_activity(self, position: int, activity: SessionActivity, agent_id: str = "main") -> None:
        activity_json = activity.model_dump(mode="json")
        values: dict[str, Any] = {
            "id": activity.id,
            "position": position,
            "type": activity.type,
            "state": activity.state,
            "timestamp": activity.timestamp.isoformat(),
            "agent_id": agent_id,
            "activity_json": activity_json,
        }
        update_values = {key: value for key, value in values.items() if key != "id"}
        statement = (
            insert(activities)
            .values(values)
            .on_conflict_do_update(
                index_elements=[activities.c.id],
                set_=update_values,
            )
        )

        with self.engine.begin() as connection:
            connection.execute(statement)

    def update_activity(self, activity: SessionActivity, agent_id: str | None = None) -> None:
        activity_json = activity.model_dump(mode="json")
        values: dict[str, Any] = {
            "timestamp": activity.timestamp.isoformat(),
            "type": activity.type,
            "state": activity.state,
            "activity_json": activity_json,
        }
        if agent_id is not None:
            values["agent_id"] = agent_id

        statement = update(activities).where(activities.c.id == activity.id).values(values)

        with self.engine.begin() as connection:
            result = connection.execute(statement)

        if result.rowcount != 1:
            raise ValueError(f"Activity does not exist: {activity.id}")

    def load_chat_messages(self, agent_id: str | None = None) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for message in self.load_session_chat_messages(agent_id=agent_id):
            messages.append(message.chat_message)

        return messages

    def load_session_chat_messages(self, agent_id: str | None = None) -> list[SessionChatMessage]:
        statement = select(chat_messages)
        if agent_id is not None:
            statement = statement.where(chat_messages.c.agent_id == agent_id)
            statement = statement.order_by(chat_messages.c.position)
        else:
            statement = statement.order_by(
                chat_messages.c.timestamp, chat_messages.c.agent_id, chat_messages.c.position
            )

        with self.engine.begin() as connection:
            rows = connection.execute(statement).mappings().all()

        session_chat_messages: list[SessionChatMessage] = []
        for row in rows:
            chat_message = row["chat_message"]
            if not isinstance(chat_message, dict):
                raise TypeError("chat_message must be a JSON object.")

            origin_value = row["message_origin"]
            message_origin: MessageOrigin
            match origin_value:
                case "user" | "model_output" | "tool_output" | "system_reminder":
                    message_origin = origin_value
                case _:
                    raise ValueError(f"Unknown chat message origin: {origin_value}")

            permission_value = row["permission"]
            permission: TaskPermission | None
            match permission_value:
                case None:
                    permission = None
                case "accepted" | "denied" | "pending" | "not_determined":
                    permission = permission_value
                case _:
                    raise ValueError(f"Unknown chat message permission: {permission_value}")

            session_chat_messages.append(
                SessionChatMessage(
                    position=int(row["position"]),
                    message_origin=message_origin,
                    permission=permission,
                    agent_id=str(row["agent_id"]),
                    chat_message=ChatMessage.from_json(json.dumps(chat_message)),
                )
            )

        return session_chat_messages

    def load_activities(self, agent_id: str | None = None) -> list[SessionActivity]:
        activities_list: list[SessionActivity] = []
        for activity in self.load_session_activities(agent_id=agent_id):
            activities_list.append(activity.activity)

        return activities_list

    def load_session_activities(self, agent_id: str | None = None) -> list[SessionActivityRecord]:
        statement = select(activities)
        if agent_id is not None:
            statement = statement.where(activities.c.agent_id == agent_id)
            statement = statement.order_by(activities.c.position)
        else:
            statement = statement.order_by(activities.c.timestamp, activities.c.agent_id, activities.c.position)

        with self.engine.begin() as connection:
            rows = connection.execute(statement).mappings().all()

        session_activities: list[SessionActivityRecord] = []
        for row in rows:
            activity_json = row["activity_json"]
            if not isinstance(activity_json, dict):
                raise TypeError("activity_json must be a JSON object.")

            activity = session_activity_adapter.validate_python(activity_json)
            session_activities.append(
                SessionActivityRecord(
                    id=str(row["id"]),
                    position=int(row["position"]),
                    type=str(row["type"]),
                    state=activity.state,
                    timestamp=row["timestamp"],
                    activity=activity,
                    agent_id=str(row["agent_id"]),
                )
            )

        return session_activities

    def update_session_config(self, config: SessionConfig) -> None:
        values: dict[str, Any] = {
            "id": SESSION_CONFIG_ID,
            "tool_preset": config.tool_preset,
            "mode": config.mode,
            "model": config.model,
        }
        update_values = {key: value for key, value in values.items() if key != "id"}
        statement = (
            insert(session_config)
            .values(values)
            .on_conflict_do_update(
                index_elements=[session_config.c.id],
                set_=update_values,
            )
        )

        with self.engine.begin() as connection:
            connection.execute(statement)

    def load_or_create_session_config(self, default_model: str | None) -> SessionConfig:
        # Try validating if the default model is valid. If it is not, we will fall back to the default SessionConfig.
        try:
            initial_config = SessionConfig.model_validate({"model": default_model})
        except ValidationError:
            initial_config = SessionConfig()

        # Attempt to create a session's config row if an existing row does not already exist.
        insert_statement = (
            insert(session_config)
            .values(
                id=SESSION_CONFIG_ID,
                tool_preset=initial_config.tool_preset,
                mode=initial_config.mode,
                model=initial_config.model,
            )
            .on_conflict_do_nothing(index_elements=[session_config.c.id])
        )
        # This gets the current row.
        select_statement = select(session_config).where(session_config.c.id == SESSION_CONFIG_ID)

        with self.engine.begin() as connection:
            connection.execute(insert_statement)
            row = connection.execute(select_statement).mappings().one()

        return SessionConfig(tool_preset=row["tool_preset"], mode=row["mode"], model=row["model"])

    def load_session_config(self) -> SessionConfig:
        statement = select(session_config).where(session_config.c.id == SESSION_CONFIG_ID)

        with self.engine.begin() as connection:
            row = connection.execute(statement).mappings().first()

        if row is None:
            return SessionConfig()

        return SessionConfig(tool_preset=row["tool_preset"], mode=row["mode"], model=row["model"])
