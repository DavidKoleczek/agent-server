import json
from pathlib import Path
from typing import Any

from interop_router.types import ChatMessage
from pydantic import TypeAdapter
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from agent_server.schemas.activity import SessionActivity, TaskPermission
from agent_server.schemas.session import SessionActivityRecord, SessionChatMessage

metadata = MetaData()
session_activity_adapter: TypeAdapter[SessionActivity] = TypeAdapter(SessionActivity)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", String, primary_key=True),
    Column("position", Integer, nullable=False, unique=True),
    Column("timestamp", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("permission", String, nullable=True),
    Column("chat_message", JSON, nullable=False),
)

activities = Table(
    "activities",
    metadata,
    Column("id", String, primary_key=True),
    Column("position", Integer, nullable=False, unique=True),
    Column("timestamp", String, nullable=False),
    Column("type", String, nullable=False),
    Column("state", String, nullable=False),
    Column("activity_json", JSON, nullable=False),
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

    def add_chat_message(self, position: int, message: ChatMessage, permission: TaskPermission | None = None) -> None:
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
                    permission=permission,
                    chat_message=chat_message,
                )
            )

    def save_activity(self, position: int, activity: SessionActivity) -> None:
        activity_json = activity.model_dump(mode="json")
        values: dict[str, Any] = {
            "id": activity.id,
            "position": position,
            "type": activity.type,
            "state": activity.state,
            "timestamp": activity.timestamp.isoformat(),
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

    def update_activity(self, activity: SessionActivity) -> None:
        activity_json = activity.model_dump(mode="json")
        statement = (
            update(activities)
            .where(activities.c.id == activity.id)
            .values(
                timestamp=activity.timestamp.isoformat(),
                type=activity.type,
                state=activity.state,
                activity_json=activity_json,
            )
        )

        with self.engine.begin() as connection:
            result = connection.execute(statement)

        if result.rowcount != 1:
            raise ValueError(f"Activity does not exist: {activity.id}")

    def load_chat_messages(self) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for message in self.load_session_chat_messages():
            messages.append(message.chat_message)

        return messages

    def load_session_chat_messages(self) -> list[SessionChatMessage]:
        statement = select(chat_messages).order_by(chat_messages.c.position)

        with self.engine.begin() as connection:
            rows = connection.execute(statement).mappings().all()

        session_chat_messages: list[SessionChatMessage] = []
        for row in rows:
            chat_message = row["chat_message"]
            if not isinstance(chat_message, dict):
                raise TypeError("chat_message must be a JSON object.")

            permission_value = row["permission"]
            permission: TaskPermission | None
            match permission_value:
                case None:
                    permission = None
                case "accepted" | "denied" | "pending":
                    permission = permission_value
                case _:
                    raise ValueError(f"Unknown chat message permission: {permission_value}")

            session_chat_messages.append(
                SessionChatMessage(
                    position=int(row["position"]),
                    permission=permission,
                    chat_message=ChatMessage.from_json(json.dumps(chat_message)),
                )
            )

        return session_chat_messages

    def load_activities(self) -> list[SessionActivity]:
        activities_list: list[SessionActivity] = []
        for activity in self.load_session_activities():
            activities_list.append(activity.activity)

        return activities_list

    def load_session_activities(self) -> list[SessionActivityRecord]:
        statement = select(activities).order_by(activities.c.position)

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
                )
            )

        return session_activities
