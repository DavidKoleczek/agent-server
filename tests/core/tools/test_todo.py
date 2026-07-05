from agent_server.core.tools.todo import TodoItem, TodoTool


def test_todos_property_initially_empty() -> None:
    tool = TodoTool()
    assert tool.todos == []


def test_execute_creates_todos() -> None:
    tool = TodoTool()

    result = tool.execute(
        todos=[
            {"content": "First task", "status": "pending", "active_form": "Starting first task"},
            {"content": "Second task", "status": "in_progress", "active_form": "Working on second task"},
        ]
    )

    assert "Todos have been modified successfully" in result
    assert len(tool.todos) == 2
    assert tool.todos[0].content == "First task"
    assert tool.todos[0].status == "pending"
    assert tool.todos[0].active_form == "Starting first task"
    assert tool.todos[1].content == "Second task"
    assert tool.todos[1].status == "in_progress"


def test_execute_updates_todos() -> None:
    tool = TodoTool()

    tool.execute(todos=[{"content": "Old task", "status": "pending", "active_form": "Old form"}])
    assert len(tool.todos) == 1

    tool.execute(
        todos=[
            {"content": "New task 1", "status": "pending", "active_form": "New form 1"},
            {"content": "New task 2", "status": "completed", "active_form": "New form 2"},
        ]
    )

    assert len(tool.todos) == 2
    assert tool.todos[0].content == "New task 1"
    assert tool.todos[1].content == "New task 2"


def test_execute_empty_list() -> None:
    tool = TodoTool()

    tool.execute(todos=[{"content": "Task", "status": "pending", "active_form": "Form"}])
    assert len(tool.todos) == 1

    result = tool.execute(todos=[])

    assert "Todos have been modified successfully" in result
    assert tool.todos == []


def test_todos_property_returns_copy() -> None:
    tool = TodoTool()
    tool.execute(todos=[{"content": "Task", "status": "pending", "active_form": "Form"}])

    todos_copy = tool.todos
    todos_copy.append(TodoItem(content="Extra", status="pending", active_form="Extra form"))

    assert len(tool.todos) == 1


def test_execute_with_all_statuses() -> None:
    tool = TodoTool()

    tool.execute(
        todos=[
            {"content": "Pending task", "status": "pending", "active_form": "Pending form"},
            {"content": "In progress task", "status": "in_progress", "active_form": "In progress form"},
            {"content": "Completed task", "status": "completed", "active_form": "Completed form"},
        ]
    )

    assert tool.todos[0].status == "pending"
    assert tool.todos[1].status == "in_progress"
    assert tool.todos[2].status == "completed"


def test_execute_invalid_todo_returns_error() -> None:
    tool = TodoTool()

    result = tool.execute(todos=[{"content": "Task", "status": "invalid_status", "active_form": "Form"}])

    assert "Error: Failed to parse todos" in result
    assert tool.todos == []


def test_execute_missing_field_returns_error() -> None:
    tool = TodoTool()

    result = tool.execute(todos=[{"content": "Task", "status": "pending"}])

    assert "Error: Failed to parse todos" in result
    assert tool.todos == []
