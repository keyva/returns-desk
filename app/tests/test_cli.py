"""The command line. Canned engines are injected; nothing calls Azure."""
from __future__ import annotations

import io
import json
import shutil

import pytest

from conftest import DATA, CannedGenerativeEngine, CannedProbabilisticEngine
from returns_desk import cli
from returns_desk.log import ResolutionLog
from returns_desk.types import Disposition as D, Engines, Resolver, ResponsibleParty as P

TODAY = "2026-10-30"


def _data_dir(tmp_path, keep: set[str]):
    d = tmp_path / "data"
    shutil.copytree(DATA, d)
    rows = json.loads((d / "sample_set.json").read_text())
    (d / "sample_set.json").write_text(json.dumps([r for r in rows if r["request_id"] in keep]))
    return d


def _engines():
    return Engines(CannedGenerativeEngine(), CannedProbabilisticEngine())


def _stdin(monkeypatch, *lines):
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(l + "\n" for l in lines)))


def _args(tmp_path, d, cmd):
    return [cmd, "--today", TODAY, "--data", str(d), "--log", str(tmp_path / "log.jsonl")]


def test_run_resolves_a_pending_request_from_scripted_input(tmp_path, monkeypatch, capsys):
    d = _data_dir(tmp_path, {"RQ-305"})
    _stdin(monkeypatch, "retailer", "refund", "motor defect, refund")
    assert cli.main(_args(tmp_path, d, "run"), engines=_engines()) == 0
    out = capsys.readouterr().out
    assert "RQ-305" in out and "retailer" in out and "customer" in out      # both cards
    resolved = ResolutionLog(tmp_path / "log.jsonl").by_request()["RQ-305"]
    assert (resolved.resolver, resolved.responsible_party, resolved.disposition) == (Resolver.CLERK, P.RETAILER, D.REFUND)
    assert resolved.amount == 89.0 and resolved.note == "motor defect, refund"


def test_run_shows_the_validation_error_and_asks_again(tmp_path, monkeypatch, capsys):
    d = _data_dir(tmp_path, {"RQ-305"})
    # a partial of 500 on an 89.00 line is rejected by the validator; the clerk is asked again
    _stdin(monkeypatch, "customer", "partial", "keeping the customer", "500",
           "customer", "partial", "keeping the customer", "30")
    assert cli.main(_args(tmp_path, d, "run"), engines=_engines()) == 0
    out = capsys.readouterr().out
    assert "partial_range" in out and "rejected" in out
    r = ResolutionLog(tmp_path / "log.jsonl").by_request()["RQ-305"]
    assert (r.disposition, r.amount) == (D.PARTIAL, 30.0)


def test_run_reprompts_on_an_unknown_answer_and_takes_a_partial_amount(tmp_path, monkeypatch):
    d = _data_dir(tmp_path, {"RQ-305"})
    _stdin(monkeypatch, "banana", "customer", "refundish", "partial", "keeping the customer", "40")
    assert cli.main(_args(tmp_path, d, "run"), engines=_engines()) == 0
    r = ResolutionLog(tmp_path / "log.jsonl").by_request()["RQ-305"]
    assert (r.responsible_party, r.disposition, r.amount) == (P.CUSTOMER, D.PARTIAL, 40.0)


def test_run_prints_policy_denials_and_auto_approvals_without_prompting(tmp_path, monkeypatch, capsys):
    d = _data_dir(tmp_path, {"RQ-303", "RQ-301", "RQ-302"})
    _stdin(monkeypatch)                                      # no input at all
    eng = _engines()
    assert cli.main(_args(tmp_path, d, "run"), engines=eng) == 0
    out = capsys.readouterr().out
    log = ResolutionLog(tmp_path / "log.jsonl").by_request()
    assert log["RQ-303"].resolver == Resolver.POLICY and log["RQ-303"].disposition == D.DENY
    assert log["RQ-301"].resolver == Resolver.ENGINES
    assert log["RQ-302"].resolver == Resolver.POLICY and log["RQ-302"].disposition == D.REFUND
    assert "RQ-303" in out and "[DENY]" in out and "RQ-301" in out and "[AUTO]" in out
    assert eng.reading.asked == ["RQ-301"]


def test_run_stops_cleanly_when_input_runs_out(tmp_path, monkeypatch):
    d = _data_dir(tmp_path, {"RQ-305"})
    _stdin(monkeypatch)
    assert cli.main(_args(tmp_path, d, "run"), engines=_engines()) == 1
    assert ResolutionLog(tmp_path / "log.jsonl").all() == []


def test_evaluate_writes_the_json_file(tmp_path, monkeypatch, capsys):
    d = _data_dir(tmp_path, {"RQ-301", "RQ-303", "RQ-305", "RQ-306", "RQ-307"})
    assert cli.main(_args(tmp_path, d, "evaluate"), engines=_engines()) == 0
    saved = json.loads((d / "evaluation.json").read_text())
    assert saved["labeled_requests"] == 5 and saved["model"] == "canned-reading"
    assert [r["engine"] for r in saved["reports"]] == ["canned-reading", "canned-scoring"]
    assert saved["reports"][0]["accuracy"] == 1.0
    out = capsys.readouterr().out
    assert "canned-reading" in out and "100" in out


def test_evaluate_includes_clerk_labels_from_the_log(tmp_path):
    d = _data_dir(tmp_path, {"RQ-301", "RQ-305"})
    from returns_desk.types import Resolution
    ResolutionLog(tmp_path / "log.jsonl").append(
        Resolution("RQ-301", Resolver.CLERK, P.RETAILER, D.REFUND, 64.0, 0.0, "retailer", note="n"))
    assert cli.main(_args(tmp_path, d, "evaluate"), engines=_engines()) == 0
    saved = json.loads((d / "evaluation.json").read_text())
    assert (saved["from_sample_set"], saved["from_resolution_log"]) == (1, 1)
    assert saved["reports"][0]["accuracy"] == 0.5


def test_reset_truncates_the_log(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    log.write_text('{"x": 1}\n')
    assert cli.main(["reset", "--log", str(log)]) == 0
    assert log.read_text() == ""


def test_run_without_the_endpoint_env_var_exits_with_a_message(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RETURNS_DESK_ENDPOINT", raising=False)
    d = _data_dir(tmp_path, {"RQ-305"})
    with pytest.raises(SystemExit) as e:
        cli.main(_args(tmp_path, d, "run"))
    assert e.value.code == 2
    assert "RETURNS_DESK_ENDPOINT" in capsys.readouterr().err


# ------------------------------------------------------------------ serve

def _serve_spies(monkeypatch):
    """Spy on build_app and replace uvicorn.run, so `serve` returns instead of listening."""
    import uvicorn
    from returns_desk import web
    seen: dict = {}
    real_build = web.build_app

    def build(desk, **kw):
        seen["desk"] = desk
        return real_build(desk, **kw)

    def run(app, host, port, **kw):
        seen.update(app=app, host=host, port=port)

    monkeypatch.setattr(web, "build_app", build)
    monkeypatch.setattr(uvicorn, "run", run)
    return seen


def test_serve_builds_the_board_and_runs_uvicorn(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    seen = _serve_spies(monkeypatch)
    d = _data_dir(tmp_path, {"RQ-301"})
    eng = _engines()
    assert cli.main(_args(tmp_path, d, "serve"), engines=eng) == 0
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 8080)
    assert seen["desk"].engines is eng
    assert [r.request_id for r in seen["desk"].queue()] == ["RQ-301"]
    from fastapi import FastAPI
    assert isinstance(seen["app"], FastAPI)


def test_serve_port_env_var_overrides_the_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT", "9191")
    seen = _serve_spies(monkeypatch)
    d = _data_dir(tmp_path, {"RQ-301"})
    assert cli.main(_args(tmp_path, d, "serve") + ["--port", "7000", "--host", "0.0.0.0"],
                    engines=_engines()) == 0
    assert (seen["host"], seen["port"]) == ("0.0.0.0", 9191)


def test_serve_accepts_the_dockerfile_command(monkeypatch):
    """The container's CMD must parse: it is the only way the hosted board starts."""
    import re
    from conftest import APP
    cmd = json.loads(re.search(r"^CMD (\[.*\])$", (APP / "Dockerfile").read_text(), re.M).group(1))
    assert cmd[:2] == ["returns-desk", "serve"]
    monkeypatch.delenv("PORT", raising=False)
    seen = _serve_spies(monkeypatch)
    assert cli.main(cmd[1:], engines=_engines()) == 0
    assert seen["host"] == "0.0.0.0"
