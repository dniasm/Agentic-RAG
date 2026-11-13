"""The 20B takes over at once when the 120B has no room, instead of waiting out Groq's retry."""
import groq
import httpx
import pytest
from langchain_core.messages import AIMessage

from renamer import agent, batch, config, limits
from tests.test_undo_and_batch import Clock


class Fake:
    """A Groq model: answers with `tokens` used under its own name, or raises `error`."""

    def __init__(self, name, tokens=1500, error=None):
        self.name, self.tokens, self.error, self.calls = name, tokens, error, 0

    def invoke(self, messages):
        self.calls += 1
        if self.error:
            raise self.error
        return AIMessage("hello", usage_metadata={"input_tokens": self.tokens - 50, "output_tokens": 50,
                                                  "total_tokens": self.tokens},
                         response_metadata={"model_name": self.name})


def rate_limited():
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    return groq.RateLimitError("Rate limit reached", response=response, body=None)


@pytest.fixture
def models(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(limits, "WINDOW", limits.TokenWindow(8000, 60.0, clock=clock))
    monkeypatch.setattr(limits, "BACKUP_WINDOW", limits.TokenWindow(8000, 60.0, clock=clock))
    made = {}

    def llm(name, retries):
        made[name] = made.get(name) or Fake(name)
        made[name].retries = retries
        return made[name]
    monkeypatch.setattr(agent, "_llm", llm)
    return made, clock


def test_the_main_model_is_used_while_there_is_room(models):
    made, _ = models
    model = agent.GroqAgentModel()
    model.invoke([])
    assert made[config.AGENT_MODEL].calls == 1 and config.BACKUP_MODEL not in made
    assert made[config.AGENT_MODEL].retries == 0            # a 429 must not sleep out Groq's Retry-After


def test_no_room_goes_straight_to_the_backup(models):
    made, _ = models
    limits.WINDOW.record(7000)                              # 1,000 left, a call is about 2,000
    model = agent.GroqAgentModel()
    model.invoke([])
    assert config.AGENT_MODEL not in made and made[config.BACKUP_MODEL].calls == 1


def test_a_rate_limit_switches_at_once(models):
    made, _ = models
    made[config.AGENT_MODEL] = Fake(config.AGENT_MODEL, error=rate_limited())
    reply = agent.GroqAgentModel().invoke([])
    assert reply.response_metadata["model_name"] == config.BACKUP_MODEL


def test_the_backups_tokens_count_against_its_own_limit(models, library_docs):
    _, clock = models
    limits.WINDOW.record(7000)
    out = agent.chat("fb1", "hi", model=agent.GroqAgentModel())
    assert out["reply"] == "hello"
    assert limits.WINDOW.used() == 7000 and limits.BACKUP_WINDOW.used() == 1500
    assert batch.status_light()["backup_used"] == 1500
    clock.t += 61
    assert agent.GroqAgentModel().invoke([]).response_metadata["model_name"] == config.AGENT_MODEL   # room again


class Empty(Fake):
    def invoke(self, messages):
        self.calls += 1
        return AIMessage("", usage_metadata={"input_tokens": 900, "output_tokens": 0, "total_tokens": 900},
                         response_metadata={"model_name": self.name})


def test_an_empty_answer_from_the_backup_is_asked_again_of_the_main_model(models, library_docs):
    made, clock = models
    made[config.BACKUP_MODEL] = Empty(config.BACKUP_MODEL)
    limits.WINDOW.record(7000)                              # the 120B is full: the 20B answers, with nothing
    model = agent.GroqAgentModel()
    slept = []
    model.sleep = lambda s: (slept.append(s), setattr(clock, "t", clock.t + s))
    out = agent.chat("fb2", "hi", model=model)
    assert out["reply"] == "hello"                          # not "Sorry, I have no answer"
    assert made[config.BACKUP_MODEL].calls == 1 and made[config.AGENT_MODEL].calls == 1
    assert 55 < slept[0] <= config.RETRY_WAIT_SECONDS       # it waited for the 120B's minute to clear
    assert out["tokens"] == 900 + 1500


class Script(Fake):
    """A Groq model that plays back a list of replies (tool calls or text)."""

    def __init__(self, name, replies):
        super().__init__(name)
        self.replies = list(replies)

    def invoke(self, messages):
        self.calls += 1
        calls, text = self.replies.pop(0)
        return AIMessage(text, tool_calls=[{"name": n, "args": a, "id": f"s{self.calls}{n}"} for n, a in calls],
                         usage_metadata={"input_tokens": 1000, "output_tokens": 0, "total_tokens": 1000},
                         response_metadata={"model_name": self.name})


def test_the_backup_stopping_early_with_a_false_claim_is_retried_on_the_main_model(models, library_docs, monkeypatch):
    """Seen live: the 20B said 'All five changes have been reverted' with one still to do."""
    made, clock = models
    svm, glove = library_docs["svm.txt"]["id"], library_docs["glove.txt"]["id"]
    from tests.test_agent import turn
    turn("fb3", [("find_documents", {"description": "svm.txt"})])
    turn("fb3", [("find_documents", {"description": "glove.txt"})])
    made[config.BACKUP_MODEL] = Script(config.BACKUP_MODEL, [
        ([("rename_document", {"doc_id": svm, "new_title": "SVM Primer"})], ""),
        ([], "Both documents have been renamed.")])                      # not true: GloVe isn't done
    made[config.AGENT_MODEL] = Script(config.AGENT_MODEL, [
        ([("rename_document", {"doc_id": glove, "new_title": "GloVe Primer"})], "")])
    answers = iter(["MORE", "DONE"])
    monkeypatch.setattr(agent, "_checker", lambda: type("C", (), {"invoke": lambda self, p: AIMessage(next(answers))})())
    limits.WINDOW.record(7000)                                           # the 120B is full: the 20B starts
    model = agent.GroqAgentModel()
    model.sleep = lambda s: setattr(clock, "t", clock.t + s)
    out = agent.chat("fb3", "rename both to Primer titles", model=model)
    assert [a["new_title"] for a in out["actions"]] == ["SVM Primer", "GloVe Primer"]
    assert "GloVe Primer" in out["reply"] and "Both documents have been renamed" not in out["reply"]
