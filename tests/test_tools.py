"""Basic unit tests covering schema validation, tool registry integrity, and
core task/note tool behavior against a throwaway SQLite DB.

Run with: pytest tests/ -v
(These tests don't require an LLM API key — no LLM-powered tools are
exercised here, since that would need a real Hugging Face/Gemini call.)
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_productivity_agent.db")

import pytest
from pydantic import ValidationError

from app.database.db import init_db
from app.models.schemas import (
    BatchExtractionResult,
    CompleteTaskArgs,
    CreateReminderArgs,
    CreateTaskArgs,
    DeleteTaskArgs,
    ListTasksArgs,
    SaveNoteArgs,
    SearchNotesArgs,
)
from app.tools import note_tools, task_tools
from app.tools.registry import TOOL_REGISTRY, get_tool_spec
from app.tools.workflow_tools import partition_batch_actions


@pytest.fixture(autouse=True, scope="module")
def _setup_db():
    init_db()
    yield


def test_registry_has_all_required_tools():
    required = {
        "create_task", "list_tasks", "update_task", "complete_task",
        "save_note", "search_notes", "extract_meeting_actions",
        "generate_work_plan", "detect_overdue_tasks", "draft_followup_email",
    }
    assert required <= set(TOOL_REGISTRY.keys())


def test_approval_required_tools_flagged():
    assert get_tool_spec("update_task").requires_approval is True
    assert get_tool_spec("complete_task").requires_approval is True
    assert get_tool_spec("create_task").requires_approval is False
    assert get_tool_spec("list_tasks").requires_approval is False


def test_create_task_args_rejects_bad_due_date():
    with pytest.raises(ValidationError):
        CreateTaskArgs(title="x", due_date="not-a-date")


def test_create_task_args_accepts_valid_input():
    args = CreateTaskArgs(title="Write report", priority="high", due_date="2026-08-01T00:00:00")
    assert args.title == "Write report"
    assert args.priority.value == "high"


def test_create_and_list_task_roundtrip():
    result = task_tools.create_task(CreateTaskArgs(title="Integration test task", priority="medium"))
    assert result["success"] is True
    task_id = result["task"]["id"]

    listed = task_tools.list_tasks(ListTasksArgs(limit=100))
    assert listed["success"] is True
    assert any(t["id"] == task_id for t in listed["tasks"])


def test_complete_task_by_id():
    created = task_tools.create_task(CreateTaskArgs(title="Complete-me"))
    task_id = created["task"]["id"]
    result = task_tools.complete_task(CompleteTaskArgs(task_id=task_id))
    assert result["success"] is True
    assert result["task"]["status"] == "completed"


def test_complete_task_unknown_id_fails_gracefully():
    result = task_tools.complete_task(CompleteTaskArgs(task_id="00000000-0000-0000-0000-000000000000"))
    assert result["success"] is False
    assert "error" in result


def test_save_and_search_notes():
    save_result = note_tools.save_note(SaveNoteArgs(title="Kickoff", content="Discussed the roadmap and milestones"))
    assert save_result["success"] is True

    search_result = note_tools.search_notes(SearchNotesArgs(query="roadmap"))
    assert search_result["success"] is True
    assert search_result["count"] >= 1


def test_delete_task_by_id():
    created = task_tools.create_task(CreateTaskArgs(title="Delete-me"))
    task_id = created["task"]["id"]
    result = task_tools.delete_task(DeleteTaskArgs(task_id=task_id))
    assert result["success"] is True

    listed = task_tools.list_tasks(ListTasksArgs(limit=200))
    assert not any(t["id"] == task_id for t in listed["tasks"])


def test_delete_task_unknown_reference_fails_gracefully():
    result = task_tools.delete_task(DeleteTaskArgs(task_reference="a task that does not exist anywhere"))
    assert result["success"] is False


def test_resolve_task_by_title_fallback():
    task_tools.create_task(CreateTaskArgs(title="Build the FastAPI backend for the project"))
    result = task_tools.complete_task(CompleteTaskArgs(task_reference="FastAPI backend"))
    assert result["success"] is True
    assert "fastapi" in result["task"]["title"].lower()


def test_create_reminder():
    result = task_tools.create_reminder(CreateReminderArgs(title="Follow up with client"))
    assert result["success"] is True
    assert result["reminder"]["tags"] == ["reminder"]


def test_create_task_with_assignee():
    result = task_tools.create_task(CreateTaskArgs(title="Design the dashboard UI", assignee="Ali"))
    assert result["success"] is True
    assert result["task"]["assignee"] == "Ali"


def test_registry_includes_new_tools_with_correct_approval_flags():
    assert "delete_task" in TOOL_REGISTRY
    assert "create_reminder" in TOOL_REGISTRY
    assert get_tool_spec("delete_task").requires_approval is True
    assert get_tool_spec("create_reminder").requires_approval is True
    assert get_tool_spec("extract_meeting_actions").requires_approval is False


def test_partition_batch_actions_single_task_is_auto_executed():
    batch = BatchExtractionResult(tasks=[{"title": "Solo task"}])
    auto_actions, approval_actions = partition_batch_actions(batch)
    assert len(auto_actions) == 1
    assert auto_actions[0]["tool_name"] == "create_task"
    assert approval_actions == []


def test_partition_batch_actions_multiple_tasks_require_approval():
    batch = BatchExtractionResult(
        tasks=[{"title": "Task A"}, {"title": "Task B"}],
    )
    auto_actions, approval_actions = partition_batch_actions(batch)
    assert auto_actions == []
    assert len(approval_actions) == 1
    assert approval_actions[0]["tool_name"] == "create_multiple_tasks"
    assert len(approval_actions[0]["arguments"]["tasks"]) == 2


def test_partition_batch_actions_notes_always_auto_and_mixed_categories():
    batch = BatchExtractionResult(
        tasks=[{"title": "Only task"}],
        notes=[{"content": "Use FastAPI for the backend."}, {"title": "DB choice", "content": "Use ChromaDB."}],
        updates=[{"task_reference": "onboarding doc", "status": "in_progress"}],
        completions=[{"task_reference": "kickoff call"}],
        deletions=[{"task_reference": "stale task"}],
        reminders=[{"title": "Ping client"}],
    )
    auto_actions, approval_actions = partition_batch_actions(batch)
    auto_tools = [a["tool_name"] for a in auto_actions]
    approval_tools = [a["tool_name"] for a in approval_actions]
    assert auto_tools.count("save_note") == 2
    assert auto_tools.count("create_task") == 1
    assert approval_tools == ["update_task", "complete_task", "delete_task", "create_reminder"]


def test_detect_overdue_tasks_excludes_completed():
    from datetime import datetime, timedelta

    args = CreateTaskArgs(
        title="Overdue task",
        due_date=(datetime.utcnow() - timedelta(days=2)).isoformat(),
    )
    created = task_tools.create_task(args)
    assert created["success"] is True

    overdue = task_tools.detect_overdue_tasks()
    assert overdue["success"] is True
    assert any(t["id"] == created["task"]["id"] for t in overdue["overdue_tasks"])


def test_llm_settings_defaults_to_configured_provider():
    from app.agent import llm_settings

    state = llm_settings.get_state()
    assert state["provider"] in ("huggingface", "gemini")
    assert state["active_hf_model"]  # HF_MODEL_1 default is non-empty


def test_llm_settings_switch_provider_and_back():
    from app.agent import llm_settings

    original = llm_settings.get_state()["provider"]
    llm_settings.set_provider("gemini")
    assert llm_settings.get_state()["provider"] == "gemini"
    llm_settings.set_provider("huggingface")
    assert llm_settings.get_state()["provider"] == "huggingface"
    llm_settings.set_provider(original)


def test_llm_settings_rejects_unknown_provider():
    from app.agent import llm_settings

    with pytest.raises(ValueError):
        llm_settings.set_provider("openai")


def test_llm_settings_switch_active_hf_model():
    from app.agent import llm_settings

    available = llm_settings.get_available_hf_models()
    assert len(available) >= 1
    original = llm_settings.get_state()["active_hf_model"]
    llm_settings.set_active_hf_model(available[-1])
    assert llm_settings.get_state()["active_hf_model"] == available[-1]
    llm_settings.set_active_hf_model(original)


def test_llm_settings_rejects_unconfigured_hf_model():
    from app.agent import llm_settings

    with pytest.raises(ValueError):
        llm_settings.set_active_hf_model("not/a-configured-model")


def test_get_llm_client_raises_clear_error_when_key_missing():
    from app.agent import llm_service, llm_settings
    from app.config import get_settings

    # No .env file is loaded in the test environment, so GEMINI_API_KEY
    # defaults to empty — get_llm_client() should refuse clearly rather
    # than silently proceeding.
    assert get_settings().GEMINI_API_KEY == ""
    llm_settings.set_provider("gemini")
    try:
        with pytest.raises(llm_service.LLMError, match="GEMINI_API_KEY"):
            llm_service.get_llm_client()
    finally:
        llm_settings.set_provider("huggingface")
