from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts import spike_langfuse_delete as spike
from tests.stubs.langfuse_stub import DEMO_PUBLIC_KEY, DEMO_SECRET_KEY, LangfuseStub

ADR = Path(__file__).resolve().parents[2] / "docs" / "adr" / "langfuse-delete-behaviour.md"

# Short poll settings: the virtual clock makes them cost nothing, but fewer polls keep it quick.
FAST = spike.ProbeSettings(timeout_s=5.0, poll_interval_s=10.0, poll_timeout_s=200.0)


def client_for(stub: LangfuseStub, timeout_s: float = 5.0) -> spike.LangfuseClient:
    return spike.LangfuseClient(stub.url, DEMO_PUBLIC_KEY, DEMO_SECRET_KEY, timeout_s)


def probe(
    mode: str, delay_s: float = 0.0, error_count: int = 1
) -> tuple[list[spike.VariantResult], LangfuseStub]:
    vclock = spike.VirtualClock()
    with LangfuseStub(
        mode, delete_delay_s=delay_s, error_count=error_count, clock=vclock.now, environ={}
    ) as stub:
        results = spike.run_probe(client_for(stub), FAST, "r1", vclock.now, vclock.sleep)
    return results, stub


def test_settings_defaults() -> None:
    s = spike.ProbeSettings.from_env({})
    assert (s.timeout_s, s.poll_interval_s, s.poll_timeout_s) == (
        spike.DEFAULT_TIMEOUT_S,
        spike.DEFAULT_POLL_INTERVAL_S,
        spike.DEFAULT_POLL_TIMEOUT_S,
    )


def test_settings_env_override() -> None:
    s = spike.ProbeSettings.from_env(
        {
            spike.ENV_TIMEOUT_S: "2.5",
            spike.ENV_POLL_INTERVAL_S: "1",
            spike.ENV_POLL_TIMEOUT_S: "30",
        }
    )
    assert (s.timeout_s, s.poll_interval_s, s.poll_timeout_s) == (2.5, 1.0, 30.0)


@pytest.mark.parametrize("value", ["fast", "0", "-3"])
def test_settings_bad_value_fails_fast(value: str) -> None:
    with pytest.raises(spike.SpikeError, match=spike.ENV_TIMEOUT_S):
        spike.ProbeSettings.from_env({spike.ENV_TIMEOUT_S: value})


def test_virtual_clock_sleep_advances_now() -> None:
    clock = spike.VirtualClock()
    start = clock.now()
    clock.sleep(15.0)
    assert clock.now() - start == 15.0


def test_client_single_batch_and_404_against_the_stub() -> None:
    with LangfuseStub("honour", environ={}) as stub:
        client = client_for(stub)
        assert client.ingest_trace("fa-spike-x").status == 207
        assert client.get_trace("fa-spike-x").status == 200
        assert client.delete_trace("fa-spike-x").status == 202
        assert client.get_trace("fa-spike-x").status == 404
        assert client.delete_trace("fa-spike-x").status == 404
        assert client.get_trace("fa-spike-x").body == {"message": "Trace fa-spike-x not found"}
        stub.add_trace("a")
        stub.add_trace("b")
        resp = client.delete_traces(["a", "b"])
        assert resp.status == 202
        assert resp.body == {"message": "Traces deleted successfully"}
        client.close()


def test_client_non_json_body_is_kept_as_text() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(502, text="bad gateway"))
    client = spike.LangfuseClient("http://x.invalid", "p", "s", 1.0, transport=transport)
    resp = client.get_trace("t")
    assert (resp.status, resp.body) == (502, "bad gateway")


def test_client_timeout_is_a_spike_error() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client = spike.LangfuseClient(
        "http://x.invalid", "p", "s", 0.5, transport=httpx.MockTransport(slow)
    )
    with pytest.raises(spike.SpikeError, match="timed out"):
        client.get_trace("t")


def test_client_applies_the_timeout_knob() -> None:
    client = spike.LangfuseClient("http://x.invalid", "p", "s", 3.5)
    assert client.timeout_s == 3.5
    client.close()


def test_poll_returns_elapsed_seconds_when_the_status_arrives() -> None:
    vclock = spike.VirtualClock()
    with LangfuseStub("honour", delete_delay_s=35.0, clock=vclock.now, environ={}) as stub:
        stub.add_trace("t1")
        client = client_for(stub)
        client.delete_trace("t1")
        seconds = spike.poll_for_status(client, "t1", 404, FAST, vclock.now, vclock.sleep)
        assert seconds == 40.0


def test_poll_returns_none_at_the_timeout() -> None:
    vclock = spike.VirtualClock()
    with LangfuseStub("ignore", clock=vclock.now, environ={}) as stub:
        stub.add_trace("t1")
        client = client_for(stub)
        client.delete_trace("t1")
        assert spike.poll_for_status(client, "t1", 404, FAST, vclock.now, vclock.sleep) is None
        assert vclock.now() >= FAST.poll_timeout_s


def test_probe_honour_measures_the_delay() -> None:
    results, _ = probe("honour", delay_s=90.0)
    assert [r.label for r in results] == ["single", "batch"]
    for r in results:
        assert r.delete_status == 202
        assert r.get_after_delete_status == 200
        assert r.seconds_to_gone == 90.0
        assert r.repeat_delete_status == 404
        assert spike.classify(r, verified=False).verdict == spike.HONOURED


def test_probe_ignore_never_disappears() -> None:
    results, _ = probe("ignore")
    for r in results:
        assert r.delete_status == 202
        assert r.seconds_to_gone is None
        assert r.repeat_delete_status is None
        verdict = spike.classify(r, verified=False)
        assert verdict.verdict == spike.IGNORED
        assert "200" in verdict.detail


def test_probe_error_first_delete_500_then_batch_202() -> None:
    results, _ = probe("error")
    single, batch = results
    assert single.delete_status == 500
    assert single.seconds_to_gone is None
    assert spike.classify(single, verified=True).verdict == spike.REJECTED
    assert batch.delete_status == 202
    assert batch.seconds_to_gone == 0.0


def test_probe_fails_when_ingestion_is_refused() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={}))
    client = spike.LangfuseClient("http://x.invalid", "p", "s", 1.0, transport=transport)
    vclock = spike.VirtualClock()
    with pytest.raises(spike.SpikeError, match="ingestion"):
        spike.run_probe(client, FAST, "r1", vclock.now, vclock.sleep)


def test_probe_fails_when_the_trace_never_shows_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(207, json={"successes": [], "errors": []})
        return httpx.Response(404, json={})

    client = spike.LangfuseClient(
        "http://x.invalid", "p", "s", 1.0, transport=httpx.MockTransport(handler)
    )
    vclock = spike.VirtualClock()
    with pytest.raises(spike.SpikeError, match="never became visible"):
        spike.run_probe(client, FAST, "r1", vclock.now, vclock.sleep)


def test_verdict_line_marks_demo_as_unverified() -> None:
    results, _ = probe("honour")
    line = spike.classify(results[0], verified=False).line()
    assert spike.UNVERIFIED in line
    assert "not a measurement" in line
    assert spike.UNVERIFIED not in spike.classify(results[0], verified=True).line()


def test_live_commands_name_throwaway_traces_and_the_documented_shapes() -> None:
    commands = spike.live_commands("r1")
    text = "\n".join(commands)
    assert "fa-spike-single-r1" in text
    assert "fa-spike-batch-r1" in text
    assert '{"traceIds": ["fa-spike-batch-r1"]}' in text
    assert "/api/public/ingestion" in text
    assert "-X DELETE" in text
    assert "$LANGFUSE_SECRET_KEY" in text


def test_main_demo_prints_three_modes_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code = spike.main(
        [],
        environ={spike.ENV_POLL_TIMEOUT_S: "60", spike.ENV_POLL_INTERVAL_S: "10"},
    )
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out[: out.index("\nVERDICT")])
    assert report["demo"] is True
    assert set(report["modes"]) == {"honour", "ignore", "error"}
    assert out.count("VERDICT[") == 6
    assert "VERDICT[error/single]: delete rejected" in out
    assert spike.UNVERIFIED in out


def test_main_live_commands(capsys: pytest.CaptureFixture[str]) -> None:
    code = spike.main(["--mode", "live", "--action", "commands", "--run-id", "r9"], environ={})
    assert code == 0
    assert "fa-spike-single-r9" in capsys.readouterr().out


def test_main_live_run_against_the_stub_writes_the_result(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out_file = tmp_path / "run.json"
    with LangfuseStub("honour", environ={}) as stub:
        env = {
            "LANGFUSE_HOST": stub.url,
            "LANGFUSE_PUBLIC_KEY": DEMO_PUBLIC_KEY,
            "LANGFUSE_SECRET_KEY": DEMO_SECRET_KEY,
        }
        code = spike.main(
            ["--mode", "live", "--action", "run", "--run-id", "r2", "--out", str(out_file)],
            environ=env,
        )
    out = capsys.readouterr().out
    assert code == 0
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["demo"] is False
    assert saved["variants"][0]["trace_id"] == "fa-spike-single-r2"
    assert saved["variants"][1]["delete_status"] == 202
    assert "VERDICT[batch]" in out
    assert spike.UNVERIFIED not in out


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"LANGFUSE_HOST": "http://x.invalid", "LANGFUSE_PUBLIC_KEY": "REPLACE_ME"},
        {
            "LANGFUSE_HOST": "http://x.invalid",
            "LANGFUSE_PUBLIC_KEY": "pk",
            "LANGFUSE_SECRET_KEY": "REPLACE_ME",
        },
        {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"},
    ],
)
def test_main_live_run_without_real_settings_fails(env: dict[str, str]) -> None:
    assert spike.main(["--mode", "live", "--action", "run"], environ=env) == 1


def test_main_live_run_surfaces_a_failed_probe() -> None:
    with LangfuseStub("honour", environ={}) as stub:
        env = {
            "LANGFUSE_HOST": stub.url,
            "LANGFUSE_PUBLIC_KEY": DEMO_PUBLIC_KEY,
            "LANGFUSE_SECRET_KEY": "wrong-secret",
        }
        assert spike.main(["--mode", "live", "--action", "run"], environ=env) == 1


def test_main_rejects_a_bad_knob() -> None:
    assert spike.main([], environ={spike.ENV_POLL_TIMEOUT_S: "-1"}) == 1


def test_run_id_must_be_safe_for_a_trace_id() -> None:
    assert spike.main(["--mode", "live", "--action", "commands", "--run-id", "a b"], {}) == 1


# AC3 cannot be met without Langfuse keys (K2): the document must say so and carry the procedure.
def test_adr_is_unverified_and_carries_the_live_procedure() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "Status: **UNVERIFIED-pending-K2.**" in text
    assert "traceIds" in text
    assert "python -m scripts.spike_langfuse_delete --mode live" in text
    assert "/api/public/ingestion" in text
    for command in spike.live_commands("<RUN>"):
        if not command.startswith("#"):
            assert command.split(" ")[0] == "curl"
