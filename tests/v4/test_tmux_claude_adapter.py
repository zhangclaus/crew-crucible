import json
from pathlib import Path

from codex_claude_orchestrator.v4.adapters.tmux_claude import ClaudeCodeTmuxAdapter
from codex_claude_orchestrator.v4.runtime import TurnEnvelope, WorkerSpec


class FakeNativeSession:
    def __init__(self):
        self.sent = []
        self.observations = []
        self.send_result = None
        self.observe_result = None
        self.observe_exception = None

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return self.send_result or {
            "marker": kwargs["turn_marker"],
            "message": kwargs["message"],
        }

    def observe(self, **kwargs):
        self.observations.append(kwargs)
        if self.observe_exception is not None:
            raise self.observe_exception
        return self.observe_result or {
            "snapshot": "hello\nmarker-1",
            "marker": "marker-1",
            "marker_seen": True,
            "transcript_artifact": "turns/turn-1/transcript.txt",
        }


def test_tmux_adapter_accepts_poll_parameters():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_initial_delay=1.0,
        poll_max_delay=5.0,
        poll_timeout=60.0,
    )
    assert adapter._poll_initial_delay == 1.0
    assert adapter._poll_max_delay == 5.0
    assert adapter._poll_timeout == 60.0


def test_tmux_adapter_poll_defaults():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    assert adapter._poll_initial_delay == 2.0
    assert adapter._poll_max_delay == 10.0
    assert adapter._poll_timeout == 300.0


def test_tmux_adapter_delivers_turn_to_native_session():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    result = adapter.deliver_turn(turn)

    assert result.delivered is True
    assert native.sent[0]["turn_marker"] == "marker-1"


def test_tmux_adapter_includes_turn_context_in_delivered_message():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
        unread_inbox_digest="- [msg-1] question from codex: review this",
        unread_message_ids=["msg-1"],
        open_protocol_requests=[{"request_id": "req-1", "subject": "Review patch"}],
        open_protocol_requests_digest="- [req-1] review from codex: Review patch",
    )

    adapter.deliver_turn(turn)

    sent_message = native.sent[0]["message"]
    assert "Implement" in sent_message
    assert "Unread inbox" in sent_message
    assert "msg-1" in sent_message
    assert "Open protocol requests" in sent_message
    assert "req-1" in sent_message
    assert "Required outbox identity" in sent_message
    assert "turn-1" in sent_message


def test_tmux_adapter_includes_required_outbox_path_in_delivered_message(tmp_path: Path):
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    outbox_path = tmp_path / "workers" / "worker-1" / "outbox" / "turn-1.json"
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
        required_outbox_path=str(outbox_path),
    )

    adapter.deliver_turn(turn)

    sent_message = native.sent[0]["message"]
    assert str(outbox_path) in sent_message
    assert "Create the parent directory if it does not exist." in sent_message


def test_tmux_adapter_unregistered_worker_uses_worker_id_as_terminal_pane():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    adapter.deliver_turn(turn)
    list(adapter.watch_turn(turn))

    assert native.sent[0]["terminal_pane"] == "worker-1"
    assert native.observations[0]["terminal_pane"] == "worker-1"


def test_tmux_adapter_registered_worker_empty_pane_uses_worker_id_as_terminal_pane():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    adapter.register_worker(
        WorkerSpec(
            crew_id="crew-1",
            worker_id="worker-1",
            runtime_type="tmux_claude",
            contract_id="contract-1",
        )
    )
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    adapter.deliver_turn(turn)
    list(adapter.watch_turn(turn))

    assert native.sent[0]["terminal_pane"] == "worker-1"
    assert native.observations[0]["terminal_pane"] == "worker-1"


def test_tmux_adapter_native_send_failure_maps_to_delivery_result():
    native = FakeNativeSession()
    native.send_result = {"delivered": False, "reason": "pane missing"}
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    result = adapter.deliver_turn(turn)

    assert result.delivered is False
    assert result.marker == "marker-1"
    assert result.reason == "pane missing"


def test_tmux_adapter_native_ok_false_maps_to_delivery_result():
    native = FakeNativeSession()
    native.send_result = {"ok": False, "marker": "marker-2", "reason": "send rejected"}
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    result = adapter.deliver_turn(turn)

    assert result.delivered is False
    assert result.marker == "marker-2"
    assert result.reason == "send rejected"


def test_tmux_adapter_watch_turn_emits_output_and_marker_events():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    adapter.register_worker(
        WorkerSpec(
            crew_id="crew-1",
            worker_id="worker-1",
            runtime_type="tmux_claude",
            contract_id="contract-1",
            terminal_pane="pane-1",
        )
    )
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == ["output.chunk", "marker.detected"]
    assert events[-1].payload["marker"] == "marker-1"


def test_tmux_adapter_watch_turn_marker_not_seen_emits_only_output_chunk(monkeypatch):
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": "still running",
        "marker": "marker-1",
        "marker_seen": False,
        "transcript_artifact": "turns/turn-1/transcript.txt",
    }
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_timeout=0.0,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == ["output.chunk", "runtime.poll_timeout"]
    assert events[0].payload == {"text": "still running"}


def test_tmux_adapter_watch_turn_emits_required_outbox_without_marker(tmp_path: Path, monkeypatch):
    outbox_path = tmp_path / "workers" / "worker-1" / "outbox" / "turn-1.json"
    outbox_path.parent.mkdir(parents=True)
    outbox_path.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "turn-1",
                "status": "completed",
                "summary": "implemented",
            }
        ),
        encoding="utf-8",
    )
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": "",
        "marker": "marker-1",
        "marker_seen": False,
        "transcript_artifact": "",
    }
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_timeout=0.0,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
        required_outbox_path=str(outbox_path),
    )

    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == ["worker.outbox.detected", "runtime.poll_timeout"]
    assert events[0].payload["valid"] is True
    assert events[0].artifact_refs == ["workers/worker-1/outbox/turn-1.json"]


def test_tmux_adapter_watch_turn_reads_outbox_when_observe_fails(tmp_path: Path):
    outbox_path = tmp_path / "workers" / "worker-1" / "outbox" / "turn-1.json"
    outbox_path.parent.mkdir(parents=True)
    outbox_path.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "turn-1",
                "status": "completed",
                "summary": "implemented",
            }
        ),
        encoding="utf-8",
    )
    native = FakeNativeSession()
    native.observe_exception = RuntimeError("capture-pane failed")
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
        required_outbox_path=str(outbox_path),
    )

    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == [
        "worker.outbox.detected",
        "runtime.observe_failed",
    ]
    assert events[0].payload["valid"] is True
    assert events[1].payload == {
        "source": "tmux",
        "error": "capture-pane failed",
    }


def test_tmux_adapter_filesystem_stream_dedupes_outbox_between_polls(tmp_path: Path, monkeypatch):
    outbox_path = tmp_path / "workers" / "worker-1" / "outbox" / "turn-1.json"
    outbox_path.parent.mkdir(parents=True)
    outbox_path.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "turn-1",
                "status": "completed",
                "summary": "implemented",
            }
        ),
        encoding="utf-8",
    )
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": "",
        "marker": "marker-1",
        "marker_seen": False,
        "transcript_artifact": "",
    }
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_timeout=0.0,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
        required_outbox_path=str(outbox_path),
    )

    first = list(adapter.watch_turn(turn))
    adapter.commit_runtime_events(turn, first)
    second = list(adapter.watch_turn(turn))

    assert [event.type for event in first] == ["worker.outbox.detected", "runtime.poll_timeout"]
    assert [event.type for event in second] == ["runtime.poll_timeout"]


def test_tmux_adapter_watch_turn_ignores_malformed_observation_values(monkeypatch):
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": ["not", "text"],
        "marker": "",
        "marker_seen": "yes",
        "transcript_artifact": 42,
    }
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_timeout=0.0,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == ["runtime.poll_timeout"]


def test_tmux_adapter_watch_turn_falls_back_when_marker_is_empty():
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": "",
        "marker": "",
        "marker_seen": True,
        "transcript_artifact": "",
    }
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == ["marker.detected"]
    assert events[0].payload["marker"] == "marker-1"
    assert events[0].artifact_refs == []


def test_tmux_adapter_tails_transcript_incrementally_and_persists_cursor(tmp_path: Path):
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("old output\n", encoding="utf-8")
    native = FakeNativeSession()
    native.observe_exception = RuntimeError("capture-pane failed")
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    adapter.register_worker(
        WorkerSpec(
            crew_id="crew-1",
            worker_id="worker-1",
            runtime_type="tmux_claude",
            contract_id="contract-1",
            terminal_pane="pane-1",
            transcript_artifact=str(transcript),
        )
    )
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )
    adapter.deliver_turn(turn)
    transcript.write_text("old output\nnew output\nmarker-1\n", encoding="utf-8")

    first_events = list(adapter.watch_turn(turn))
    adapter.commit_runtime_events(turn, first_events)
    second_adapter = ClaudeCodeTmuxAdapter(native_session=native)
    second_adapter.register_worker(
        WorkerSpec(
            crew_id="crew-1",
            worker_id="worker-1",
            runtime_type="tmux_claude",
            contract_id="contract-1",
            terminal_pane="pane-1",
            transcript_artifact=str(transcript),
        )
    )
    second_events = list(second_adapter.watch_turn(turn))

    assert [event.type for event in first_events] == [
        "runtime.output.appended",
        "marker.detected",
        "runtime.observe_failed",
    ]
    assert first_events[0].payload["text"] == "new output\nmarker-1\n"
    assert not [event for event in second_events if event.type == "runtime.output.appended"]


class TranscriptWritingNativeSession(FakeNativeSession):
    def __init__(self, transcript_path: Path):
        super().__init__()
        self.transcript_path = transcript_path
        self.observe_exception = RuntimeError("capture-pane failed")

    def send(self, **kwargs):
        result = super().send(**kwargs)
        self.transcript_path.write_text(
            self.transcript_path.read_text(encoding="utf-8") + "during send\nmarker-1\n",
            encoding="utf-8",
        )
        return result


def test_watch_turn_polls_until_marker_detected(monkeypatch):
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": "still working",
        "marker": "marker-1",
        "marker_seen": False,
        "transcript_artifact": "",
    }

    call_count = 0

    def observe_with_marker_on_third(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return {
                "snapshot": "done\nmarker-1",
                "marker": "marker-1",
                "marker_seen": True,
                "transcript_artifact": "",
            }
        return {
            "snapshot": "still working",
            "marker": "marker-1",
            "marker_seen": False,
            "transcript_artifact": "",
        }

    native.observe = observe_with_marker_on_third
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_initial_delay=0.01,
        poll_max_delay=0.05,
        poll_timeout=10.0,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    assert call_count == 3
    assert events[-1].type == "marker.detected"
    assert events[-1].payload["marker"] == "marker-1"


def test_tmux_adapter_initializes_transcript_cursor_before_send(tmp_path: Path):
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("before turn\n", encoding="utf-8")
    native = TranscriptWritingNativeSession(transcript)
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    adapter.register_worker(
        WorkerSpec(
            crew_id="crew-1",
            worker_id="worker-1",
            runtime_type="tmux_claude",
            contract_id="contract-1",
            terminal_pane="pane-1",
            transcript_artifact=str(transcript),
        )
    )
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    adapter.deliver_turn(turn)
    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == [
        "runtime.output.appended",
        "marker.detected",
        "runtime.observe_failed",
    ]
    assert events[0].payload["text"] == "during send\nmarker-1\n"


def test_watch_turn_yields_incremental_output(monkeypatch):
    native = FakeNativeSession()

    call_count = 0
    def observe_growing_text(**kwargs):
        nonlocal call_count
        call_count += 1
        snapshots = ["hel", "hello", "hello world\nmarker-1"]
        idx = min(call_count - 1, len(snapshots) - 1)
        return {
            "snapshot": snapshots[idx],
            "marker": "marker-1",
            "marker_seen": idx == len(snapshots) - 1,
            "transcript_artifact": "",
        }

    native.observe = observe_growing_text
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_initial_delay=0.01,
        poll_max_delay=0.05,
        poll_timeout=10.0,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)
    turn = TurnEnvelope(
        crew_id="crew-1", worker_id="worker-1", turn_id="turn-1",
        round_id="round-1", phase="source", message="go", expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    output_events = [e for e in events if e.type == "output.chunk"]
    assert len(output_events) == 3
    assert output_events[0].payload["text"] == "hel"
    assert output_events[1].payload["text"] == "lo"
    assert output_events[2].payload["text"] == " world\nmarker-1"
    assert events[-1].type == "marker.detected"


def test_watch_turn_timeout_yields_poll_timeout(monkeypatch):
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": "still going",
        "marker": "marker-1",
        "marker_seen": False,
        "transcript_artifact": "",
    }
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_initial_delay=0.01,
        poll_max_delay=0.05,
        poll_timeout=0.05,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)

    # Make monotonic advance past deadline after first poll
    import codex_claude_orchestrator.v4.adapters.tmux_claude as mod
    original_monotonic = mod.time.monotonic
    calls = [0]
    def fake_monotonic():
        calls[0] += 1
        if calls[0] > 2:
            return original_monotonic() + 999
        return original_monotonic()
    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)

    turn = TurnEnvelope(
        crew_id="crew-1", worker_id="worker-1", turn_id="turn-1",
        round_id="round-1", phase="source", message="go", expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    timeout_events = [e for e in events if e.type == "runtime.poll_timeout"]
    assert len(timeout_events) == 1
    assert timeout_events[0].payload["timeout_seconds"] == 0.05


def test_watch_turn_backoff_increases_delay(monkeypatch):
    native = FakeNativeSession()
    native.observe_result = {
        "snapshot": "waiting",
        "marker": "marker-1",
        "marker_seen": False,
        "transcript_artifact": "",
    }
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_initial_delay=1.0,
        poll_max_delay=5.0,
        poll_timeout=300.0,
    )

    class _AbortPolling(Exception):
        """Sentinel raised to break out of the poll loop."""

    sleep_calls = []
    def track_sleep(d):
        sleep_calls.append(d)
        if len(sleep_calls) >= 4:
            raise _AbortPolling()
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", track_sleep)

    turn = TurnEnvelope(
        crew_id="crew-1", worker_id="worker-1", turn_id="turn-1",
        round_id="round-1", phase="source", message="go", expected_marker="marker-1",
    )

    try:
        list(adapter.watch_turn(turn))
    except _AbortPolling:
        pass

    assert sleep_calls[0] == 1.0
    assert sleep_calls[1] == 2.0
    assert sleep_calls[2] == 4.0
    assert sleep_calls[3] == 5.0  # capped at poll_max_delay


def test_watch_turn_observe_failed_returns_immediately(monkeypatch):
    native = FakeNativeSession()
    native.observe_exception = RuntimeError("tmux session dead")
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_initial_delay=0.01,
        poll_timeout=10.0,
    )
    monkeypatch.setattr("codex_claude_orchestrator.v4.adapters.tmux_claude.time.sleep", lambda _: None)
    turn = TurnEnvelope(
        crew_id="crew-1", worker_id="worker-1", turn_id="turn-1",
        round_id="round-1", phase="source", message="go", expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    assert len(events) == 1
    assert events[0].type == "runtime.observe_failed"
    assert events[0].payload["source"] == "tmux"
    assert "tmux session dead" in events[0].payload["error"]
