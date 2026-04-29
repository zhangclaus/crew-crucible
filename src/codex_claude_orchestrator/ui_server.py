from __future__ import annotations

import json
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from codex_claude_orchestrator.run_recorder import RunRecorder
from codex_claude_orchestrator.session_recorder import SessionRecorder
from codex_claude_orchestrator.skill_evolution import SkillEvolution


def build_ui_state(repo_root: Path) -> dict:
    repo_root = repo_root.resolve()
    state_root = repo_root / ".orchestrator"
    return {
        "repo": str(repo_root),
        "sessions": SessionRecorder(state_root).list_sessions(),
        "runs": RunRecorder(state_root).list_runs(),
        "skills": SkillEvolution(state_root).list_skills(),
    }


def resolve_ui_request(repo_root: Path, request_path: str) -> tuple[str, str]:
    repo_root = repo_root.resolve()
    state_root = repo_root / ".orchestrator"
    path = unquote(urlparse(request_path).path)
    session_recorder = SessionRecorder(state_root)
    run_recorder = RunRecorder(state_root)
    skill_evolution = SkillEvolution(state_root)

    if path == "/":
        return "text/html; charset=utf-8", render_index_html(repo_root)
    if path == "/api/state":
        return "application/json; charset=utf-8", _json(build_ui_state(repo_root))
    if path.startswith("/api/sessions/"):
        session_id = _safe_resource_id(path.removeprefix("/api/sessions/"))
        return "application/json; charset=utf-8", _json(session_recorder.read_session(session_id))
    if path.startswith("/api/runs/"):
        run_id = _safe_resource_id(path.removeprefix("/api/runs/"))
        return "application/json; charset=utf-8", _json(run_recorder.read_run(run_id))
    if path.startswith("/api/skills/"):
        skill_id = _safe_resource_id(path.removeprefix("/api/skills/"))
        return "application/json; charset=utf-8", _json(skill_evolution.show_skill(skill_id))
    if path.startswith("/api/run-artifacts/"):
        run_id, artifact = _split_artifact_path(path, "/api/run-artifacts/")
        return "text/plain; charset=utf-8", _read_safe_artifact(state_root / "runs" / run_id / "artifacts", artifact)
    if path.startswith("/api/session-artifacts/"):
        session_id, artifact = _split_artifact_path(path, "/api/session-artifacts/")
        return (
            "text/plain; charset=utf-8",
            _read_safe_artifact(state_root / "sessions" / session_id / "artifacts", artifact),
        )
    raise FileNotFoundError(path)


def make_ui_handler(repo_root: Path):
    repo_root = repo_root.resolve()

    class OrchestratorUIHandler(BaseHTTPRequestHandler):
        server_version = "OrchestratorUI/0.1"

        def do_GET(self) -> None:
            try:
                content_type, body = resolve_ui_request(repo_root, self.path)
            except (FileNotFoundError, ValueError, KeyError):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send(body, content_type)

        def log_message(self, format: str, *args) -> None:
            return

        def _send(self, text: str, content_type: str) -> None:
            body = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return OrchestratorUIHandler


def _json(payload: dict | list) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _split_artifact_path(path: str, prefix: str) -> tuple[str, str]:
    remainder = path.removeprefix(prefix)
    item_id, separator, artifact = remainder.partition("/")
    if not item_id or not separator or not artifact:
        raise FileNotFoundError(path)
    return item_id, artifact


def _safe_resource_id(value: str) -> str:
    resource_id = value.strip("/")
    if not resource_id or "/" in resource_id or "\\" in resource_id or resource_id in {".", ".."}:
        raise ValueError("unsafe resource id")
    relative = Path(resource_id)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe resource id")
    return resource_id


def run_ui_server(repo_root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), make_ui_handler(repo_root))
    actual_host, actual_port = server.server_address
    url = f"http://{actual_host}:{actual_port}"
    print(json.dumps({"url": url, "repo": str(repo_root.resolve())}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def render_index_html(repo_root: Path) -> str:
    repo_text = escape(str(repo_root.resolve()), quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Orchestrator V2 Console</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d9dee8;
      --text: #1f2937;
      --muted: #667085;
      --accent: #2f6f72;
      --accent-2: #445d8c;
      --warn: #9a6a13;
      --bad: #a33a3a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
    }}
    header {{
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .repo {{
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 54vw;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 330px minmax(0, 1fr) 360px;
      min-height: calc(100vh - 56px);
    }}
    aside, main, section {{
      min-width: 0;
      border-right: 1px solid var(--line);
    }}
    aside, section {{
      background: var(--panel);
    }}
    .pane-head {{
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
      font-weight: 700;
    }}
    .pane-block + .pane-block {{
      border-top: 1px solid var(--line);
    }}
    button {{
      min-height: 30px;
      border: 1px solid #bfc7d5;
      background: #fff;
      color: var(--text);
      padding: 0 10px;
      border-radius: 6px;
      cursor: pointer;
      font: inherit;
    }}
    button:hover {{ border-color: var(--accent-2); }}
    .list {{
      display: grid;
      gap: 8px;
      padding: 12px;
    }}
    .item {{
      width: 100%;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
    }}
    .item.active {{
      border-color: var(--accent);
      box-shadow: inset 4px 0 0 var(--accent);
    }}
    .item-title {{
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .meta {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}
    .content {{
      padding: 14px;
      display: grid;
      gap: 14px;
    }}
    .band {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }}
    .band h2 {{
      margin: 0;
      padding: 11px 13px;
      border-bottom: 1px solid var(--line);
      font-size: 14px;
      letter-spacing: 0;
    }}
    .band-body {{
      padding: 12px 13px;
      display: grid;
      gap: 10px;
    }}
    .row {{
      display: grid;
      grid-template-columns: 130px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .value {{ overflow-wrap: anywhere; }}
    .timeline {{
      display: grid;
      gap: 10px;
    }}
    .event {{
      border-left: 4px solid var(--accent-2);
      padding: 8px 10px;
      background: #f9fafb;
      border-radius: 0 6px 6px 0;
    }}
    .ok {{ color: var(--accent); font-weight: 700; }}
    .bad {{ color: var(--bad); font-weight: 700; }}
    .warn {{ color: var(--warn); font-weight: 700; }}
    pre {{
      margin: 0;
      padding: 12px;
      border: 1px solid #ccd3df;
      border-radius: 6px;
      background: #1f2937;
      color: #edf2f7;
      overflow: auto;
      max-height: 320px;
      font-size: 12px;
      line-height: 1.5;
    }}
    .tabs {{
      display: flex;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }}
    .tab.active {{
      border-color: var(--accent);
      color: var(--accent);
      font-weight: 700;
    }}
    @media (max-width: 1080px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside, main, section {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .repo {{ max-width: 45vw; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Orchestrator V2 Console</h1>
    <div class="repo">{repo_text}</div>
  </header>
  <div class="layout">
    <aside>
      <div class="pane-block">
        <div class="pane-head"><span>Sessions</span><button id="refresh">Refresh</button></div>
        <div id="sessions" class="list"></div>
      </div>
      <div class="pane-block">
        <div class="pane-head"><span>Agent Runs</span></div>
        <div id="runs" class="list"></div>
      </div>
    </aside>
    <main>
      <div class="tabs">
        <button class="tab active" data-tab="timeline">Session Timeline</button>
        <button class="tab" data-tab="output">OutputTrace</button>
        <button class="tab" data-tab="verify">Verification</button>
      </div>
      <div id="main" class="content"></div>
    </main>
    <section>
      <div class="pane-head"><span>Pending Skills</span></div>
      <div id="skills" class="list"></div>
    </section>
  </div>
  <script>
    const state = {{ sessions: [], runs: [], skills: [] }};
    let selectedSession = null;
    let selectedRun = null;
    let selectedView = "session";
    let selectedTab = "timeline";

    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({{
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }}[ch]));

    async function loadState() {{
      const response = await fetch("/api/state");
      const payload = await response.json();
      state.sessions = payload.sessions || [];
      state.runs = payload.runs || [];
      state.skills = payload.skills || [];
      if (!selectedSession && state.sessions.length) selectedSession = state.sessions[0].session_id;
      if (!selectedRun && state.runs.length) selectedRun = state.runs[0].run_id;
      if (!selectedSession && selectedRun) selectedView = "run";
      renderSessions();
      renderRuns();
      renderSkills();
      await renderMain();
    }}

    function renderSessions() {{
      const host = document.querySelector("#sessions");
      if (!state.sessions.length) {{
        host.innerHTML = '<div class="meta">No sessions</div>';
        return;
      }}
      host.innerHTML = state.sessions.map((session) => `
        <button class="item ${{selectedView === "session" && session.session_id === selectedSession ? "active" : ""}}" data-session="${{esc(session.session_id)}}">
          <div class="item-title">${{esc(session.goal || session.session_id)}}</div>
          <div class="meta">${{esc(session.status)}} · ${{esc(session.assigned_agent)}} · ${{esc(session.session_id)}}</div>
          <div class="meta">${{esc(session.summary || "")}}</div>
        </button>
      `).join("");
      host.querySelectorAll("[data-session]").forEach((button) => {{
        button.addEventListener("click", async () => {{
          selectedSession = button.dataset.session;
          selectedView = "session";
          renderSessions();
          renderRuns();
          await renderMain();
        }});
      }});
    }}

    function renderRuns() {{
      const host = document.querySelector("#runs");
      if (!state.runs.length) {{
        host.innerHTML = '<div class="meta">No agent runs</div>';
        return;
      }}
      host.innerHTML = state.runs.map((run) => `
        <button class="item ${{selectedView === "run" && run.run_id === selectedRun ? "active" : ""}}" data-run="${{esc(run.run_id)}}">
          <div class="item-title">${{esc(run.summary || run.run_id)}}</div>
          <div class="meta">${{esc(run.status)}} · ${{esc(run.agent)}} · ${{esc(run.run_id)}}</div>
          <div class="meta">accepted: ${{esc(run.accepted)}}</div>
        </button>
      `).join("");
      host.querySelectorAll("[data-run]").forEach((button) => {{
        button.addEventListener("click", async () => {{
          selectedRun = button.dataset.run;
          selectedView = "run";
          renderSessions();
          renderRuns();
          await renderMain();
        }});
      }});
    }}

    function renderSkills() {{
      const host = document.querySelector("#skills");
      const skills = state.skills.filter((skill) => skill.status === "pending");
      if (!skills.length) {{
        host.innerHTML = '<div class="meta">No pending skills</div>';
        return;
      }}
      host.innerHTML = skills.map((skill) => `
        <div class="item">
          <div class="item-title">${{esc(skill.name)}}</div>
          <div class="meta">${{esc(skill.source_session_id || "")}}</div>
          <div class="meta">${{esc(skill.summary || "")}}</div>
        </div>
      `).join("");
    }}

    async function renderMain() {{
      const host = document.querySelector("#main");
      if (selectedView === "run") {{
        if (!selectedRun) {{
          host.innerHTML = '<div class="band"><h2>Agent Run</h2><div class="band-body"><div class="meta">No run selected</div></div></div>';
          return;
        }}
        const response = await fetch(`/api/runs/${{encodeURIComponent(selectedRun)}}`);
        const detail = await response.json();
        await renderRunDetail(host, detail);
        return;
      }}
      if (!selectedSession) {{
        host.innerHTML = '<div class="band"><h2>Session Timeline</h2><div class="band-body"><div class="meta">No session selected</div></div></div>';
        return;
      }}
      const response = await fetch(`/api/sessions/${{encodeURIComponent(selectedSession)}}`);
      const detail = await response.json();
      if (selectedTab === "output") renderOutput(host, detail);
      else if (selectedTab === "verify") renderVerification(host, detail);
      else renderTimeline(host, detail);
    }}

    function renderTimeline(host, detail) {{
      const session = detail.session || {{}};
      const turns = detail.turns || [];
      const challenges = detail.challenges || [];
      host.innerHTML = `
        <div class="band">
          <h2>Session Timeline</h2>
          <div class="band-body">
            <div class="row"><div class="label">Status</div><div class="value ${{session.status === "accepted" ? "ok" : "warn"}}">${{esc(session.status)}}</div></div>
            <div class="row"><div class="label">Goal</div><div class="value">${{esc(session.goal)}}</div></div>
            <div class="row"><div class="label">Rounds</div><div class="value">${{esc(session.current_round)}} / ${{esc(session.max_rounds)}}</div></div>
            <div class="row"><div class="label">Summary</div><div class="value">${{esc(session.final_summary)}}</div></div>
          </div>
        </div>
        <div class="band">
          <h2>Turns</h2>
          <div class="band-body timeline">
            ${{turns.map((turn) => `<div class="event"><strong>${{esc(turn.phase)}}</strong> · round ${{esc(turn.round_index)}}<div class="meta">${{esc(turn.summary || turn.message)}}</div></div>`).join("") || '<div class="meta">No turns</div>'}}
          </div>
        </div>
        <div class="band">
          <h2>Challenges</h2>
          <div class="band-body timeline">
            ${{challenges.map((challenge) => `<div class="event"><strong>${{esc(challenge.challenge_type)}}</strong><div class="meta">${{esc(challenge.summary)}}</div><div>${{esc(challenge.question || "")}}</div></div>`).join("") || '<div class="meta">No challenges</div>'}}
          </div>
        </div>
      `;
    }}

    function renderOutput(host, detail) {{
      const traces = detail.output_traces || [];
      host.innerHTML = `
        <div class="band">
          <h2>OutputTrace</h2>
          <div class="band-body">
            ${{traces.map((trace) => `
              <div class="event">
                <strong>${{esc(trace.agent)}} · ${{esc(trace.run_id)}}</strong>
                <div class="meta">${{esc(trace.display_summary || trace.output_summary)}}</div>
                <div class="row"><div class="label">Command</div><div class="value">${{esc((trace.command || []).join(" "))}}</div></div>
                <div class="row"><div class="label">Artifacts</div><div class="value">${{esc((trace.artifact_paths || []).join(", "))}}</div></div>
                <div class="row"><div class="label">Changed</div><div class="value">${{esc((trace.changed_files || []).join(", "))}}</div></div>
                <pre>${{esc(JSON.stringify(trace.evaluation || {{}}, null, 2))}}</pre>
              </div>
            `).join("") || '<div class="meta">No output traces</div>'}}
          </div>
        </div>
      `;
    }}

    async function renderRunDetail(host, detail) {{
      const task = detail.task || {{}};
      const run = detail.run || {{}};
      const artifacts = detail.artifacts || [];
      const stdout = artifacts.includes("stdout.txt") ? await fetchRunArtifact(run.run_id, "stdout.txt") : "";
      const stderr = artifacts.includes("stderr.txt") ? await fetchRunArtifact(run.run_id, "stderr.txt") : "";
      host.innerHTML = `
        <div class="band">
          <h2>Agent Run</h2>
          <div class="band-body">
            <div class="row"><div class="label">Agent</div><div class="value">${{esc(run.agent)}}</div></div>
            <div class="row"><div class="label">Status</div><div class="value">${{esc(run.status)}}</div></div>
            <div class="row"><div class="label">Goal</div><div class="value">${{esc(task.goal)}}</div></div>
            <div class="row"><div class="label">Task</div><div class="value">${{esc(task.task_id)}} · ${{esc(task.task_type)}}</div></div>
            <div class="row"><div class="label">Artifacts</div><div class="value">${{esc(artifacts.join(", "))}}</div></div>
          </div>
        </div>
        <div class="band">
          <h2>Evaluation</h2>
          <div class="band-body"><pre>${{esc(JSON.stringify(detail.evaluation || {{}}, null, 2))}}</pre></div>
        </div>
        <div class="band">
          <h2>stdout</h2>
          <div class="band-body"><pre>${{esc(stdout || "No stdout")}}</pre></div>
        </div>
        <div class="band">
          <h2>stderr</h2>
          <div class="band-body"><pre>${{esc(stderr || "No stderr")}}</pre></div>
        </div>
        <div class="band">
          <h2>Events</h2>
          <div class="band-body timeline">
            ${{(detail.events || []).map((event) => `<div class="event"><strong>${{esc(event.event_type)}}</strong><div class="meta">${{esc(JSON.stringify(event.payload || {{}}))}}</div></div>`).join("") || '<div class="meta">No events</div>'}}
          </div>
        </div>
      `;
    }}

    async function fetchRunArtifact(runId, artifact) {{
      try {{
        const response = await fetch(`/api/run-artifacts/${{encodeURIComponent(runId)}}/${{encodeURIComponent(artifact)}}`);
        if (!response.ok) return "";
        return await response.text();
      }} catch {{
        return "";
      }}
    }}

    function renderVerification(host, detail) {{
      const verifications = detail.verifications || [];
      host.innerHTML = `
        <div class="band">
          <h2>Verification</h2>
          <div class="band-body timeline">
            ${{verifications.map((record) => `<div class="event"><strong class="${{record.passed ? "ok" : "bad"}}">${{record.passed ? "passed" : "failed"}}</strong> · ${{esc(record.command || record.kind)}}<div class="meta">${{esc(record.summary)}}</div></div>`).join("") || '<div class="meta">No verification records</div>'}}
          </div>
        </div>
      `;
    }}

    document.querySelector("#refresh").addEventListener("click", loadState);
    document.querySelectorAll(".tab").forEach((tab) => {{
      tab.addEventListener("click", async () => {{
        document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
        tab.classList.add("active");
        selectedTab = tab.dataset.tab;
        await renderMain();
      }});
    }});
    loadState();
  </script>
</body>
</html>"""


def _read_safe_artifact(base_dir: Path, artifact: str) -> str:
    relative = Path(artifact)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe artifact path")
    path = (base_dir / relative).resolve()
    base = base_dir.resolve()
    if base != path and base not in path.parents:
        raise ValueError("unsafe artifact path")
    return path.read_text(encoding="utf-8")
