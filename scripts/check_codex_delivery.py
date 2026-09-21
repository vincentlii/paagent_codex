"""Real CLI + real MCP, synthetic localhost Responses server; no model or API key."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker
from pa_agent.agent_runtime.execution_contract import ANALYSIS_EXECUTION_POLICY, INSTRUCTION_DELIVERY_POLICY
from pa_agent.agent_runtime.frozen_job import AgentJobBudget, FrozenAgentJobBuilder, FrozenAgentJobStore
from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer

AGENT_MARKER = "SYNTHETIC_POLICY_62AC_READONLY"
EVIDENCE_MARKER = "SYNTHETIC_KNOWLEDGE_82EF_COMPLETE"


def request_tools(request):
    # Responses-lite carries declarations in additional_tools input items.
    declarations = request.get("tools", []) + [tool for item in request.get("input", [])
        if item.get("type") == "additional_tools" for tool in item.get("tools", [])]
    def leaves(items):
        for tool in items:
            if tool.get("type") == "namespace":
                yield from leaves(tool.get("tools", []))
            else:
                yield tool
    return list(leaves(declarations))


def instruction_texts(request):
    return [request.get("instructions") or ""] + [part.get("text", "")
        for item in request.get("input", []) if item.get("role") in {"system", "developer"}
        for part in item.get("content", []) if isinstance(part, dict)]


def probe(model_catalog: Path, codex_binary: str, legacy_project_docs: bool = False,
          *, inspect_tools: bool = False, tool_mode: str | None = None,
          enable_code_mode: bool = False, inspect_code_tools: bool = False,
          model: str = "deepseek-flash", session_continuation: bool = False):
    requests = []
    code_transport = model != "deepseek-flash"
    if session_continuation and not code_transport:
        raise ValueError("Session continuation probe requires an OpenAI Codex model")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            sequence = len(requests)
            if inspect_code_tools and len(requests) == 1:
                item = {"type": "custom_tool_call", "id": f"fc_local_{sequence}", "call_id": f"call_local_{sequence}",
                        "name": "exec", "input": "text(ALL_TOOLS.map(t => t.name))", "status": "completed"}
            elif inspect_tools or inspect_code_tools:
                item = {"type": "message", "id": "msg_local", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}]}
            elif code_transport and (
                len(requests) == 1
                or session_continuation and len(requests) == 3
            ):
                item = {"type": "custom_tool_call", "id": f"fc_local_{sequence}", "call_id": f"call_local_{sequence}",
                        "name": "exec", "status": "completed", "input":
                        '// @exec: {"max_output_tokens": 204096}\n'
                        'const entry = ALL_TOOLS.find(t => t.name.endsWith("__get_role_baseline"));'
                        'if (!entry) throw new Error("Frozen baseline tool missing");'
                        'const response = await tools[entry.name]({});'
                        'text({tool_names: ALL_TOOLS.map(t => t.name), baseline: response.structuredContent});'}
            elif not code_transport and len(requests) == 1:
                item = {"type": "tool_search_call", "id": "ts_local", "call_id": "search_local",
                        "execution": "client", "arguments": {"query": "+pa_evidence get_role_baseline", "limit": 1},
                        "status": "completed"}
            elif not code_transport and len(requests) == 2:
                item = {"type": "function_call", "id": "fc_local", "call_id": "call_local",
                        "namespace": "mcp__pa_evidence", "name": "get_role_baseline", "arguments": "{}", "status": "completed"}
            else:
                item = {"type": "message", "id": "msg_local", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}]}
            response = {"id": f"resp_{len(requests)}", "object": "response", "status": "completed",
                        "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
            events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                      {"type": "response.output_item.added", "output_index": 0, "item": item},
                      {"type": "response.output_item.done", "output_index": 0, "item": item},
                      {"type": "response.completed", "response": response}]
            data = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with tempfile.TemporaryDirectory(prefix="pa-local-delivery-") as directory:
        root = Path(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            home = root / "home"
            home.mkdir()
            catalog = root / "models.json"
            catalog_data = json.loads(model_catalog.read_bytes())
            catalog_data = {"models": [item for item in catalog_data["models"] if item["slug"] == model]}
            if len(catalog_data["models"]) != 1:
                raise ValueError("Probe catalog must contain the selected model")
            if tool_mode is not None:
                catalog_data["models"][0]["tool_mode"] = tool_mode
            catalog.write_text(json.dumps(catalog_data), encoding="utf-8")
            assets = load_codex_runtime_assets()
            agents_text = assets.agents_text.rstrip() + "\n" + AGENT_MARKER
            job = FrozenAgentJobBuilder(runtime_version="local-probe", agents_text=agents_text,
                skill_documents={"stage1": assets.skill_documents["stage1"]}).build(
                task_id="synthetic", request_id="synthetic:stage1:1", role="stage1", attempt=1,
                snapshot_hash="a" * 64, cutoff_ts_open=1, messages=[], output_schema={"type": "object"},
                fact_projection={"facts": []}, bars=[], knowledge_documents={"probe": EVIDENCE_MARKER},
                allowed_knowledge_ids=("probe",), knowledge_manifest={"modules": [{"knowledge_module_id": "probe",
                    "loading_policy": "mandatory", "load_order": 1}]},
                budget=AgentJobBudget(max_evidence_calls=10, max_evidence_bytes=100000),
                core_context={"task_id": "synthetic", "analysis_execution_policy": ANALYSIS_EXECUTION_POLICY,
                              **({} if legacy_project_docs else {"instruction_delivery_policy": INSTRUCTION_DELIVERY_POLICY}),
                              "frozen_snapshot": {"snapshot_hash": "a" * 64, "cutoff_ts_open": 1}})
            path = FrozenAgentJobStore(root / "jobs").publish(job)
            worker = CodexCliWorker(CodexCliConfig(codex_binary=codex_binary, codex_home=home,
                runtime_root=root / "runtime", outbox_root=root / "outbox", model_catalog=catalog,
                python_executable=sys.executable))
            workspace = (
                worker._session_workspace(path.name)
                if session_continuation
                else root / "workspace"
            )
            schema = worker._materialize_workspace(workspace, job)
            command = worker._command(job=job, job_root=path.parent, workspace=schema.parent,
                schema_path=schema, output_path=root / "result", audit_path=root / "audit",
                model=model, reasoning_effort="high", persist_session=session_continuation)
            local_provider_args = [
                "-c", 'model_provider="local_probe"',
                "-c", 'model_providers.local_probe.name="Local probe"',
                "-c", 'forced_login_method="api"',
                "-c", f"model_catalog_json={json.dumps(str(catalog))}",
                "-c", f'model_providers.local_probe.base_url="http://127.0.0.1:{server.server_port}/v1"',
                "-c", 'model_providers.local_probe.wire_api="responses"',
                "-c", "model_providers.local_probe.requires_openai_auth=false",
                "-c", "model_providers.local_probe.request_max_retries=0",
            ]
            command += local_provider_args
            if enable_code_mode:
                command += ["-c", "features.code_mode_host=true", "-c", "features.multi_agent_v2=false",
                            "-c", "agents.enabled=false",
                            "-c", 'features.code_mode={enabled=true,excluded_tool_namespaces=["functions","clock"]}']
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("CODEX_") and not any(word in key.upper() for word in ("TOKEN", "API_KEY", "SECRET"))}
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, encoding="utf-8", timeout=45)
            continuation = None
            if session_continuation and result.returncode == 0:
                thread_id, _usage = worker._parse_events(result.stdout)
                if not thread_id:
                    raise AssertionError("Stage 1 did not return a resumable thread identity")
                stage2 = FrozenAgentJobBuilder(
                    runtime_version="local-probe",
                    agents_text=agents_text,
                    skill_documents={"stage2": assets.skill_documents["stage2"]},
                ).build(
                    task_id="synthetic",
                    request_id="synthetic:stage2:1",
                    role="stage2",
                    attempt=1,
                    snapshot_hash="a" * 64,
                    cutoff_ts_open=1,
                    messages=[],
                    output_schema={"type": "object"},
                    fact_projection={"facts": []},
                    bars=[],
                    knowledge_documents={"probe": EVIDENCE_MARKER},
                    allowed_knowledge_ids=("probe",),
                    knowledge_manifest={"modules": [{
                        "knowledge_module_id": "probe",
                        "loading_policy": "mandatory",
                        "load_order": 1,
                        "sha256": "synthetic",
                    }]},
                    budget=AgentJobBudget(max_evidence_calls=10, max_evidence_bytes=100000),
                    core_context={
                        "task_id": "synthetic",
                        "analysis_execution_policy": ANALYSIS_EXECUTION_POLICY,
                        "instruction_delivery_policy": INSTRUCTION_DELIVERY_POLICY,
                        "frozen_snapshot": {"snapshot_hash": "a" * 64, "cutoff_ts_open": 1},
                        "stage1_diagnosis": {"probe": "STAGE2_UPSTREAM_5D31"},
                        "router_manifest": [],
                        "higher_timeframe": {},
                        "pa_session_continuation": {
                            "protocol_version": "pa_session_continuation_v1",
                            "parent_job_digest": path.name,
                            "thread_id": thread_id,
                            "inherited_price_frame": True,
                            "inherited_higher_timeframe": True,
                            "inherited_knowledge_ids": ["probe"],
                        },
                    },
                )
                stage2_path = FrozenAgentJobStore(root / "jobs").publish(stage2)
                stage2_schema = worker._materialize_workspace(
                    workspace, stage2, resume=True
                )
                stage2_command = worker._command(
                    job=stage2,
                    job_root=stage2_path.parent,
                    workspace=workspace,
                    schema_path=stage2_schema,
                    output_path=root / "result-stage2",
                    audit_path=root / "audit-stage2",
                    model=model,
                    reasoning_effort="high",
                    resume_thread_id=thread_id,
                )
                stage2_command += local_provider_args
                stage2_result = subprocess.run(
                    stage2_command,
                    env=env,
                    cwd=workspace,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=45,
                )
                resumed_thread_id, _usage = worker._parse_events(stage2_result.stdout)
                continuation = {
                    "exit": stage2_result.returncode,
                    "same_thread": resumed_thread_id == thread_id,
                    "stderr": stage2_result.stderr[-2000:],
                    "stdout": stage2_result.stdout[-2000:],
                    "job": stage2,
                }
            if inspect_tools or inspect_code_tools:
                summary = {"tool_mode": tool_mode, "enable_code_mode": enable_code_mode,
                           "exit": result.returncode, "requests": len(requests),
                           "tools": [{key: tool[key] for key in ("type", "name", "namespace") if key in tool}
                                     for tool in request_tools(requests[0])] if requests else [],
                           "errors": [event["item"] for line in result.stdout.splitlines()
                                      if (event := json.loads(line)).get("item", {}).get("type") == "error"]}
                if inspect_code_tools:
                    summary["outputs"] = [item for request in requests[-1:] for item in request.get("input", [])
                                          if item.get("type", "").endswith("call_output")]
                print(json.dumps(summary, ensure_ascii=False))
                return summary
            summary = {"legacy_project_docs": legacy_project_docs, "exit": result.returncode, "requests": len(requests),
                       "policy_copies": [json.dumps(item).count(AGENT_MARKER) for item in requests],
                       "knowledge_copies": [json.dumps(item).count(EVIDENCE_MARKER) for item in requests]}
            expected_requests = 4 if session_continuation else 2 if code_transport else 3
            if result.returncode or len(requests) != expected_requests:
                summary.update(stderr=result.stderr[-2000:], stdout=result.stdout[-2000:])
            expected = EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=path.name)._call_tool(
                "get_role_baseline", {})["structuredContent"]
            baseline_request = requests[1:2] if session_continuation else requests[-1:]
            outputs = [item["output"] for request in baseline_request for item in request.get("input", [])
                       if item.get("type") == ("custom_tool_call_output" if code_transport else "function_call_output")]
            try:
                if code_transport:
                    decoded = json.loads(outputs[0][-1]["text"])
                    delivered = decoded["baseline"]
                    summary["only_frozen_evidence_tools"] = set(decoded["tool_names"]) == {
                        "mcp__pa_evidence__" + name for name in FrozenEvidenceGateway(job).available_tool_names()}
                else:
                    delivered = json.loads(outputs[0].split("Output:\n", 1)[-1])
            except (ValueError, IndexError, AttributeError, KeyError, TypeError):
                delivered = None
            summary["complete_baseline_equal"] = delivered == expected
            # Windows write_text uses CRLF; CLI trims the final file newline.
            # Compare the entire policy after line-ending normalization only.
            summary["complete_instructions_equal"] = all(
                any(value.replace("\r\n", "\n") == agents_text for value in instruction_texts(request))
                for request in requests)
            summary["top_level_tools"] = [
                {key: tool[key] for key in ("type", "name") if key in tool}
                for tool in request_tools(requests[0])] if requests else []
            assert result.returncode == 0 and len(requests) == expected_requests, "local protocol probe did not complete"
            if session_continuation:
                stage2_expected = EvidenceMcpServer(
                    FrozenEvidenceGateway(continuation["job"]),
                    job_digest=continuation["job"].manifest.job_digest,
                )._call_tool("get_role_baseline", {})["structuredContent"]
                try:
                    stage2_output = [
                        item["output"]
                        for item in requests[-1].get("input", [])
                        if item.get("type") == "custom_tool_call_output"
                    ][-1]
                    stage2_decoded = json.loads(stage2_output[-1]["text"])
                    stage2_delivered = stage2_decoded["baseline"]
                except (StopIteration, ValueError, IndexError, KeyError, TypeError):
                    stage2_decoded = {}
                    stage2_delivered = None
                summary["stage2_delta_equal"] = stage2_delivered == stage2_expected
                summary["stage2_only_frozen_evidence_tools"] = set(
                    stage2_decoded.get("tool_names", [])
                ) == {
                    "mcp__pa_evidence__" + name
                    for name in FrozenEvidenceGateway(continuation["job"]).available_tool_names()
                }
                summary["same_thread"] = bool(continuation["same_thread"])
                summary["stage2_exit"] = continuation["exit"]
                if not summary["stage2_delta_equal"]:
                    summary["stage2_actual_identity"] = (
                        (stage2_delivered or {}).get("identity")
                        if isinstance(stage2_delivered, dict)
                        else None
                    )
                    summary["stage2_expected_identity"] = stage2_expected.get("identity")
                    summary["stage2_actual_operations"] = [
                        item.get("operation")
                        for item in (stage2_delivered or {}).get("resources", [])
                    ] if isinstance(stage2_delivered, dict) else []
                    summary["stage2_expected_operations"] = [
                        item.get("operation")
                        for item in stage2_expected.get("resources", [])
                    ]
                    print(json.dumps(summary, ensure_ascii=False))
                assert continuation["exit"] == 0 and continuation["same_thread"]
                assert summary["stage2_delta_equal"]
                assert summary["stage2_only_frozen_evidence_tools"]
            else:
                assert summary["policy_copies"] == [2 if legacy_project_docs else 1] * expected_requests
                assert summary["knowledge_copies"] == ([0, 1] if code_transport else [0, 0, 1])
            assert summary["complete_baseline_equal"] and summary["complete_instructions_equal"]
            if code_transport:
                assert summary.get("only_frozen_evidence_tools"), "Unexpected Code Mode tool surface"
                assert {"exec", "wait"} <= {tool.get("name") for tool in request_tools(requests[0])} <= {
                    "exec", "wait", "request_user_input", "request_user_input_async"}, "Unexpected direct tool surface"
            print(json.dumps(summary, ensure_ascii=False))
            return summary
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-catalog", type=Path, required=True, help="Non-secret DeepSeek read-only model catalog")
    parser.add_argument("--codex-binary", default=shutil.which("codex"))
    parser.add_argument("--legacy-project-docs", action="store_true")
    parser.add_argument("--inspect-tools", action="store_true")
    parser.add_argument("--tool-mode", choices=["code_mode_only"])
    parser.add_argument("--enable-code-mode", action="store_true")
    parser.add_argument("--inspect-code-tools", action="store_true")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--session-continuation", action="store_true")
    args = parser.parse_args()
    if not args.codex_binary:
        parser.error("Codex executable is required")
    probe(args.model_catalog, args.codex_binary, args.legacy_project_docs,
          inspect_tools=args.inspect_tools, tool_mode=args.tool_mode,
          enable_code_mode=args.enable_code_mode, inspect_code_tools=args.inspect_code_tools,
          model=args.model, session_continuation=args.session_continuation)
