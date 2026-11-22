import serve
from config import Settings


def test_main_passes_listener_settings_to_uvicorn(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        serve.uvicorn, "run", lambda app, **kwargs: captured.update(app=app, **kwargs)
    )
    serve.main(Settings(host="127.0.0.1", port=9001, keepalive_timeout_s=7))
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001
    assert captured["timeout_keep_alive"] == 7
    assert captured["app"].title == "stub-chat-api"


def test_main_reads_settings_from_env_by_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setenv("STUB_PORT", "9002")
    monkeypatch.setenv("STUB_KEEPALIVE_TIMEOUT_S", "9")
    serve.main()
    assert captured["port"] == 9002
    assert captured["timeout_keep_alive"] == 9
