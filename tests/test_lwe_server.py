"""LWE M4: local application tests.

Driven over real HTTP against a real server on loopback, because the things
worth testing here are the boundary behaviours — token enforcement, event
ordering, refusal to bind beyond localhost — and a handler tested by direct
method call would not exercise any of them.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from ai_text_eval.lwe.server import SessionStore, build_server
from ai_text_eval.lwe.session import Attestations, SessionIntent, SessionState


@pytest.fixture()
def app(tmp_path):
    httpd = build_server(tmp_path / "sessions", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def request(url, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Session-Token", token)
    try:
        with urllib.request.urlopen(req) as response:
            payload = response.read().decode()
            kind = response.headers.get("Content-Type", "")
            return response.status, (json.loads(payload) if "json" in kind else payload)
    except urllib.error.HTTPError as err:
        payload = err.read().decode()
        try:
            return err.code, json.loads(payload)
        except json.JSONDecodeError:
            return err.code, payload


def start_session(app, **over):
    body = {"contributor": "wren", "prompt": "Describe a commute.",
            "category": "H-01", "length_bucket": "B250",
            "consent_to_logging": True,
            "environment_free_of_generative_tools": True,
            "spot_verification_acknowledged": True}
    body.update(over)
    status, data = request(f"{app}/session", "POST", body)
    return status, data


def type_text(app, sid, token, text, pos=0, kind="insert"):
    return request(f"{app}/session/{sid}/events", "POST",
                   {"events": [{"kind": kind, "pos": pos, "text": text}]}, token)


# =====================================================================
# Binding and headers
# =====================================================================


def test_the_server_binds_loopback_only(tmp_path):
    """No network beyond this machine. The contributor's writing is not
    something to expose on a LAN by default."""
    httpd = build_server(tmp_path / "s", port=0)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


def test_pages_forbid_external_resources(app):
    """A writing environment that could load a remote script could become a
    generative writing tool without the contributor knowing."""
    req = urllib.request.Request(f"{app}/")
    with urllib.request.urlopen(req) as response:
        policy = response.headers.get("Content-Security-Policy")
    assert "default-src 'none'" in policy


def test_the_start_page_renders(app):
    status, markup = request(f"{app}/")
    assert status == 200
    assert "gauntlet-write" in markup


def test_an_unknown_path_is_a_404(app):
    status, _ = request(f"{app}/nowhere")
    assert status == 404


# =====================================================================
# Creating a session
# =====================================================================


def test_starting_a_session_returns_an_id_and_a_token(app):
    status, data = start_session(app)
    assert status == 200
    assert data["session_id"]
    assert len(data["token"]) >= 20


def test_a_session_without_consent_is_refused(app):
    status, data = start_session(app, consent_to_logging=False)
    assert status == 400
    assert "surveillance" in data["error"]


def test_a_session_without_a_name_is_refused(app):
    status, data = start_session(app, contributor="  ")
    assert status == 400
    assert "named contributor" in data["error"]


def test_declining_the_environment_attestation_still_starts(app):
    status, _ = start_session(app, environment_free_of_generative_tools=False)
    assert status == 200


def test_sessions_get_distinct_ids(app):
    first = start_session(app)[1]["session_id"]
    second = start_session(app)[1]["session_id"]
    assert first != second


# =====================================================================
# The session token
# =====================================================================


def test_events_without_a_token_are_refused(app):
    _, data = start_session(app)
    status, _ = type_text(app, data["session_id"], None, "hello")
    assert status == 403


def test_events_with_a_wrong_token_are_refused(app):
    _, data = start_session(app)
    status, _ = type_text(app, data["session_id"], "not-the-token", "hello")
    assert status == 403


def test_one_sessions_token_does_not_work_on_another(app):
    """Another process on this machine must not be able to append to a live
    session it did not start."""
    first = start_session(app)[1]
    second = start_session(app)[1]
    status, _ = type_text(app, second["session_id"], first["token"], "hello")
    assert status == 403


def test_reading_a_session_needs_no_token(app):
    """The record is protected from modification, not from being read by its
    own contributor."""
    _, data = start_session(app)
    status, _ = request(f"{app}/session/{data['session_id']}/state")
    assert status == 200


# =====================================================================
# Writing
# =====================================================================


def test_typed_text_is_recorded(app):
    _, s = start_session(app)
    status, result = type_text(app, s["session_id"], s["token"], "the bus was late")
    assert status == 200
    assert result["applied"] == 1
    assert result["words"] == 4


def test_a_paste_is_recorded_as_a_paste(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "pasted in", kind="paste")
    _, state = request(f"{app}/session/{s['session_id']}/state")
    assert state["facts"]["paste_count"] == 1
    assert state["facts"]["pasted_chars"] == len("pasted in")


def test_a_batch_applies_in_order(app):
    _, s = start_session(app)
    request(f"{app}/session/{s['session_id']}/events", "POST", {"events": [
        {"kind": "insert", "pos": 0, "text": "hello cruel world"},
        {"kind": "delete", "pos": 5, "length": 6},
    ]}, s["token"])
    _, state = request(f"{app}/session/{s['session_id']}/state")
    assert state["facts"]["deleted_chars"] == 6
    assert state["facts"]["events"]["delete"] == 1


def test_a_bad_event_stops_the_batch_and_reports_what_applied(app):
    """The journal must never contain an event the document did not accept."""
    _, s = start_session(app)
    status, data = request(f"{app}/session/{s['session_id']}/events", "POST",
                           {"events": [
                               {"kind": "insert", "pos": 0, "text": "ok"},
                               {"kind": "teleport", "pos": 0}]}, s["token"])
    assert status == 400
    assert data["applied"] == 1


def test_an_impossible_edit_is_reported_not_silently_dropped(app):
    _, s = start_session(app)
    status, data = request(f"{app}/session/{s['session_id']}/events", "POST",
                           {"events": [{"kind": "delete", "pos": 0, "length": 99}]},
                           s["token"])
    assert status == 409
    assert "does not apply" in data["error"]


def test_focus_events_are_accepted(app):
    _, s = start_session(app)
    request(f"{app}/session/{s['session_id']}/events", "POST",
            {"events": [{"kind": "focus_out"}, {"kind": "focus_in"}]}, s["token"])
    _, state = request(f"{app}/session/{s['session_id']}/state")
    assert state["facts"]["focus_out_count"] == 1


# =====================================================================
# Closing and exporting
# =====================================================================


def test_closing_seals_and_verifies(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "a finished sample")
    status, data = request(f"{app}/session/{s['session_id']}/close", "POST",
                           token=s["token"])
    assert status == 200
    assert data["verification"]["verified"] is True
    assert data["state"] == "closed"


def test_writing_after_close_is_refused(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "done")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    status, _ = type_text(app, s["session_id"], s["token"], " more", pos=4)
    assert status == 409


def test_export_reports_the_tier_it_earned(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "written under logging")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    status, data = request(f"{app}/session/{s['session_id']}/export", "POST",
                           token=s["token"])
    assert status == 200
    assert data["tier"] == "T1"
    assert data["supported"] is True


def test_a_declined_attestation_exports_at_t2(app):
    _, s = start_session(app, environment_free_of_generative_tools=False)
    type_text(app, s["session_id"], s["token"], "written without the attestation")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    _, data = request(f"{app}/session/{s['session_id']}/export", "POST",
                      token=s["token"])
    assert data["tier"] == "T2"


def test_abandoning_leaves_a_retained_record(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "half a thought")
    status, data = request(f"{app}/session/{s['session_id']}/abandon", "POST",
                           {"reason": "interrupted"}, s["token"])
    assert status == 200
    assert data["state"] == "abandoned"


# =====================================================================
# The reviewer page
# =====================================================================


def test_the_review_page_orders_verification_before_the_text(app):
    """CAS §6.1: a reviewer who reads the prose first reasons backwards from
    how it reads, which is the review defect the ordering prevents."""
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "the sample text itself")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    _, markup = request(f"{app}/session/{s['session_id']}/review")
    assert markup.index("1. Verification") < markup.index("2. Process facts")
    assert markup.index("2. Process facts") < markup.index("3. What the contributor")
    assert markup.index("3. What the contributor") < markup.index("4. The text")


def test_the_review_page_says_the_text_is_not_evidence(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "some prose")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    _, markup = request(f"{app}/session/{s['session_id']}/review")
    assert "not</strong> evidence for the" in markup


def test_the_review_page_shows_no_score(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "some prose")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    _, markup = request(f"{app}/session/{s['session_id']}/review")
    lowered = markup.lower()
    for word in ("authenticity", "humanness", "suspicious", "likelihood"):
        assert word not in lowered


def test_the_review_page_escapes_contributor_text(app):
    """The text is contributor-controlled and is rendered back into a page."""
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "<script>alert(1)</script>")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    _, markup = request(f"{app}/session/{s['session_id']}/review")
    assert "<script>alert(1)</script>" not in markup
    assert "&lt;script&gt;" in markup


def test_an_open_session_shows_the_writing_page(app):
    _, s = start_session(app)
    _, markup = request(f"{app}/session/{s['session_id']}")
    assert "recording" in markup
    assert "<textarea id=surface" in markup


def test_a_closed_session_shows_the_review_page_instead(app):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "done writing")
    request(f"{app}/session/{s['session_id']}/close", "POST", token=s["token"])
    _, markup = request(f"{app}/session/{s['session_id']}")
    assert "1. Verification" in markup


def test_an_unknown_session_is_a_404(app):
    status, _ = request(f"{app}/session/does-not-exist/state")
    assert status == 404


# =====================================================================
# The store
# =====================================================================


def test_the_store_lists_sessions(app, tmp_path):
    _, s = start_session(app)
    type_text(app, s["session_id"], s["token"], "listed")
    status, data = request(f"{app}/sessions")
    assert status == 200
    assert [row["session_id"] for row in data["sessions"]] == [s["session_id"]]


def test_the_store_reloads_a_session_from_disk(tmp_path):
    """A restart must not lose a session in progress."""
    store = SessionStore(tmp_path / "sessions")
    session = store.create(
        SessionIntent(contributor="wren"),
        Attestations(consent_to_logging=True,
                     environment_free_of_generative_tools=True))
    session.insert(0, "written before the restart")

    reloaded = SessionStore(tmp_path / "sessions").get(session.session_id)
    assert reloaded.text == "written before the restart"
    assert reloaded.state is SessionState.OPEN


def test_tokens_do_not_survive_a_restart(tmp_path):
    """Tokens guard a live local endpoint; persisting one would leave a
    credential sitting in the evidence directory."""
    store = SessionStore(tmp_path / "sessions")
    session = store.create(SessionIntent(contributor="wren"),
                           Attestations(consent_to_logging=True))
    reloaded = SessionStore(tmp_path / "sessions").get(session.session_id)
    assert reloaded.token != session.token
    assert not any("token" in p.read_text()
                   for p in session.root.iterdir() if p.is_file())
