from app import billing, security
from app.agent.runner import close_dangling_tool_calls, compact_history
from app.agent.tools import scrub, truncate


def test_encrypt_roundtrip():
    enc = security.encrypt("secret-token")
    assert enc != "secret-token"
    assert security.decrypt(enc) == "secret-token"
    assert security.decrypt("garbage") is None


def test_session_cookie():
    assert security.read_session(security.sign_session(42)) == 42
    assert security.read_session("tampered") is None


def test_price_lookup_and_cost():
    pb = billing.PriceBook()
    pb.set_prices({"anthropic/claude-sonnet-5": billing.ModelPrice(0.0004, 0.002, 0.00004)})
    # имя модели сопоставляется без провайдера и пунктуации
    assert pb.get("claude-sonnet-5").input == 0.0004
    assert pb.get("anthropic/claude-sonnet-5").output == 0.002
    cost = pb.cost_micro("anthropic/claude-sonnet-5", 1000, 100, cached_tokens=500)
    markup = billing.get_settings().markup
    expected = (500 * 0.0004 + 500 * 0.00004 + 100 * 0.002) * markup
    assert cost == billing.rub_to_micro(expected)
    assert pb.cost_micro("x", 0, 0) == 0


def test_scrub_and_truncate():
    assert scrub("token=abc123secret", ["abc123secret"]) == "token=***"
    out = truncate("x" * 1000, 100)
    assert len(out) < 200 and "обрезано" in out


def test_compact_history_shrinks_old_tool_results():
    hist = [{"role": "tool", "tool_call_id": str(i), "content": "y" * 10_000} for i in range(20)]
    compact_history(hist, budget=50_000)
    assert sum(len(m["content"]) for m in hist) <= 90_000
    assert len(hist[-1]["content"]) == 10_000  # свежие не трогаем


def test_close_dangling_tool_calls():
    hist = [{"role": "assistant", "content": None, "tool_calls": [{"id": "a"}, {"id": "b"}]},
            {"role": "tool", "tool_call_id": "a", "content": "ok"}]
    close_dangling_tool_calls(hist)
    assert [m.get("tool_call_id") for m in hist if m["role"] == "tool"] == ["a", "b"]
