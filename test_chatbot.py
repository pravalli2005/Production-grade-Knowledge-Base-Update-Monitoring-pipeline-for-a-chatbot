"""Tests for general-answer fallback when the knowledge base has no match."""
from types import SimpleNamespace

from pipeline.chatbot import ChatbotEngine
from pipeline.versioning import VersionManager


def test_unmatched_question_uses_openai(monkeypatch, temp_workspace):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "choices": [{"message": {"content": "Mars has two moons: Phobos and Deimos."}}]
            },
        )

    monkeypatch.setattr("pipeline.chatbot.requests.post", fake_post)
    engine = ChatbotEngine(VersionManager(versions_dir=temp_workspace["versions"]))

    response = engine.ask("How many moons does Mars have?")

    assert response.answer == "Mars has two moons: Phobos and Deimos."
    assert response.answer_source == "openai"
    assert not response.escalated
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "gpt-4o-mini"
    assert captured["payload"]["messages"][-1]["content"] == "How many moons does Mars have?"
    assert captured["timeout"] == 30


def test_unmatched_question_explains_missing_openai_key(monkeypatch, temp_workspace):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    engine = ChatbotEngine(VersionManager(versions_dir=temp_workspace["versions"]))

    response = engine.ask("How many moons does Mars have?")

    assert response.escalated
    assert response.escalation_reason == "AI_PROVIDER_NOT_CONFIGURED"
    assert "OPENAI_API_KEY" in response.answer
    assert response.answer_source == "error"


def test_personal_data_is_masked_before_openai_request(monkeypatch, temp_workspace):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}

    def fake_post(_url, *, json, **_kwargs):
        captured["messages"] = json["messages"]
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": "Please keep your information safe."}}]},
        )

    monkeypatch.setattr("pipeline.chatbot.requests.post", fake_post)
    engine = ChatbotEngine(VersionManager(versions_dir=temp_workspace["versions"]))

    response = engine.ask("My email is user@example.com. How many moons does Mars have?")

    serialized_messages = str(captured["messages"])
    assert "user@example.com" not in serialized_messages
    assert "[REDACTED_EMAIL]" in serialized_messages
    assert response.pii_masked["EMAIL"] == 1
