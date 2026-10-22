#!/usr/bin/env python3
"""Single-operation Evo-Harness tools. No model calls or experiment scheduler.

Invoke with python3 -B evo.py REQUEST.json. Requests/results are JSON. Private
contexts and journals stay outside the exported, data-only skill directory.
"""
import sys
sys.dont_write_bytecode = True

import contextlib
import ast
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import posixpath
import re
import shlex
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request

_inspection_spec = importlib.util.spec_from_file_location(
    "evo_inspection", Path(__file__).with_name("inspection.py"))
inspection = importlib.util.module_from_spec(_inspection_spec)
_inspection_spec.loader.exec_module(inspection)

VERSION = 1
IDENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ident(value):
    require(isinstance(value, str) and IDENT.fullmatch(value), "invalid identifier")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ReflectionBoundaryError(ValueError):
    """An already-observed operation cannot be repaired by rewriting a draft."""
    def __init__(self, message, phase, tool_call_id=None):
        self.phase, self.tool_call_id = phase, tool_call_id
        super().__init__(f"{phase}" + (f" tool_call_id={tool_call_id}" if tool_call_id else "") + ": " + str(message))


class ReflectionRequestError(ValueError):
    """A controlled local-data request was refused without performing it."""


class ReflectionEntryNotReady(ValueError):
    """Only a current safe request or missing feedback observation is repairable."""


class ReflectionDraftError(ValueError):
    """Only the unsealed candidate JSON may be repaired in the same Session."""


def candidate_write_call(output):
    """A native call template for the bound draft, not an executable helper op."""
    require(isinstance(output, str) and Path(output).is_absolute() and ".." not in Path(output).parts,
            "absolute candidate output path required")
    return {"tool": "write_file", "arguments": {"file_path": output,
            "content": "REPLACE_WITH_YOUR_COMPLETE_CANDIDATE_ARRAY_AS_JSON_TEXT"}}


def candidate_contract(task_id, attempt_id, inputs, output):
    """Prepare public evidence identifiers before solving; never learning content."""
    ident(task_id); ident(attempt_id)
    refs = []
    require(isinstance(inputs, list) and inputs and len(set(inputs)) == len(inputs), "unique authorized inputs required")
    for path in inputs:
        require(isinstance(path, str) and Path(path).is_absolute(), "authorized input path must be absolute")
        raw = regular(path).read_bytes()
        refs.append({"path": path, "sha256": digest(raw), "bytes": len(raw)})
    return {"schema": "evo-candidate-contract-v1", "task_id": task_id, "attempt_id": attempt_id,
            "output_path": output, "write_call": candidate_write_call(output),
            "write_instruction": "After complete feedback, replace only write_call.arguments.content with your entire candidate JSON array as text, then call native write_file directly. For an unsealed draft repair, rewrite that same complete array from your own history. If the frozen profile declares native-draft-v1, native edit_file may also modify this exact unsealed output. No Python/shell read-modify-write or read_file.",
            "output_type": "JSON array; [] is an explicit justified empty candidate decision, still sealed for negative feedback",
            "candidate_fields": {"id": {"type": "string identifier", "prefix": task_id + "-" + attempt_id + "-"},
                "task_id": {"const": task_id}, "attempt_id": {"const": attempt_id},
                "lesson": "nonempty string", "trigger": "nonempty string", "scope_hint": "nonempty string",
                "evidence": "nonempty list of evidence objects"},
            "evidence_examples_by_kind": {
                "input": {"kind": "input", "sha256": "EXACT_SHA256_FROM_INPUT_REFS", "claim": "YOUR_SUPPORTED_CLAIM", "epistemic": "observed"},
                "tool": {"kind": "tool", "tool_call_id": "ORIGINAL_SESSION_CALL_ID", "claim": "YOUR_SUPPORTED_CLAIM", "epistemic": "observed"},
                "answer": {"kind": "answer", "claim": "YOUR_SUPPORTED_CLAIM", "epistemic": "observed"},
                "feedback": {"kind": "feedback", "claim": "YOUR_SUPPORTED_CLAIM", "epistemic": "observed"}},
            "evidence_shape": "Each example is one complete evidence object, not a wrapper keyed by its kind. sha256 and tool_call_id are siblings of kind. Replace placeholders; epistemic may be observed or inferred. No extra identifier is required for answer/feedback. An existing extra sha256 on those two kinds is ignored by validation, not additional evidence; do not perform an operation merely to remove it.",
            "input_refs": refs,
            "recovery": {"candidate_draft_invalid": "Use write_call to replace only your own unsealed candidate array, then seal again in this same Session.",
                         "reflection_boundary_violation": "Stop and return the error; already executed operations cannot be repaired or erased."}}


def native_prompt(call):
    """Resolve the exact native task without reconstructing it through a model.

    This is a live registration/sealing check, not a dispatcher. The native
    tool independently reads and hashes the same file before creating a child;
    its original child trace still contains the complete task text.
    """
    require(isinstance(call, dict), "complete native call required")
    if "prompt" in call:
        require("prompt_file" not in call and "prompt_sha256" not in call,
                "native task must use inline prompt or a hashed prompt file, never both")
        text = call["prompt"]
    else:
        name, expected = call.get("prompt_file"), call.get("prompt_sha256")
        require(isinstance(name, str) and Path(name).is_absolute(), "absolute native prompt_file required")
        require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected),
                "native prompt_file needs its exact SHA256")
        path = regular(name)
        require(path == path.resolve() and path.name.lower() not in (".vault.toml", ".project_config.toml"),
                "native prompt file must be a canonical non-secret regular file")
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024 and digest(raw) == expected, "native prompt file size or SHA256 changed")
        text = raw.decode("utf-8")  # Preserve BOM, CRLF and Unicode exactly.
    require(isinstance(text, str) and text.strip() and "\0" not in text, "complete native task text required")
    return text


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode()


def read(path):
    return json.loads(Path(path).read_text())


def write_once(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".evo-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def regular(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), f"not a regular file: {path}")
    return path


def filemap(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "invalid directory")
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "symlinks are forbidden")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
        else:
            require(path.is_dir(), "non-regular entry")
    return result


def finite(value, low=0, high=None):
    require(type(value) in (int, float) and math.isfinite(value) and value >= low
            and (high is None or value <= high), "invalid numeric value")
    return value


def nonempty(value, label):
    require(isinstance(value, str) and value.strip(), f"missing {label}")
    return value


def feedback(value, task, attempt, answer_sha):
    require(value["task_id"] == task and value["attempt_id"] == attempt,
            "feedback task/attempt mismatch")
    require(value["answer_sha256"] == answer_sha, "feedback belongs to another answer")
    require(value["status"] in ("ok", "error"), "invalid feedback status")
    if value["status"] == "error":
        require(value.get("score") is None and value.get("correct") is None,
                "failed judge cannot supply a score")
        return False
    finite(value["score"], high=1)
    require(type(value["correct"]) is bool, "correct must be a boolean")
    raw = json.loads(value["raw_response"])
    if value.get("source") == "penguin-evaluation":
        require(raw["protocol_version"] == 1 and raw["status"] == "ok", "invalid evaluator receipt")
        require(raw["case_id"] == task, "evaluation case mismatch")
        require(raw["score"] / 100 == value["score"] and (raw["score"] == 100) == value["correct"],
                "evaluation normalization mismatch")
        require(set(value["evaluation_identity"]) == {"agent_id", "expected_version", "provider", "model_id", "thinking_level", "run"},
                "incomplete evaluation identity")
        for key, expected in value["evaluation_identity"].items():
            require(raw[key] == expected, "evaluation identity mismatch")
        return not value["correct"]
    require(value["http_status"] == 200, "feedback did not succeed")
    require(raw["score"] == value["score"] and raw["correct"] == value["correct"],
            "normalized feedback differs from raw response")
    return not value["correct"] or value["score"] < 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def judge_paths(training, task, attempt):
    """Derive identities from the installed training profile, never caller output paths."""
    config = training["config_sha256"]
    require(isinstance(config, str) and re.fullmatch(r"[0-9a-f]{64}", config), "invalid frozen config hash")
    require(task in training["train_ids"] and attempt == training["attempt_id"], "unregistered training attempt")
    paths = {key: Path(training[key]) for key in ("private_learning", "answer_dir", "feedback_dir")}
    for path in paths.values():
        require(path.is_absolute() and str(path) == str(path.resolve()), "training paths must be canonical absolute paths")
    require(paths["feedback_dir"].is_relative_to(paths["private_learning"]), "feedback outside private learning")
    name = f"{ident(task)}.{ident(attempt)}.json"
    return {"answer": str(paths["answer_dir"] / f"{task}.json"),
            "output": str(paths["feedback_dir"] / config / name),
            "judge_request": str(paths["private_learning"] / "judge-requests" / config / name),
            "training_snapshot": str(paths["private_learning"] / "frozen-training.json")}


def validate_judge_request(req, training, request_path):
    require(req["op"] == "judge" and req["split"] == "train", "train-only judge")
    require(req["train_ids"] == training["train_ids"], "training roster changed")
    paths = judge_paths(training, req["task_id"], req["attempt_id"])
    require(str(request_path) == paths["judge_request"], "noncanonical judge request")
    for key in ("answer", "output"):
        require(req[key] == paths[key], f"noncanonical judge {key}")
    url = urllib.parse.urlparse(req["url"])
    require(url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
            and url.path == "/api/judge" and not url.query and not url.fragment
            and not url.username and not url.password, "judge must be the local training API")
    return paths


def judge(req, request_path=None):
    """One authorized training attempt. Failure stays an error, never a zero."""
    task, attempt = ident(req["task_id"]), ident(req["attempt_id"])
    helper = regular(__file__).resolve()
    training_path = helper.parent.parent / "training.json"
    training_raw = regular(training_path).read_bytes()
    training = json.loads(training_raw)
    paths = validate_judge_request(req, training, request_path or judge_paths(training, task, attempt)["judge_request"])
    request_raw = regular(paths["judge_request"]).read_bytes()
    require(json.loads(request_raw) == req, "judge request file differs from operation")
    answer_bytes = regular(req["answer"]).read_bytes()
    answer = json.loads(answer_bytes)
    require(isinstance(answer, dict), "answer must be an object")
    with locked(paths["training_snapshot"] + ".lock"):
        snapshot = Path(paths["training_snapshot"])
        if snapshot.exists():
            require(regular(snapshot).read_bytes() == training_raw, "installed training changed")
        else:
            write_once(snapshot, training_raw)
    identity = {"source": "evo-judge", "config_sha256": training["config_sha256"],
                "training_path": str(training_path), "training_sha256": digest(training_raw),
                "training_snapshot": paths["training_snapshot"],
                "helper_path": str(helper), "helper_sha256": digest(helper.read_bytes()),
                "judge_request_path": paths["judge_request"], "judge_request_sha256": digest(request_raw),
                "feedback_path": paths["output"]}
    dest = Path(paths["output"])
    with locked(str(dest) + ".lock"):
        if dest.exists():
            previous = read(regular(dest))
            require(previous["task_id"] == task and previous["attempt_id"] == attempt
                    and previous["answer_sha256"] == digest(answer_bytes)
                    and all(previous.get(k) == v for k, v in identity.items()), "attempt identity conflict")
            # Even an interrupted HTTP call is never repeated silently.
            return previous
        if training.get("guard_version") == 2 and training.get("reflection_mode") == "solver-session":
            readiness = judge_readiness(training, task, paths["judge_request"])
            if not readiness["ready"]:
                return readiness
        record = {**identity, "task_id": task, "attempt_id": attempt, "answer_sha256": digest(answer_bytes),
                  "status": "error", "error": "judge call in flight or interrupted", "score": None,
                  "correct": None, "url": req["url"], "request": {"task_id": task, "answer": answer}}
        atomic(dest, encoded(record))
        request = urllib.request.Request(req["url"], data=encoded(record["request"]),
                                         headers={"Content-Type": "application/json"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=30) as response:
                raw = response.read().decode()
                record.update(raw_response=raw, http_status=response.status)
            parsed = json.loads(raw)
            finite(parsed["score"], high=1)
            require(type(parsed["correct"]) is bool, "missing boolean correctness")
            require(record["http_status"] == 200, "judge HTTP failure")
            record.update(status="ok", score=parsed["score"], correct=parsed["correct"])
            record.pop("error")
        except (OSError, ValueError, KeyError) as exc:
            record["error"] = str(exc)
        atomic(dest, encoded(record))
        return record


def judge_stdout(record):
    """Bounded CLI receipt; the canonical ledger retains the complete feedback."""
    if record.get("op") == "judge-readiness":
        return record
    raw = encoded(record)
    return {"source": "evo-judge-receipt", "feedback_path": record["feedback_path"],
            "sha256": digest(raw), "bytes": len(raw),
            **{key: record[key] for key in ("task_id", "attempt_id", "config_sha256", "status", "score", "correct")}}


def prepare_judge_request(req):
    """Write the fixed request, without submitting an answer or contacting judge."""
    helper = regular(__file__).resolve()
    training = read(helper.parent.parent / "training.json")
    task = ident(req["task_id"])
    paths = judge_paths(training, task, training["attempt_id"])
    request = {"op": "judge", "split": "train", "train_ids": training["train_ids"],
               "task_id": task, "attempt_id": training["attempt_id"],
               "answer": paths["answer"], "output": paths["output"], "url": req["url"]}
    validate_judge_request(request, training, paths["judge_request"])
    path = Path(paths["judge_request"])
    with locked(str(path) + ".lock"):
        if path.exists():
            require(read(regular(path)) == request, "canonical judge request already differs")
        else:
            write_once(path, encoded(request))
    return {"op": "prepare-judge-request", "task_id": task, "attempt_id": training["attempt_id"],
            "judge_request": str(path), "request_sha256": digest(path.read_bytes()),
            "command": shlex.join(["python3", "-B", str(helper), str(path)]),
            "model_calls": 0, "judge_calls": 0}


def trace_view(paths, expected, path_map=None, active_call=None):
    """Preserve complete action/result pairs, with raw sources and no head/tail cut."""
    messages, sources, metas, native_rows = [], [], [], []
    for path in paths:
        raw = regular((path_map or {}).get(str(path), path)).read_bytes()
        sources.append({"path": str(path), "sha256": digest(raw), "bytes": len(raw)})
        for line_no, line in enumerate(raw.splitlines(), 1):
            row = json.loads(line)
            native_rows.append((str(path), line_no, row))
            require(not row.get("origin"), "use original child trace, not forwarded duplicates")
            payload = row.get("payload", {})
            if row.get("type") == "session_meta":
                metas.append(payload)
            elif row.get("type") == "model_msg" and payload.get("type") in (
                    "text", "tool_call", "tool_call_output"):
                messages.append({"source": str(path), "line": line_no, "payload": payload})
    require(metas, "trace missing session_meta")
    for meta in metas:
        for key in ("session_id", "provider", "model_id", "workspace", "agent_state", "source"):
            require(meta.get(key) == expected[key], f"trace binding mismatch: {key}")
    require(expected["source"] in ("cli", "benchmark", "subagent"), "unsupported trace source")
    if expected["source"] == "subagent":
        parent_path = (path_map or {}).get(expected["parent_trace"], expected["parent_trace"])
        parent = [json.loads(line) for line in regular(parent_path).read_bytes().splitlines()]
        require(any(row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "subagent"
                    and row["payload"].get("session_id") == expected["session_id"] for row in parent),
                "child not referenced by parent trace")
    calls, results = {}, {}
    for message in messages:
        p = message["payload"]
        if p["type"] == "tool_call":
            key = p["tool_call_id"]
            require(key not in calls, "duplicate tool call")
            calls[key] = message
        elif p["type"] == "tool_call_output":
            key = p["tool_call_id"]
            require(key not in results, "duplicate tool output")
            results[key] = message
    if active_call is not None:
        require(set(calls) - set(results) == {active_call} and not (set(results) - set(calls)),
                "only the current seal call may lack its tool result")
        require(messages[-1] == calls[active_call], "current seal must be the last native action")
        del calls[active_call]
    require(set(calls) == set(results), "incomplete action/observation pairing")
    return {"binding": expected, "sources": sources, "truncated": False, "metas": metas,
            "denied_calls": [row["payload"]["tool_call_id"] for i, (_, _, row) in enumerate(native_rows)
                if row.get("payload", {}).get("type") == "tool_call" and native_dispatch_was_denied(native_rows, i, row["payload"]["tool_call_id"])],
            "messages": messages, "pairs": [{"call": calls[k], "observation": results[k]} for k in calls]}


def json_values(text):
    """Parse JSON objects in wrapped stdout once, without scanning their string data.

    In particular a read-material receipt can contain JSON examples as escaped
    text. Those are material, not additional tool results or judge responses.
    """
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        if text[i] in "{[":
            try:
                parsed, end = decoder.raw_decode(text, i)
                yield parsed
                i = end
                continue
            except ValueError:
                pass
        i += 1


def contains_json(text, value):
    """Tool output may wrap stdout with an exit-code header; never substring-score."""
    return any(parsed == value for parsed in json_values(text))


def python_judge_probe(source):
    """Recognize visible Python HTTP calls using the judge URL, not JSON writers.

    This is a cooperative trace audit, not a Python/network security sandbox.
    Literal endpoints and simple local assignments are enough to distinguish
    the actual CRM request writer from urllib/requests calls to judge.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    assigned = {}
    imports = {"urllib", "requests", "httpx", "aiohttp", "http"}
    imported_functions = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.asname or alias.name.split(".")[0] for alias in node.names
                           if alias.name.split(".")[0] in {"urllib", "requests", "httpx", "aiohttp", "http"})
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {"urllib", "requests", "httpx", "aiohttp", "http"}:
            imports.update(alias.asname or alias.name for alias in node.names)
            imported_functions.update({alias.asname or alias.name: alias.name for alias in node.names})
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and node.value is not None:
                    assigned.setdefault(target.id, []).append(node.value)
    def endpoint(node, seen=frozenset()):
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str) and "/api/judge" in node.value
        if isinstance(node, ast.Name) and node.id not in seen:
            return any(endpoint(value, seen | {node.id}) for value in assigned.get(node.id, []))
        return any(endpoint(child, seen) for child in ast.iter_child_nodes(node)) if not isinstance(node, ast.Name) else False
    def network_owner(node, seen=frozenset()):
        if isinstance(node, ast.Name):
            if node.id in imports:
                return True
            return node.id not in seen and any(network_owner(value, seen | {node.id}) for value in assigned.get(node.id, []))
        return any(network_owner(child, seen) for child in ast.iter_child_nodes(node))
    network = {"urlopen", "urlretrieve", "request", "get", "post", "put", "patch", "delete", "send", "open"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if isinstance(node.func, ast.Name):
            name = imported_functions.get(name, name)
        # opener.open(Request(...)) is HTTP, whereas open(path, 'w') and
        # Path(...).open() in a JSON writer are ordinary filesystem operations.
        known = name in ("urlopen", "urlretrieve") or network_owner(node.func)
        # A business request may serialize a judge URL as data. Only the URL
        # argument of the actual network operation identifies its destination.
        url_index = 1 if name == "request" else 0
        urls = [node.args[url_index]] if len(node.args) > url_index else []
        urls += [k.value for k in node.keywords if k.arg in ("url", "fullurl")]
        if name in network and known and any(endpoint(arg) for arg in urls):
            return True
    return False


def direct_judge_probe(command):
    """Reject recognizable HTTP execution; a URL in data/docs is not execution."""
    if "/api/judge" not in command:
        return False
    # Remove heredoc data from shell tokens. Python bodies are parsed as code;
    # cat/tee bodies are data, even if they contain endpoint names or examples.
    lines, shell_lines, index = command.splitlines(), [], 0
    while index < len(lines):
        line = lines[index]
        match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if not match:
            shell_lines.append(line)
            index += 1
            continue
        end = index + 1
        while end < len(lines) and lines[end].strip() != match[2]:
            end += 1
        body = "\n".join(lines[index + 1:end])
        header = line[:match.start()]
        if re.search(r"(?:^|[\s;&|])(?:\S*/)?python[0-9.]*\s", header) and python_judge_probe(body):
            return True
        if re.search(r"(?:^|[\s;&|])(?:\S*/)?(?:ba|z|da)?sh\s", header) and direct_judge_probe(body):
            return True
        shell_lines.append(header)
        index = min(end + 1, len(lines))
    try:
        lexer = shlex.shlex("\n".join(shell_lines), posix=True, punctuation_chars=";&|<>()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    segments, segment = [], []
    for token in tokens + [";"]:
        if token and all(c in ";&|()\n" for c in token):
            if segment:
                segments.append(segment)
                segment = []
        else:
            segment.append(token)
    variables = {}
    def expand(value):
        return re.sub(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
                      lambda match: variables.get(match[1] or match[2], match[0]), value)
    for args in segments:
        i = 0
        while i < len(args) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[i]):
            key, value = args[i].split("=", 1)
            variables[key] = expand(value)
            i += 1
        # Recognize only executable positions. `echo curl URL` is data, while
        # ordinary env/sudo/exec wrappers still execute the following command.
        while i < len(args) and Path(args[i]).name in ("env", "sudo", "exec"):
            wrapper = Path(args[i]).name
            i += 1
            while i < len(args):
                if args[i] == "--":
                    i += 1
                    break
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[i]):
                    key, value = args[i].split("=", 1)
                    variables[key] = expand(value)
                    i += 1
                elif args[i].startswith("-"):
                    option = args[i]
                    i += 1
                    if option in (("-u", "--unset", "-C", "--chdir") if wrapper == "env" else ("-u", "--user", "-g", "--group")):
                        i += 1
                else:
                    break
        if i >= len(args):
            continue
        binary = Path(args[i]).name
        if binary == "export":
            for argument in args[i + 1:]:
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argument):
                    key, value = argument.split("=", 1)
                    variables[key] = expand(value)
        if binary in ("curl", "wget", "http", "https"):
            skip = False
            for arg in args[i + 1:]:
                if skip:
                    skip = False
                    continue
                if arg in ("-d", "--data", "--data-raw", "--data-binary", "--json", "-H", "--header", "-o", "--output", "--post-data"):
                    skip = True
                    continue
                arg = expand(arg)
                if arg.startswith(("http://", "https://", "--url=http://", "--url=https://")) and "/api/judge" in arg:
                    return True
        if re.fullmatch(r"python[0-9.]*", binary) and i + 2 < len(args) and args[i + 1] == "-c":
            if python_judge_probe(args[i + 2]):
                return True
        if binary in ("sh", "bash", "zsh", "dash") and i + 2 < len(args) and args[i + 1] in ("-c", "-lc"):
            if direct_judge_probe(args[i + 2]):
                return True
        if binary in ("node", "nodejs") and i + 2 < len(args) and args[i + 1] in ("-e", "--eval"):
            if re.search(r"\b(?:fetch|axios\.(?:post|get))\s*\(\s*['\"][^'\"]*/api/judge", args[i + 2]):
                return True
    return False


def verify_judge_evidence(view, fb, request_path, path_map=None, *, feedback_complete=True):
    """Audit cooperative execution, not an arbitrary-code security sandbox.

    A score must be returned by the one exact helper command. Visible raw HTTP
    probes, repeated helper executions and echoed/fabricated receipts invalidate
    the attempt. Obfuscated code/network activity needs the runtime boundary.
    """
    mapped = path_map or {}
    require(fb.get("source") == "evo-judge", "feedback lacks canonical judge receipt")
    training_raw = regular(mapped.get(fb["training_path"], fb["training_path"])).read_bytes()
    require(digest(training_raw) == fb["training_sha256"], "frozen training evidence changed")
    training = json.loads(training_raw)
    request_raw = regular(mapped.get(request_path, request_path)).read_bytes()
    require(request_path == fb["judge_request_path"] and digest(request_raw) == fb["judge_request_sha256"],
            "judge request evidence changed")
    req = json.loads(request_raw)
    paths = validate_judge_request(req, training, request_path)
    helper = str(Path(fb["training_path"]).parent / "scripts/evo.py")
    require(fb["helper_path"] == helper and fb["helper_sha256"] == digest(regular(__file__).read_bytes()),
            "judge helper differs from trusted implementation")
    require(fb["config_sha256"] == training["config_sha256"] and
            fb["training_snapshot"] == paths["training_snapshot"] and fb["feedback_path"] == paths["output"],
            "judge receipt differs from frozen identity")
    require(req["task_id"] == fb["task_id"] and req["attempt_id"] == fb["attempt_id"]
            and req["url"] == fb["url"] and fb["request"]["task_id"] == req["task_id"], "judge request/receipt mismatch")
    ledger_raw = regular(mapped.get(fb["feedback_path"], fb["feedback_path"])).read_bytes()
    require(ledger_raw == encoded(fb), "canonical feedback ledger changed")
    expected = ["python3", "-B", helper, request_path]
    matched = []
    chunked = []
    for pair in view["pairs"]:
        call, observation = pair["call"]["payload"], pair["observation"]["payload"]["output"]
        if call["tool_call_id"] in view.get("denied_calls", []):
            continue
        authorized = False
        receipt_read = call["name"] == "read_file"
        values = list(json_values(observation))
        if call["name"] == "exec_command":
            args = json.loads(call["arguments"])
            command = args.get("cmd", "")
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = []
            authorized = (tokens == expected and not any(c in command for c in "`$\n\r;&|<>")
                          and not args.get("run_in_background", False))
            require(not direct_judge_probe(command), "direct judge probe in Solver trace")
            # The canonical helper's read-material output is data observation,
            # not a second score. It must exactly bind the frozen ledger bytes.
            if tokens[:3] == expected[:3] and len(tokens) == 4 and not any(c in command for c in "`$\n\r;&|<>"):
                for value in values:
                    if not isinstance(value, dict) or value.get("material_path") != fb["feedback_path"]:
                        continue
                    offset = value.get("offset")
                    if type(offset) is int and 0 <= offset <= len(ledger_raw.decode()):
                        text = ledger_raw.decode()
                        receipt_read = value == {"material_path": fb["feedback_path"], "sha256": digest(ledger_raw),
                            "offset": offset, "end": min(offset + 4000, len(text)), "total": len(text),
                            "text": text[offset:offset + 4000]}
            receipt_read = receipt_read or tokens in (["cat", fb["feedback_path"]], ["cat", "--", fb["feedback_path"]])
            if not authorized and helper in tokens:
                # A second helper request is detectable locally when its file is
                # available. Native hooks must also reject it before execution.
                other_path = tokens[-1] if tokens else ""
                other = Path(mapped.get(other_path, other_path))
                if other.is_file():
                    try:
                        other_req = read(other)
                    except (ValueError, OSError):
                        other_req = None
                    require(not isinstance(other_req, dict) or other_req.get("op") != "judge",
                            "noncanonical or repeated judge operation")
        if authorized:
            if is_unready_judge(pair, view, training, fb["task_id"], path_map):
                continue
            if fb not in values:
                require(judge_stdout(fb) in values, "judge command did not return bound receipt")
                chunked.append(pair)
            matched.append(call["tool_call_id"])
        elif receipt_read:
            # Reading API documentation or an existing receipt is not another
            # scoring request, and cannot satisfy the canonical command below.
            continue
        else:
            require(fb not in values and judge_stdout(fb) not in values,
                    "judge receipt was not produced by canonical exec")
            # Business APIs can legitimately expose score/correct fields. Only
            # explicit method receipts are scoring evidence; raw judge traffic
            # is identified by the executing command, not response key names.
            require(not any(isinstance(value, dict) and value.get("source") in ("evo-judge", "evo-judge-receipt")
                            for value in values), "additional judge feedback in Solver trace")
    require(len(matched) == 1, "requires exactly one canonical judge operation")
    for pair in chunked if feedback_complete else []:
        # Live pre-tool checks must allow the reads that establish this
        # completion; final sealing still requires every raw-feedback byte.
        # Complete feedback must be observed after the one judge has returned;
        # never rerun judge to recover an output-length truncation.
        positions = {(m["source"], m["line"]): i for i, m in enumerate(view["messages"])}
        observation = pair["observation"]
        returned = positions[(observation["source"], observation["line"])]
        later = [p for p in view["pairs"] if positions[(p["call"]["source"], p["call"]["line"])] > returned]
        verify_reads({**view, "pairs": later}, [fb["feedback_path"]], path_map)
    return {"path": request_path, "sha256": digest(request_raw), "tool_call_id": matched[0]}


def material(req):
    path = regular(req["path"])
    raw = path.read_bytes()
    content = raw.decode()
    start = req.get("offset", 0)
    require(type(start) is int and 0 <= start <= len(content), "invalid material offset")
    end = min(start + 4000, len(content))
    return {"material_path": str(path), "sha256": digest(raw), "offset": start,
            "end": end, "total": len(content), "text": content[start:end]}


def observation_positions(view):
    positions = {(m["source"], m["line"]): index for index, m in enumerate(view.get("messages", []))}
    def at(message, fallback):
        return positions.get((message.get("source"), message.get("line")), fallback)
    return [(at(pair["call"], 2 * index), at(pair["observation"], 2 * index + 1))
            for index, pair in enumerate(view["pairs"])]


def helper_observation(pair, view):
    """A read/digest receipt must come from an executed installed method helper.

    Native Solver traces legitimately batch reads in shell loops or write a
    request in a heredoc before executing the helper. Preserve those forms;
    reject print-only receipts and helper names merely quoted as data. This is
    still a cooperative execution audit, not protection against arbitrary
    mixed shell programs that forge their own stdout.
    """
    call = pair["call"].get("payload", {})
    if call.get("name") != "exec_command":
        return False
    args = json.loads(call["arguments"])
    command = args.get("cmd", args.get("command", ""))
    allowed = {str(Path(__file__).resolve())}
    binding = view.get("binding", {})
    if binding.get("agent_state"):
        allowed.add(str(Path(binding["agent_state"]) / "skills/evo-harness-rsi-toolkit/scripts/evo.py"))
    lines, shell, i = command.splitlines(), [], 0
    while i < len(lines):
        match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", lines[i])
        if not match:
            shell.append(lines[i])
            i += 1
            continue
        shell.append(lines[i][:match.start()])
        i += 1
        while i < len(lines) and lines[i].strip() != match[2]:
            i += 1
        i += 1
    try:
        lexer = shlex.shlex("\n".join(shell), posix=True, punctuation_chars=";&|\n")
        lexer.whitespace, lexer.whitespace_split = " \t\r", True
        fragments, fragment = [], []
        for token in list(lexer) + [";"]:
            if token and all(char in ";&|\n" for char in token):
                fragments.append(fragment)
                fragment = []
            else:
                fragment.append(token)
    except ValueError:
        return False
    for argv in fragments:
        if argv and argv[0] in ("do", "then"):
            argv = argv[1:]
        if len(argv) == 4 and argv[:2] == ["python3", "-B"] and argv[2] in allowed:
            return True
    return False


def material_read_status(view, records, standalone=False):
    # Build this once per trace. Searching every output anew for every chunk
    # made a one-megabyte captured context take seconds to audit, and five
    # contexts could exceed the native hook timeout during sealing/freezing.
    indexed = {}
    positions = observation_positions(view)
    for pair, (_, observed) in zip(view["pairs"], positions):
        if not (standalone_helper_request(pair, view) if standalone else helper_observation(pair, view)):
            continue
        values = list(json_values(pair["observation"]["payload"]["output"]))
        if standalone and len(values) != 1:
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            key = (value.get("material_path"), value.get("sha256"), value.get("offset"))
            if isinstance(key[0], str) and isinstance(key[1], str) and type(key[2]) is int:
                indexed.setdefault(key, []).append((value, observed))
    receipts, missing = [], []
    last_read = -1
    for path, content, fingerprint in records:
        # Complete fixed-size chunks must be present as actual tool outputs.
        for offset in range(0, max(len(content), 1), 4000):
            end = min(offset + 4000, len(content))
            expected = {"material_path": str(path), "sha256": fingerprint, "offset": offset,
                        "end": end, "total": len(content), "text": content[offset:end]}
            observed = [position for value, position in indexed.get((str(path), fingerprint, offset), []) if value == expected]
            if not observed:
                missing.append({"path": str(path), "offset": offset})
            else:
                last_read = max(last_read, min(observed))
        receipts.append({"path": str(path), "sha256": fingerprint})
    return receipts, last_read, missing


def verify_material_records(view, records, standalone=False):
    receipts, last_read, missing = material_read_status(view, records, standalone)
    require(not missing, "material not fully read: " + (f"{missing[0]['path']} offset {missing[0]['offset']}" if missing else ""))
    return receipts, last_read


def material_records(paths, path_map=None):
    records = []
    for path in paths:
        raw = regular((path_map or {}).get(str(path), path)).read_bytes()
        records.append((str(path), raw.decode(), digest(raw)))
    return records


def verify_reads(view, paths, path_map=None):
    return verify_material_records(view, material_records(paths, path_map))[0]


def native_read_count(value, default):
    """The native read_file count coercion (JS Number/floor), without eval."""
    if value is None:
        return default
    if isinstance(value, str):
        # ECMAScript whitespace; Python strip additionally removes U+0085.
        value = value.strip(" \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004"
                            "\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")
        try:
            if re.fullmatch(r"0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+", value):
                value = float(int(value, 0))
            elif re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
                value = float(value)
            else:
                return None
        except (ValueError, OverflowError):
            return None
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
        return math.floor(number) if math.isfinite(number) else None
    except (ValueError, OverflowError):
        return None


def native_file_read(view, path, content, before_call_id=None):
    """Prove complete public text from exact original native read_file windows.

    Match the frozen Penguin renderer, not numbered text found inside arbitrary
    outputs. Ordinary pagination is allowed; truncated windows, long lines and
    scan-cap files still need read-material. This is intentionally used only for
    Solver public inputs, never feedback or sealed learning-role materials.
    """
    workspace = view.get("binding", {}).get("workspace", "")
    if not posixpath.isabs(workspace) or not posixpath.isabs(path):
        return None
    if "\0" in content or len(content.encode()) >= 8 * 1024 * 1024:
        return None
    lines = content.split("\n")
    if content.endswith("\n") or not content:
        lines.pop()
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    if any(len(line.encode("utf-16-le")) // 2 > 2000 for line in lines):
        return None
    rows = [f"{number:6}\t{line}" for number, line in enumerate(lines, 1)]
    positions = observation_positions(view)
    boundary = None
    if before_call_id is not None:
        boundaries = [called for pair, (called, _) in zip(view["pairs"], positions)
                      if pair["call"]["payload"].get("tool_call_id") == before_call_id]
        if len(boundaries) != 1:
            return None
        boundary = boundaries[0]
    covered, empty_at = {}, None
    for pair, (called, observed) in zip(view["pairs"], positions):
        call, result = pair["call"]["payload"], pair["observation"]["payload"]
        if (call.get("name") != "read_file" or result.get("stop_reason") != "completed"
                or result.get("images") or called >= observed
                or boundary is not None and observed >= boundary):
            continue
        try:
            args = json.loads(call["arguments"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        target = args.get("file_path")
        if not isinstance(target, str) or not target or "\0" in target or re.match(r"https?://", target, re.I):
            continue
        # Container source paths are lexical POSIX identities, including during
        # host relocation. Never resolve them through the reviewing host's FS.
        # Node path.resolve collapses double-leading slashes; normpath alone
        # preserves exactly two on POSIX Python.
        actual = "/" + posixpath.normpath(posixpath.join(workspace, target)).lstrip("/")
        expected_path = "/" + posixpath.normpath(path).lstrip("/")
        if actual != expected_path:
            continue
        offset = native_read_count(args.get("offset"), 1)
        limit = native_read_count(args.get("limit"), 2000)
        if offset is None or limit is None or limit <= 0:
            continue
        offset = max(1, offset)
        if not rows:
            if result.get("output") == f'"{target}" is an empty file (0 lines).':
                empty_at = observed if empty_at is None else min(empty_at, observed)
            continue
        if offset > len(rows):
            continue
        end = min(offset + limit - 1, len(rows))
        expected = rows[offset - 1:end]
        if "\r\n" in content:
            expected = expected + ["(file uses CRLF line endings)"]
        if end < len(rows):
            expected = expected + [f"[file has {len(rows)} lines total; showing {offset}-{end} — call again with offset to continue]"]
        if result.get("output") != "\n".join(expected):
            continue
        for number in range(offset, end + 1):
            covered[number] = min(covered.get(number, observed), observed)
    if not rows:
        return empty_at
    return max(covered.values()) if len(covered) == len(rows) else None


def verify_solver_skill(view, fb, path_map=None):
    """Guarded continuous Solvers must observe cold H0 before task actions.

    Call only after verify_judge_evidence has bound the profile, judge and raw
    trace. Recreate the authorized empty package from the frozen profile; do
    not trust current State, a copied receipt, or an arbitrary displayed file.
    """
    mapped = path_map or {}
    snapshot = fb["training_snapshot"]
    raw_profile = regular(mapped.get(snapshot, snapshot)).read_bytes()
    require(digest(raw_profile) == fb["training_sha256"], "frozen training snapshot changed")
    training = json.loads(raw_profile)
    if training.get("guard_version") != 2:
        return None
    return solver_skill_observation(view, training, fb["task_id"], fb["attempt_id"])


def solver_skill_observation(view, training, task_id, attempt_id, allow_missing=False):
    expected = render_skill([], training["max_bytes"])
    if training.get("context_transport") == CONTEXT_TRANSPORT:
        metas = view.get("metas", [])
        observed = context_observation(metas, {"SKILL.md": expected})
        return {"path": view["binding"]["agent_state"] + "/skills/evo-harness-learned/SKILL.md",
                "sha256": digest(expected), "bytes": len(expected), "observed_at": -1,
                "context_transport": CONTEXT_TRANSPORT, "model_context": observed}
    workspace, state = view["binding"]["workspace"], view["binding"]["agent_state"]
    require(posixpath.isabs(workspace) and posixpath.isabs(state), "Solver native State/workspace must be absolute")
    slot = state.rstrip("/") + "/skills/evo-harness-learned"
    target = slot + "/SKILL.md"
    preparation = {inspection.native_instructions(slot),
                   state.rstrip("/") + "/skills/evo-harness-rsi-toolkit/training.json"}
    grants = []
    for message in view["messages"]:
        payload = message["payload"]
        if payload.get("type") != "text" or payload.get("role") != "user":
            continue
        for value in json_values(payload["text"]):
            grant = value.get("evo_task") if isinstance(value, dict) else None
            if (isinstance(grant, dict) and grant.get("split") == "train"
                    and grant.get("task_id") == task_id and grant.get("attempt_id") == attempt_id):
                grants.append(grant)
    require(grants and all(grant == grants[0] for grant in grants), "Solver needs its unique public input grant")
    public = grants[0].get("inputs")
    require(isinstance(public, list) and public and all(isinstance(path, str) and posixpath.isabs(path) for path in public),
            "Solver public input grant must contain absolute paths")
    public = {inspection._path(path, workspace) for path in public}
    public_input = workspace.rstrip("/") + "/train_tasks/" + task_id + "/input"
    positions = observation_positions(view)
    complete = native_file_read(view, target, expected.decode())
    for pair, (called, observed) in zip(view["pairs"], positions):
        call, output = pair["call"]["payload"], pair["observation"]["payload"]
        if (call.get("name") != "exec_command" or output.get("stop_reason") != "completed"
                or output.get("images") or called >= observed):
            continue
        if inspection.complete_file_stdout(call, output.get("output"), expected, target,
                                           workspace, slot, public, public_input, preparation_files=preparation):
            complete = observed if complete is None else min(complete, observed)
    if not allow_missing:
        require(complete is not None, f"Solver frozen H0 Skill was not fully observed through native read_file or exact foreground cat: missing complete, exact observation of {target}; use the frozen Reader read_calls native read_file calls to supply remaining evidence")
    for pair, (called, _) in zip(view["pairs"], positions):
        if complete is not None and called >= complete:
            continue
        call = pair["call"]["payload"]
        if call.get("name") == "read_file":
            args = json.loads(call["arguments"])
            path = inspection._path(args.get("file_path"), workspace)
            require(path == target or path in public or path in preparation,
                    "only granted public files, this native State's AGENTS.md/training.json or the frozen H0 Skill may be read before its complete observation")
        else:
            require(call.get("name") == "exec_command", "task actions may not precede the complete Solver H0 Skill read")
            try:
                inspection._readonly_commands(call, workspace, slot, public, public_input, preparation_files=preparation)
            except ValueError as exc:
                raise ValueError(f"{exc}; cannot verify pre-read call {call.get('tool_call_id', '<missing>')} under the finite inspection protocol before complete observation of {target}") from exc
    if complete is None:
        require(allow_missing, f"Solver frozen H0 Skill was not fully observed through native read_file or exact foreground cat: missing complete, exact observation of {target}; use the frozen Reader read_calls native read_file calls to supply remaining evidence")
        return None
    return {"path": target, "sha256": digest(expected), "bytes": len(expected), "observed_at": complete}


def solver_input_read_status(view, records, before_call_id=None):
    """Keep helper receipts, with full native reads for granted public files."""
    receipts, last_read, missing = material_read_status(view, records)
    unresolved = {item["path"] for item in missing}
    for path, content, fingerprint in records:
        if path not in unresolved:
            continue
        require(digest(content.encode()) == fingerprint, "public input fingerprint differs")
        observed = native_file_read(view, path, content, before_call_id)
        if observed is not None:
            unresolved.remove(path)
            last_read = max(last_read, observed)
    missing = [item for item in missing if item["path"] in unresolved]
    return receipts, last_read, missing



def verify_solver_inputs(view, records, before_call_id=None):
    receipts, last_read, missing = solver_input_read_status(view, records, before_call_id)
    require(not missing, "material not fully read: " + (f"{missing[0]['path']} offset {missing[0]['offset']}" if missing else ""))
    return receipts, last_read


def standalone_helper_request(pair, view):
    """Recognize one literal foreground helper invocation, without shell evaluation."""
    call = pair["call"].get("payload", {})
    if call.get("name") != "exec_command":
        return None
    args = json.loads(call["arguments"])
    command = args.get("cmd", args.get("command", ""))
    command = command.strip() if isinstance(command, str) else command
    if not isinstance(command, str) or args.get("run_in_background") or any(c in command for c in "$`\n;|&><"):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    allowed = {str(Path(__file__).resolve())}
    state = view.get("binding", {}).get("agent_state")
    if state:
        allowed.add(str(Path(state) / "skills/evo-harness-rsi-toolkit/scripts/evo.py"))
    if (len(argv) == 4 and argv[:2] == ["python3", "-B"] and argv[2] in allowed
            and Path(argv[2]).is_absolute() and Path(argv[3]).is_absolute()):
        return argv[3]
    return None


def role_contract(training, role, task_id=None):
    """Frozen engineering inputs; never chooses or changes learning content."""
    require(training.get("guard_version") == 2, "role sealing requires guarded training")
    require(role in ("reflector", "evolver"), "only learning roles seal outputs")
    private = Path(training["private_learning"])
    same_solver = role == "reflector" and training.get("reflection_mode") == "solver-session"
    role_file = str(private / "roles" / (("solver" if same_solver else role) + ".md"))
    contexts = [str(private / "contexts" / (task + ".json")) for task in training["train_ids"]]
    if role == "reflector":
        require(task_id in training["train_ids"], "role task is outside the frozen batch")
        context = str(private / "contexts" / (task_id + ".json"))
        output = str(private / "roles" / task_id / "candidates.json")
        materials = [role_file, context]
        if same_solver:
            materials = [role_file, judge_paths(training, task_id, training["attempt_id"])["output"]]
        validation = {"op": "validate-candidates", "contexts": [context], "train_ids": [task_id],
                      "candidates": output, "candidate_prefix": task_id + "-" + training["attempt_id"] + "-"}
    else:
        require(task_id is None, "batch Evolver has no task_id")
        output = str(private / "decisions.json")
        materials = [role_file, str(private / "candidates.json"), str(private / "prebatch-library.json"), *contexts]
        validation = {"op": "validate-decisions", "contexts": contexts, "train_ids": training["train_ids"],
                      "candidates": materials[1], "library": materials[2], "decisions": output,
                      **{key: training[key] for key in ("limits", "max_bytes")}}
    contract = {"schema": 1, "role": role, "task_id": task_id, "config_sha256": training["config_sha256"],
                "materials": materials, "output": output, "validation": validation}
    if same_solver:
        contract.update(reflection_mode="solver-session", dispatch_phase="solver",
                        attempt_id=training["attempt_id"], request_dir=str(private / "roles" / task_id / "requests"))
        contract["validation"] = {"op": "seal-role-output", "role": "reflector", "task_id": task_id}
    return contract


def solver_reflection_profile(fb, path_map=None):
    if fb.get("source") != "evo-judge":
        return None
    mapped = path_map or {}
    raw = regular(mapped.get(fb["training_snapshot"], fb["training_snapshot"])).read_bytes()
    require(digest(raw) == fb["training_sha256"], "frozen training snapshot changed")
    profile = json.loads(raw)
    return profile if profile.get("reflection_mode") == "solver-session" else None


def reflection_evidence_value(req, view, training, fb, path_map=None):
    """Read-only transforms of observed pre-judge data; no service calls or writes."""
    def requested(condition, message):
        if not condition:
            raise ReflectionRequestError(message)
    requested(training.get("reflection_evidence") == "observed-v1", "local reflection evidence was not authorized")
    requested(set(req) <= {"op", "task_id", "source", "rows_path", "where", "group_by", "metrics", "offset"}
            and req["op"] == "reflection-evidence" and req["task_id"] == fb["task_id"], "invalid local reflection request")
    _, judge_end = next(loc for pair, loc in zip(view["pairs"], observation_positions(view))
                        if standalone_helper_request(pair, view) == fb["judge_request_path"]
                        and pair["call"]["payload"]["tool_call_id"] not in view.get("denied_calls", [])
                        and not is_unready_judge(pair, view, training, fb["task_id"], path_map))
    before = [pair for pair, (_, seen) in zip(view["pairs"], observation_positions(view)) if seen < judge_end]
    source = req["source"]
    requested(isinstance(source, dict), "source must be an object")
    if set(source) == {"input"}:
        grants = [v["evo_task"] for m in view["messages"] for v in json_values(m["payload"].get("text", ""))
                  if isinstance(v, dict) and isinstance(v.get("evo_task"), dict)]
        requested(len(grants) == 1 and source["input"] in grants[0]["inputs"], "reflection input is outside this task's grant")
        context_source = str(Path(training["private_learning"]) / "contexts" / (fb["task_id"] + ".json"))
        if path_map is not None and context_source in path_map:
            context = read(path_map[context_source])
            require(context["split"] == "train" and context["task_id"] == fb["task_id"]
                    and context["attempt_id"] == fb["attempt_id"]
                    and context["answer_sha256"] == fb["answer_sha256"]
                    and context["trace"]["binding"]["session_id"] == view["binding"]["session_id"],
                    "portable reflection context binding changed")
            inputs = [item for item in context["inputs"] if item["source"] == source["input"]]
            require(len(inputs) == 1, "portable reflection input missing or ambiguous")
            raw = inputs[0]["text"].encode()
            require(digest(raw) == inputs[0]["sha256"], "portable reflection input hash changed")
        else:
            raw = regular((path_map or {}).get(source["input"], source["input"])).read_bytes()
        verify_solver_inputs({**view, "pairs": before}, [(source["input"], raw.decode(), digest(raw))])
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReflectionRequestError("choose an already observed JSON source") from exc
        source_hash = digest(raw)
    else:
        requested(set(source) == {"tool_call_id", "json_index"} and type(source["json_index"]) is int
                and source["json_index"] >= 0, "source must name an observed input or JSON tool observation")
        pairs = [p for p in before if p["call"]["payload"]["tool_call_id"] == source["tool_call_id"]]
        requested(len(pairs) == 1, "source is not this Solver's observed pre-judge call")
        output = pairs[0]["observation"]["payload"]
        requested(output.get("stop_reason") == "completed" and not output.get("images"), "source observation incomplete")
        values = list(json_values(output["output"]))
        requested(source["json_index"] < len(values), "no complete JSON at the observed index; use existing history")
        data, source_hash = values[source["json_index"]], digest(output["output"].encode())
    def field(value, path):
        requested(isinstance(path, list) and all(isinstance(k, (str, int)) and not isinstance(k, bool) for k in path), "field path must be a JSON key/index list")
        for key in path:
            value = value[key]
        return value
    rows = field(data, req.get("rows_path", []))
    if any(key in req for key in ("where", "group_by", "metrics")):
        requested(isinstance(rows, list), "aggregate source must be a JSON row array")
        predicates = req.get("where", [])
        requested(isinstance(predicates, list), "where must be a list")
        def matches(row, condition):
            requested(set(condition) in ({"field", "op", "value"}, {"field", "op", "right_field"}), "invalid comparison")
            left = field(row, condition["field"])
            right = field(row, condition["right_field"]) if "right_field" in condition else condition["value"]
            op = condition["op"]
            requested(op in ("eq", "ne", "lt", "le", "gt", "ge", "in"), "unsupported comparison")
            if op == "eq": return left == right
            if op == "ne": return left != right
            if op == "lt": return left < right
            if op == "le": return left <= right
            if op == "gt": return left > right
            if op == "ge": return left >= right
            return left in right
        rows = [row for row in rows if all(matches(row, c) for c in predicates)]
        if "metrics" in req:
            requested(isinstance(req["metrics"], dict), "metrics must be an object")
            requested(isinstance(req.get("group_by", []), list), "group_by must be a list")
            groups = {}
            for row in rows:
                key = json.dumps([field(row, path) for path in req.get("group_by", [])], sort_keys=True)
                groups.setdefault(key, []).append(row)
            if not req.get("group_by") and not groups:
                groups["[]"] = []
            result = []
            for key, group in sorted(groups.items()):
                value = {"group": json.loads(key)}
                for name, metric in req["metrics"].items():
                    ident(name)
                    requested(set(metric) <= {"op", "field"}, "invalid metric")
                    op = metric["op"]
                    requested(op in ("count", "distinct", "sum", "min", "max"), "unsupported metric")
                    values = [field(row, metric["field"]) for row in group] if op != "count" else []
                    if op == "count": value[name] = len(group)
                    elif op == "distinct": value[name] = len({json.dumps(v, sort_keys=True) for v in values})
                    else:
                        requested(all(type(v) in (int, float) and math.isfinite(v) for v in values), "numeric metric required")
                        value[name] = sum(values) if op == "sum" else (min(values) if op == "min" else max(values)) if values else None
                result.append(value)
            rows = result
    text = encoded(rows).decode()
    offset = req.get("offset", 0)
    requested(type(offset) is int and 0 <= offset <= len(text), "invalid local evidence offset")
    return {"op": "reflection-evidence", "task_id": fb["task_id"], "source_sha256": source_hash,
            "sha256": digest(text.encode()), "offset": offset, "end": min(offset + 4000, len(text)),
            "total": len(text), "text": text[offset:offset + 4000]}


def reflection_evidence(req, request_path):
    helper = regular(__file__).resolve()
    training = read(helper.parent.parent / "training.json")
    require(read(Path(training["private_learning"]) / "frozen-training.json") == training, "frozen training changed")
    contract = role_contract(training, "reflector", req["task_id"])
    require(str(Path(request_path).parent) == contract["request_dir"], "use own reflection request directory")
    view = native_role_view(training, contract, request_path)
    fb = read(judge_paths(training, req["task_id"], training["attempt_id"])["output"])
    require(fb["status"] == "ok", "judge error is not reflection feedback")
    reflection_operations(view, training, req["task_id"], fb, contract)
    verify_judge_evidence(view, fb, fb["judge_request_path"])
    positions = observation_positions(view)
    returned = next(seen for pair, (_, seen) in zip(view["pairs"], positions)
                    if standalone_helper_request(pair, view) == fb["judge_request_path"]
                    and pair["call"]["payload"]["tool_call_id"] not in view.get("denied_calls", [])
                    and not is_unready_judge(pair, view, training, req["task_id"]))
    after = [p for p, (called, _) in zip(view["pairs"], positions) if called > returned]
    try:
        verify_material_records({**view, "pairs": after}, material_records(contract["materials"][1:]), standalone=True)
    except ValueError as exc:
        raise ReflectionEntryNotReady("read the complete bound feedback before this optional local calculation") from exc
    require(not any(isinstance(v, dict) and v.get("op") == "seal-role-output" and v.get("complete")
                    for p in view["pairs"] for v in json_values(p["observation"]["payload"]["output"])), "reflection already sealed")
    try:
        return reflection_evidence_value(req, view, training, fb)
    except (ReflectionRequestError, KeyError, TypeError, IndexError) as exc:
        raise ReflectionEntryNotReady(str(exc)) from exc


def reflection_action(call, training, task_id, requests=None, executed_requests=None, drafts=None):
    """Finite post-judge protocol, reconstructed from native writes, never shell intent.

    Requests are plain data written through write_file. Only literal foreground
    read-material/seal helper invocations are accepted. This is an evidence
    validator; native child tools are not a hostile-code sandbox.
    """
    contract = role_contract(training, "reflector", task_id)
    require(contract.get("reflection_mode") == "solver-session", "original Solver reflection mode required")
    args = json.loads(call["arguments"])
    require(isinstance(args, dict), "invalid post-judge tool arguments")
    requests = requests if requests is not None else {}
    executed_requests = executed_requests if executed_requests is not None else set(requests)
    drafts = drafts if drafts is not None else {}
    directory = contract["request_dir"]
    def request_path(value):
        return (isinstance(value, str) and posixpath.dirname(value) == directory
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.json", posixpath.basename(value)) is not None)
    def valid_request(value):
        require(isinstance(value, dict), "reflection request must be a JSON object")
        if value.get("op") == "read-material":
            require(set(value) <= {"op", "path", "offset"} and value.get("path") == contract["materials"][1]
                    and type(value.get("offset", 0)) is int and value.get("offset", 0) >= 0,
                    "post-judge reads are restricted to this attempt's complete feedback")
        elif value.get("op") == "reflection-evidence":
            require(training.get("reflection_evidence") == "observed-v1" and value.get("task_id") == task_id,
                    "local evidence is not authorized for this task/profile")
        else:
            require(value == {"op": "seal-role-output", "role": "reflector", "task_id": task_id},
                    "post-judge helper is restricted to feedback reading or original Solver sealing")
        return value
    if call.get("name") in ("write_file", "edit_file"):
        path = args.get("file_path")
        require(isinstance(path, str) and posixpath.isabs(path), "use the assigned absolute draft/request path")
        path = posixpath.normpath(path)
        if call["name"] == "edit_file":
            require(training.get("candidate_edit") == "native-draft-v1", "draft edits require the declared new protocol")
            require(path == contract["output"], "edit only this original Solver's own unsealed candidates.json")
            require(path in drafts, "edit requires this Session's preceding candidate write")
            require(set(args) <= {"file_path", "old_string", "new_string", "replace_all"}, "unsupported native edit arguments")
            old, new = args.get("old_string"), args.get("new_string")
            require(isinstance(old, str) and old and isinstance(new, str) and old != new, "native edit needs distinct literal strings")
            require("replace_all" not in args or type(args["replace_all"]) is bool, "replace_all must be boolean")
            previous = drafts[path]
            count = previous.count(old)
            require(count > 0 and (count == 1 or args.get("replace_all") is True), "edit must match the actual preceding draft uniquely, or use replace_all")
            content = previous.replace(old, new, -1 if args.get("replace_all") is True else 1)
            drafts[path] = content
            return {"kind": "candidate-write", "path": path, "content": content, "via": "edit_file"}
        content = args.get("content")
        require(isinstance(content, str), "reflection write needs literal content")
        if path == contract["output"]:
            drafts[path] = content
            return {"kind": "candidate-write", "path": path, "content": content}
        require(request_path(path), "post-judge writes are restricted to own candidates and request directory")
        # Literal request data has no effect until the helper is invoked.
        # Keep its exact bytes so unexecuted format repairs remain possible,
        # while an already-executed request cannot silently change meaning.
        require(path not in executed_requests or requests[path] == content, "an executed reflection request cannot be changed")
        requests[path] = content
        return {"kind": "request-write", "path": path, "request_text": content}
    require(call.get("name") == "exec_command", "post-judge business queries, answer edits and extra tools are forbidden")
    pair = {"call": {"payload": call}}
    path = standalone_helper_request(pair, {"binding": {"agent_state": training.get("agent_state", "")}})
    # The installed helper may be relocated during offline review. The exact
    # helper in the original Session's State is supplied by the caller profile.
    require(path is not None and request_path(path) and path in requests,
            "post-judge exec needs one literal helper request previously written by this Solver")
    value = valid_request(json.loads(requests[path]))
    executed_requests.add(path)
    return {"kind": value["op"], "path": path, "request": value}


def verify_solver_scoped_reads(pair, view, training, task, fb, contract):
    """Check explicit native/helper reads; never interpret arbitrary programs."""
    workspace = view["binding"]["workspace"]
    def canonical(path):
        require(isinstance(path, str) and path and "\0" not in path, "invalid Solver material path")
        return posixpath.normpath(path if posixpath.isabs(path) else posixpath.join(workspace, path))
    def under(path, root):
        return path == root or path.startswith(root.rstrip("/") + "/")
    private = canonical(training["private_learning"])
    own_requests = canonical(contract["request_dir"])
    own_answer = canonical(judge_paths(training, task, training["attempt_id"])["answer"])
    allowed_private = {canonical(contract["materials"][0]), canonical(fb["judge_request_path"]),
                       canonical(fb["training_snapshot"])}
    call, paths = pair["call"]["payload"], []
    if call.get("name") == "read_file":
        paths.append(json.loads(call["arguments"]).get("file_path"))
    elif standalone_helper_request(pair, view):
        paths.extend(value["material_path"] for value in json_values(pair["observation"]["payload"]["output"])
                     if isinstance(value, dict) and "material_path" in value and "sha256" in value
                     and all(k in value for k in ("offset", "end", "total", "text")))
    for path in paths:
        actual = canonical(path)
        if under(actual, private):
            require(actual in allowed_private or under(actual, own_requests),
                    "Solver cannot read private candidates, other tasks, or a library for curation before scoring")
        if under(actual, canonical(training["answer_dir"])):
            require(actual == own_answer, "Solver cannot read another task's answer")
        roster_root = canonical(posixpath.join(workspace, "train_tasks"))
        if under(actual, roster_root):
            require(under(actual, posixpath.join(roster_root, task)), "Solver cannot read another training task")
        # /app is GDPevo's fixed container root; offline fixtures may relocate
        # workspace but must not turn that explicit original test root public.
        test_roots = {canonical(posixpath.join(workspace, "test_tasks")), "/app/test_tasks"}
        require(not any(under(actual, root) for root in test_roots),
                "Solver cannot read held-out test materials")


def reflection_operations(view, training, task, fb, contract, path_map=None):
    """Separate preparation data from the finite operations after scoring.

    A pre-judge request file can hold role/input reads or judge preparation.
    It becomes a post-judge operation only when actually invoked after judge;
    that invocation must still satisfy reflection_action's restricted protocol.
    """
    positions = observation_positions(view)
    judges = [(pair, loc) for pair, loc in zip(view["pairs"], positions)
              if standalone_helper_request(pair, view) == fb["judge_request_path"]
              and pair["call"]["payload"]["tool_call_id"] not in view.get("denied_calls", [])
              and not is_unready_judge(pair, view, training, task, path_map)]
    if len(judges) != 1:
        bad = judges[1] if len(judges) > 1 else (None, None)
        raise ReflectionBoundaryError("requires exactly one canonical judge operation", "judge",
                                      bad[0]["call"]["payload"]["tool_call_id"] if bad[0] else None)
    judge_pair, (judge_at, judge_returned) = judges[0]
    requests, executed, drafts, actions, sealed = {}, set(), {}, [], False
    for pair, (called, observed) in zip(view["pairs"], positions):
        call = pair["call"]["payload"]
        phase = "pre_judge" if called <= judge_at else "post_judge"
        if call["tool_call_id"] in view.get("denied_calls", []):
            continue  # Native decision + aborted receipt prove no operation occurred.
        try:
            if called <= judge_at:
                verify_solver_scoped_reads(pair, view, training, task, fb, contract)
                if call.get("name") == "write_file":
                    args = json.loads(call["arguments"])
                    path = args.get("file_path", "")
                    require(path != contract["output"], "candidate was written before authorized feedback")
                    if posixpath.dirname(path) == contract["request_dir"]:
                        content = args["content"]
                        require(isinstance(content, str), "prepared helper request needs literal content")
                        require(path not in executed or requests[path] == content, "an executed reflection request cannot be changed")
                        requests[path] = content
                invoked = standalone_helper_request(pair, view)
                if invoked in requests:
                    executed.add(invoked)
                require(observed <= judge_returned, "pre-judge operation completed after scoring boundary")
                continue
            require(called > judge_returned and called < observed, "post-judge actions must follow scoring observation")
            require(not sealed, "no tool operation is allowed after successful reflection sealing")
            action = reflection_action(call, {**training, "agent_state": view["binding"]["agent_state"]}, task, requests, executed, drafts)
            require(pair["observation"]["payload"].get("stop_reason") == "completed", "post-judge operation did not complete")
            actions.append((pair, called, observed, action))
            if action["kind"] == "seal-role-output":
                sealed = any(isinstance(v, dict) and v.get("op") == "seal-role-output" and v.get("complete") is True
                             for v in json_values(pair["observation"]["payload"].get("output", "")))
        except (ValueError, KeyError, TypeError) as exc:
            raise ReflectionBoundaryError(str(exc), phase, call.get("tool_call_id")) from exc
    return judge_pair, (judge_at, judge_returned), actions


def verify_solver_reflection(view, fb, path_map=None, require_seal=True, input_records=None):
    training = solver_reflection_profile(fb, path_map)
    if training is None:
        return None
    mapped = path_map or {}
    require(view["binding"].get("source") == "subagent" and view["binding"].get("parent_trace"),
            "reflection requires the original native Solver child Session")
    sources = [source["path"] for source in view["sources"]]
    require(sources == sorted(set(sources)), "original Solver trace segments must be complete and ordered")
    if path_map is None:
        trace_root = Path(view["binding"]["agent_state"]).parent / "traces"
        actual_sources = sorted(str(path) for path in trace_root.rglob(view["binding"]["session_id"] + "_*.jsonl"))
        require(sources == actual_sources, "all original Solver trace segments are required")
    task, attempt = fb["task_id"], fb["attempt_id"]
    contract = role_contract(training, "reflector", task)
    _, _, actions = reflection_operations(view, training, task, fb, contract, path_map)
    require(fb["status"] == "ok", "judge error cannot become negative reflection")
    negative = feedback(fb, task, attempt, fb["answer_sha256"])
    judge = verify_judge_evidence(view, fb, fb["judge_request_path"], path_map)
    verify_solver_skill(view, fb, path_map)
    positions = observation_positions(view)
    judge_pair, (judge_at, judge_returned) = next((p, loc) for p, loc in zip(view["pairs"], positions)
        if p["call"]["payload"]["tool_call_id"] == judge["tool_call_id"])
    require(judge_at < judge_returned, "judge observation must follow its operation")
    user_messages = [m for m in view["messages"] if m["payload"].get("type") == "text"
                     and m["payload"].get("role") == "user" and m["payload"].get("sender") == "parent_agent"]
    require(len(user_messages) == 1, "reflection must inherit the original Solver run without parent steering")
    first = user_messages[0]
    first_index = view["messages"].index(first)
    require(all(first_index < called for called, _ in positions), "original Solver task must precede every action")
    grants = [value.get("evo_task") for value in json_values(first["payload"]["text"])
              if isinstance(value, dict) and "evo_task" in value]
    seals = [value.get("evo_role_seal") for value in json_values(first["payload"]["text"])
             if isinstance(value, dict) and "evo_role_seal" in value]
    require(len(grants) == len(seals) == 1 and isinstance(grants[0], dict), "original Solver task/reflection grants required")
    grant = grants[0]
    require(set(grant) == {"split", "task_id", "attempt_id", "inputs"} and grant["split"] == "train"
            and grant["task_id"] == task and grant["attempt_id"] == attempt,
            "reflection grant belongs to another task or attempt")
    parent_rows = [json.loads(line) for line in regular(mapped.get(view["binding"]["parent_trace"], view["binding"]["parent_trace"])).read_bytes().splitlines()]
    parent_metas = [r["payload"] for r in parent_rows if r.get("type") == "session_meta" and not r.get("origin")]
    require(len(parent_metas) == 1 and parent_metas[0].get("source") in ("cli", "benchmark"), "reflection parent is not the original training root")
    require(seals[0] == {**contract, "parent_session_id": parent_metas[0]["session_id"]}, "original Solver reflection contract changed")
    records = material_records(contract["materials"], path_map)
    before = [p for p, (_, observed) in zip(view["pairs"], positions) if observed < judge_at]
    verify_material_records({**view, "pairs": before}, records[:1], standalone=True)
    if input_records is None and path_map is not None:
        context_path = str(Path(training["private_learning"]) / "contexts" / (task + ".json"))
        if context_path in mapped:
            context = read(mapped[context_path])
            require(context["task_id"] == task and context["attempt_id"] == attempt,
                    "portable original Solver input context changed")
            input_records = context["inputs"]
    if input_records is None:
        inputs = material_records(grant["inputs"], path_map)
    else:
        require([record["source"] for record in input_records] == grant["inputs"], "original input grants changed")
        inputs = [(record["source"], record["text"], record["sha256"]) for record in input_records]
        require(all(digest(text.encode()) == fingerprint for _, text, fingerprint in inputs), "captured input changed")
    verify_solver_inputs(view, inputs, judge["tool_call_id"])
    after = [p for p, (called, _) in zip(view["pairs"], positions) if called > judge_returned]
    feedback_reads, observed_feedback = verify_material_records({**view, "pairs": after}, records[1:], standalone=True)
    reads, _, missing = material_read_status(view, records, standalone=True)
    require(not missing, "original Solver must observe complete role and raw feedback")
    writes, sealed, seal_id = [], False, None
    for pair, called, observed, action in actions:
        call, result = pair["call"]["payload"], pair["observation"]["payload"]
        if action["kind"] == "candidate-write":
            require(negative, "success cannot generate failure candidates")
            if called <= observed_feedback:
                raise ReflectionBoundaryError("full raw feedback must be observed before candidate writing", "post_judge", call["tool_call_id"])
            writes.append(action["content"])
        elif action["kind"] == "read-material":
            receipt = material({**action["request"], "path": mapped.get(action["request"]["path"], action["request"]["path"])})
            receipt["material_path"] = action["request"]["path"]
            require(contains_json(result["output"], receipt), "reflection feedback read was incomplete or fabricated")
        elif action["kind"] == "reflection-evidence":
            values = list(json_values(result["output"]))
            refused = (len(values) == 1 and isinstance(values[0], dict)
                       and values[0].get("code") == "reflection_evidence_unready"
                       and values[0].get("recoverable") is True and "error" in values[0])
            if called <= observed_feedback:
                require(refused, "complete raw feedback must precede local reflection evidence")
                continue  # The controlled entry refused before returning any data.
            try:
                expected = reflection_evidence_value(action["request"], view, training, fb, path_map)
            except (ReflectionRequestError, KeyError, TypeError, IndexError):
                require(refused, "invalid local evidence request lacks its refusal receipt")
                continue
            require(contains_json(result["output"], expected), "local reflection evidence receipt changed")
        elif action["kind"] == "seal-role-output":
            values = list(json_values(result.get("output", "")))
            complete = [v for v in values if isinstance(v, dict) and v.get("op") == "seal-role-output" and v.get("complete") is True]
            if complete:
                require(negative and called > observed_feedback and writes, "reflection seal requires feedback followed by a candidate draft")
                output = regular(mapped.get(contract["output"], contract["output"])).read_bytes()
                expected = role_seal_receipt(contract, view["binding"]["session_id"], reads, digest(output))
                require(complete == [expected], "reflection output differs from its original successful seal")
                require(writes[-1].encode() == output, "sealed candidate file differs from original Solver write")
                sealed, seal_id = True, call["tool_call_id"]
            else:
                require(not any(isinstance(v, dict) and v.get("complete") is True for v in values), "unbound complete reflection receipt")
    base = {"mode": "solver-session", "task_id": task, "attempt_id": attempt,
            "session_id": view["binding"]["session_id"], "answer_sha256": fb["answer_sha256"],
            "judge_tool_call_id": judge["tool_call_id"], "feedback_sha256": digest(encoded(fb)),
            "feedback_observed_at": observed_feedback}
    if not negative:
        require(not writes and not sealed, "successful Solver must not invent failure reflection")
        return {**base, "status": "not-required"}
    try:
        require(writes, "negative feedback requires the original Solver's explicit candidate decision")
        candidate_raw = regular(mapped.get(contract["output"], contract["output"])).read_bytes()
        require(writes[-1].encode() == candidate_raw, "candidate output changed after original Solver write")
        candidates = json.loads(candidate_raw)
        require(isinstance(candidates, list) and all(isinstance(c, dict) for c in candidates), "candidate output must be a JSON list; [] means an explicit empty decision")
        for index, candidate in enumerate(candidates):
            missing = sorted({"id", "task_id", "attempt_id", "lesson", "trigger", "scope_hint", "evidence"} - set(candidate))
            require(not missing, f"candidate[{index}] missing required fields: " + ", ".join(missing))
            require(candidate["task_id"] == task and candidate["attempt_id"] == attempt,
                    f"candidate[{index}] must retain assigned task_id={task} and attempt_id={attempt}")
            require(isinstance(candidate["evidence"], list) and candidate["evidence"]
                    and all(isinstance(ref, dict) for ref in candidate["evidence"]),
                    f"candidate[{index}].evidence must be a nonempty list of evidence objects")
        require(all(isinstance(c.get("id"), str) and c["id"].startswith(task + "-" + attempt + "-") for c in candidates), "candidate namespace changed")
        context = {"negative": True, "trace": view, "inputs": [{"sha256": fp} for _, _, fp in inputs]}
        validate_candidates(candidates, {(task, attempt): context})
    except (ValueError, KeyError, TypeError, OSError) as exc:
        message = ("candidate evidence is missing required field: " + str(exc)) if isinstance(exc, KeyError) else str(exc)
        raise ReflectionDraftError(message) from exc
    require(not require_seal or sealed, "negative feedback is missing original Solver reflection sealing")
    return {**base, "status": "sealed" if sealed else "unsealed", "output": contract["output"],
            "output_sha256": digest(candidate_raw), "seal_tool_call_id": seal_id,
            "audit_request": {"op": "audit-role", "role": "reflector", "task_id": task,
                              "binding": view["binding"], "traces": [s["path"] for s in view["sources"]],
                              "materials": contract["materials"], "outputs": [contract["output"]],
                              "role_text_path": contract["materials"][0], "require_role_seal": True}}


def native_dispatch_was_denied(rows, call_at, call_id):
    """A native pre-execution denial is an attempt, not a child dispatch.

    Require the engine's ordered decision and aborted receipt. Missing,
    contradictory or ambiguous evidence remains in the uniqueness check.
    """
    decisions, outputs = [], []
    for index, (_, _, row) in enumerate(rows):
        payload = row.get("payload", {})
        if payload.get("tool_call_id") != call_id:
            continue
        if row.get("type") == "event_msg" and payload.get("type") == "approval_decision":
            decisions.append((index, payload))
        elif row.get("type") == "model_msg" and payload.get("type") == "tool_call_output":
            outputs.append((index, payload))
    return (len(decisions) == len(outputs) == 1
            and decisions[0][1].get("decision") in ("deny", "forbidden")
            and outputs[0][1].get("stop_reason") == "aborted"
            and call_at < decisions[0][0] < outputs[0][0])


def solver_completion(view, path_map=None, expected=None):
    """Bind terminal Solver evidence to the original native background dispatch.

    Child traces are sealed in full after reflection. The still-running parent
    may append other tasks, so only its immutable prefix through this completion
    notice is committed; no existing bytes may change.
    """
    mapped = path_map or {}
    rows = [json.loads(line) for s in view["sources"]
            for line in regular(mapped.get(s["path"], s["path"])).read_bytes().splitlines()]
    events = [r["payload"] for r in rows if not r.get("origin") and r.get("type") == "event_msg"
              and r.get("payload", {}).get("type") == "request_end"]
    require(events and events[-1].get("status") == "completed", "original Solver needs a completed terminal request")
    require(view["messages"] and view["messages"][-1]["payload"].get("type") == "text"
            and view["messages"][-1]["payload"].get("role") == "assistant", "original Solver must return after its final tool observation")
    prompt = next(m["payload"]["text"] for m in view["messages"]
                  if m["payload"].get("role") == "user" and m["payload"].get("sender") == "parent_agent")
    first_path = view["binding"]["parent_trace"]
    first_rows = [json.loads(line) for line in regular(mapped.get(first_path, first_path)).read_bytes().splitlines()]
    metas = [r["payload"] for r in first_rows if r.get("type") == "session_meta" and not r.get("origin")]
    require(len(metas) == 1, "original training parent metadata missing")
    parent = metas[0]
    for key in ("provider", "model_id", "workspace", "agent_state"):
        require(parent.get(key) == view["binding"][key], "original Solver parent runtime changed")
    if expected is not None:
        sources = expected.get("sources", [])
        require(sources and any(s["path"] == first_path for s in sources), "missing original parent completion prefix")
    else:
        paths = sorted(Path(first_path).parent.glob(parent["session_id"] + "_*.jsonl"))
        if Path(first_path) not in paths:
            paths.append(Path(first_path)); paths.sort()
        sources = [{"path": str(p), "bytes": len(regular(p).read_bytes())} for p in paths]
    parent_rows, loaded = [], []
    for source in sources:
        raw = regular(mapped.get(source["path"], source["path"])).read_bytes()
        prefix = raw[:source["bytes"]]
        require(len(prefix) == source["bytes"] and prefix.endswith(b"\n"), "parent completion prefix is incomplete")
        if expected is not None:
            require(digest(prefix) == source["sha256"], "original parent completion prefix changed")
        segment_rows = [json.loads(line) for line in prefix.splitlines()]
        segment_metas = [r["payload"] for r in segment_rows if r.get("type") == "session_meta" and not r.get("origin")]
        require(len(segment_metas) == 1 and all(segment_metas[0].get(key) == parent.get(key)
                for key in ("session_id", "source", "provider", "model_id", "workspace", "agent_state")),
                "original parent segment identity changed")
        offset = 0
        for line in prefix.splitlines(keepends=True):
            offset += len(line)
            row = json.loads(line)
            if not row.get("origin"):
                parent_rows.append((source["path"], offset, row))
        loaded.append({"path": source["path"], "bytes": len(prefix), "sha256": digest(prefix)})
    def matching_call(payload):
        if payload.get("type") != "tool_call" or payload.get("name") != "run_subagent":
            return False
        call = json.loads(payload["arguments"])
        if call.get("prompt") != prompt and not ("prompt" not in call and call.get("prompt_sha256") == digest(prompt.encode())
                                                 and isinstance(call.get("prompt_file"), str)):
            return False
        require(call.get("run_in_background") is True and all(call.get(k) == view["binding"][k] for k in ("provider", "model_id")),
                "original Solver must use the frozen native background call")
        return True
    calls = [(i, r["payload"]) for i, (_, _, r) in enumerate(parent_rows)
             if r.get("type") == "model_msg"
             and r.get("payload", {}).get("type") == "tool_call"
             and r["payload"].get("name") == "run_subagent"
             and not native_dispatch_was_denied(parent_rows, i, r.get("payload", {}).get("tool_call_id"))
             and matching_call(r.get("payload", {}))]
    require(len(calls) == 1, "one original Solver dispatch required")
    call_at, call = calls[0]
    outputs = [(i, r["payload"]) for i, (_, _, r) in enumerate(parent_rows)
               if r.get("type") == "model_msg" and r.get("payload", {}).get("type") == "tool_call_output"
               and r["payload"].get("tool_call_id") == call["tool_call_id"]]
    require(len(outputs) == 1 and outputs[0][0] > call_at, "original Solver launch result missing")
    handles = re.findall(r"\bsubagent_id ([A-Za-z0-9_-]+)(?:[;\]\s])", outputs[0][1]["output"])
    require(len(handles) == 1, "original Solver native handle missing")
    require(any(r.get("type") == "event_msg" and r.get("payload", {}).get("type") == "subagent"
                and r["payload"].get("session_id") == view["binding"]["session_id"] for _, _, r in parent_rows), "original Solver parent pointer missing")
    notices = []
    for i, (path, offset, row) in enumerate(parent_rows):
        p = row.get("payload", {})
        if p.get("type") != "text" or p.get("sender") != "harness":
            continue
        block = re.match(r"(?:\[background_task_done\]\n(.*?)\n\[/background_task_done\]|<background_task_done>\n(.*?)\n</background_task_done>)", p.get("text", ""), re.S)
        if block:
            values = dict(re.findall(r"^(kind|id|status):\s*(\S+)\s*$", block[1] or block[2], re.M))
            if values.get("kind") == "subagent" and values.get("id") == handles[0]:
                notices.append((i, path, offset, values))
    require(len(notices) == 1 and notices[0][0] > outputs[0][0] and notices[0][3].get("status") == "completed",
            "original Solver requires its completed native background notice")
    end_path, end_offset = notices[0][1:3]
    committed = []
    for source in loaded:
        if source["path"] == end_path:
            raw = regular(mapped.get(end_path, end_path)).read_bytes()[:end_offset]
            committed.append({"path": end_path, "bytes": end_offset, "sha256": digest(raw)})
            break
        committed.append(source)
    return {"parent_session_id": parent["session_id"], "subagent_id": handles[0],
            "dispatch_tool_call_id": call["tool_call_id"], "sources": committed, "status": "completed"}


def guarded_role_contract(req):
    if req.get("role") not in ("reflector", "evolver"):
        require(not req.get("require_role_seal"), "guarded role identity is missing")
        return None
    role_text = req.get("role_text_path")
    path = str(Path(role_text).parent.parent / "frozen-training.json") if role_text else ""
    mapped = req.get("path_map", {}).get(path, path)
    if not mapped or not Path(mapped).is_file():
        require(not req.get("require_role_seal"), "guarded role needs frozen-training.json")
        return None
    training = read(mapped)
    if training.get("guard_version") != 2:
        require(not req.get("require_role_seal"), "guarded role profile is missing")
        return None
    contract = role_contract(training, req["role"], req.get("task_id"))
    require(len(req["materials"]) == len(contract["materials"]) and set(req["materials"]) == set(contract["materials"])
            and req["outputs"] == [contract["output"]]
            and role_text == contract["materials"][0], "role audit differs from frozen material/output contract")
    return contract


def role_seal_receipt(contract, session_id, reads, output_sha256):
    return {"op": "seal-role-output", "complete": True, "role": contract["role"],
            "task_id": contract["task_id"], "session_id": session_id,
            "config_sha256": contract["config_sha256"], "file_path": contract["output"],
            "sha256": output_sha256, "materials": reads}


def native_role_view(training, contract, request_path, *, active_tool=None):
    """Bind this helper to the original claimed native role and current exec."""
    helper = regular(__file__).resolve()
    state = helper.parents[3]
    private = Path(training["private_learning"])
    session_id = ident(active_tool["session_id"] if active_tool is not None else os.environ.get("PENGUIN_SESSION_ID", ""))
    trace_root = state.parent / "traces"
    traces = sorted(trace_root.rglob(session_id + "_*.jsonl"))
    require(traces, "current native Session has no original trace")
    rows = [json.loads(line) for path in traces for line in regular(path).read_bytes().splitlines()]
    metas = [row["payload"] for row in rows if row.get("type") == "session_meta" and not row.get("origin")]
    require(metas and all(meta["session_id"] == session_id and meta["source"] == "subagent"
                         and Path(meta["agent_state"]).resolve() == state for meta in metas),
            "seal must run in the assigned original native leaf Session")
    meta = metas[0]
    for key in ("provider", "model_id"):
        require(meta[key] == training["runtime"][key], "leaf runtime differs from frozen training")
    prompts = [row["payload"]["text"] for row in rows if not row.get("origin")
               and row.get("type") == "model_msg" and row.get("payload", {}).get("type") == "text"
               and row["payload"].get("role") == "user" and row["payload"].get("sender") == "parent_agent"]
    require(prompts, "native leaf has no original parent task")
    tickets = []
    for path in sorted((private / "dispatch").glob("*.json")):
        if path.name.endswith(".claimed.json"):
            continue
        ticket = read(path)
        if (ticket.get("phase") == contract.get("dispatch_phase", contract["role"]) and ticket.get("task_id") == contract["task_id"]
                and native_prompt(ticket.get("call")) == prompts[0]):
            claim = read(path.parent / (digest(ticket["slot"].encode()) + ".claimed.json"))
            require(claim["request_sha256"] == digest(json.dumps(ticket["call"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()),
                    "role dispatch claim does not match its native task")
            tickets.append((ticket, claim))
    require(len(tickets) == 1, "seal needs exactly one registered native role dispatch")
    ticket, claim = tickets[0]
    expected_grant = {"evo_role_seal": {**contract, "parent_session_id": ticket["session_id"]}}
    require(any(value == expected_grant for value in json_values(prompts[0])), "parent role sealing grant differs from frozen inputs")
    parent_trace = None
    parent_call = False
    for path in sorted(trace_root.rglob(ticket["session_id"] + "_*.jsonl")):
        for line in regular(path).read_bytes().splitlines():
            row = json.loads(line); payload = row.get("payload", {})
            if row.get("origin"):
                continue
            if row.get("type") == "event_msg" and payload.get("type") == "subagent" and payload.get("session_id") == session_id:
                parent_trace = str(path)
            if (row.get("type") == "model_msg" and payload.get("type") == "tool_call"
                    and payload.get("name") == "run_subagent" and payload.get("tool_call_id") == claim["tool_call_id"]
                    and json.loads(payload["arguments"]) == ticket["call"]):
                parent_call = True
    require(parent_trace and parent_call, "native child is not bound to its registered parent call")
    binding = {key: meta[key] for key in ("session_id", "source", "provider", "model_id", "workspace", "agent_state")}
    binding["parent_trace"] = parent_trace
    calls = [row["payload"] for row in rows if not row.get("origin") and row.get("type") == "model_msg" and row.get("payload", {}).get("type") == "tool_call"]
    require(calls, "current seal tool call is absent from native trace")
    active = calls[-1]
    if active_tool is None:
        require(standalone_helper_request({"call": {"payload": active}}, {"binding": binding}) == str(request_path),
                "seal must be one literal foreground helper command in the current native Session")
    else:
        require(active["tool_call_id"] == active_tool["tool_call_id"]
                and active["name"] == active_tool["tool_name"]
                and json.loads(active["arguments"]) == json.loads(active_tool["arguments"]),
                "stage check differs from the original pending native tool call")
    return trace_view([str(path) for path in traces], binding, active_call=active["tool_call_id"])


def judge_readiness(training, task_id, request_path):
    """Check observed authorized material before the first actual scoring call."""
    contract = role_contract(training, "reflector", task_id)
    view = native_role_view(training, contract, request_path)
    return judge_readiness_record(view, training, task_id)


def judge_readiness_record(view, training, task_id, path_map=None):
    contract = role_contract(training, "reflector", task_id)
    original = [m["payload"]["text"] for m in view["messages"] if m["payload"].get("type") == "text"
                and m["payload"].get("role") == "user" and m["payload"].get("sender") == "parent_agent"]
    require(len(original) == 1, "judge readiness requires the original Solver task without steering")
    grants = [value["evo_task"] for value in json_values(original[0])
              if isinstance(value, dict) and set(value) == {"evo_task"}]
    require(len(grants) == 1 and set(grants[0]) == {"split", "task_id", "attempt_id", "inputs"}
            and grants[0]["split"] == "train" and grants[0]["task_id"] == task_id
            and grants[0]["attempt_id"] == training["attempt_id"], "judge readiness task/attempt differs from original Solver grant")
    inputs = grants[0]["inputs"]
    require(isinstance(inputs, list) and inputs and all(isinstance(p, str) and Path(p).is_absolute() for p in inputs)
            and len(inputs) == len(set(inputs)), "invalid original public input grant")
    paths = judge_paths(training, task_id, training["attempt_id"])
    scope = {"judge_request_path": paths["judge_request"], "training_snapshot": paths["training_snapshot"]}
    for pair in view["pairs"]:
        verify_solver_scoped_reads(pair, view, training, task_id, scope, contract)
    skill = solver_skill_observation(view, training, task_id, training["attempt_id"], allow_missing=True)
    mapped = path_map or {}
    context_path = str(Path(training["private_learning"]) / "contexts" / (task_id + ".json"))
    if context_path in mapped:
        context = read(mapped[context_path])
        require(context["task_id"] == task_id and context["attempt_id"] == training["attempt_id"]
                and [item["source"] for item in context["inputs"]] == inputs, "portable readiness input identity changed")
        input_records = [(item["source"], item["text"], item["sha256"]) for item in context["inputs"]]
        require(all(digest(text.encode()) == fingerprint for _, text, fingerprint in input_records), "portable readiness input changed")
    else:
        input_records = material_records(inputs, path_map)
    _, _, missing = solver_input_read_status(view, input_records)
    _, _, role_missing = material_read_status(view, material_records(contract["materials"][:1], path_map), standalone=True)
    missing += role_missing
    plan = []
    if skill is None:
        path = str(Path(view["binding"]["agent_state"]) / "skills/evo-harness-learned/SKILL.md")
        plan.append({"tool": "read_file", "arguments": {"file_path": path, "offset": 1, "limit": 2000}})
        missing.insert(0, {"path": path, "offset": 1, "method": "native read_file"})
    for item in [item for item in missing if item.get("method") != "native read_file"]:
        request = {"op": "read-material", "path": item["path"], "offset": item["offset"]}
        name = "ready-" + digest(encoded(request))[:24] + ".json"
        destination = str(Path(contract["request_dir"]) / name)
        plan.append({"request_path": destination, "request": request,
                     "command": shlex.join(["python3", "-B", str(Path(view["binding"]["agent_state"]) / "skills/evo-harness-rsi-toolkit/scripts/evo.py"), destination])})
    return {"op": "judge-readiness", "ready": not missing, "complete": not missing,
            "recoverable": bool(missing), "task_id": task_id, "attempt_id": training["attempt_id"],
            "session_id": view["binding"]["session_id"], "judge_calls": 0,
            "missing_read_count": len(missing), "more_missing_reads": len(missing) > 8,
            "missing_reads": missing[:8], "read_plan": plan[:8],
            "next_action": "Read the exact listed paths in this original Solver Session. For each request, write its supplied JSON then execute its command. Retry the same canonical judge command after these reads; it checks all remaining material and returns the next bounded plan if needed. No scoring call or attempt was consumed." if missing
                           else "Authorized material is fully observed; the canonical helper may perform the one scoring call."}


def is_unready_judge(pair, view, training, task_id, path_map=None):
    """A verified no-HTTP readiness result is not an actual judge operation."""
    if training.get("guard_version") != 2 or training.get("reflection_mode") != "solver-session":
        return False
    values = list(json_values(pair["observation"]["payload"].get("output", "")))
    if not any(isinstance(v, dict) and v.get("op") == "judge-readiness" for v in values):
        return False
    require(pair["observation"]["payload"].get("stop_reason") == "completed", "judge readiness operation did not complete")
    called = view["messages"].index(pair["call"])
    prefix = {**view, "messages": view["messages"][:called],
              "pairs": [p for p, (_, observed) in zip(view["pairs"], observation_positions(view)) if observed < called]}
    expected = judge_readiness_record(prefix, training, task_id, path_map)
    require(not expected["ready"] and values == [expected], "judge readiness receipt differs from original observations")
    return True


def seal_role_output(req, request_path):
    """A native learning leaf checks its own observations before sealing its draft.

    The server injects PENGUIN_SESSION_ID for this leaf's exec. No request may
    supply another Session, trace list, material scope or path remapping. The
    only ignored unmatched action is this currently executing seal command.
    """
    require(set(req) <= {"op", "role", "task_id"} and req.get("op") == "seal-role-output",
            "seal request accepts only op, role and assigned task_id")
    require(request_path and Path(request_path).is_absolute(), "seal request path must be absolute")
    helper = regular(__file__).resolve()
    state = helper.parents[3]
    training = read(helper.parent.parent / "training.json")
    private = Path(training["private_learning"])
    require(read(private / "frozen-training.json") == training, "frozen training profile changed")
    contract = role_contract(training, req["role"], req.get("task_id"))
    view = native_role_view(training, contract, request_path)
    session_id = view["binding"]["session_id"]
    if contract.get("reflection_mode") == "solver-session":
        require(not any(value.get("op") == "seal-role-output" and value.get("complete") is True
                        for pair in view["pairs"] if standalone_helper_request(pair, view)
                        for value in json_values(pair["observation"]["payload"]["output"])
                        if isinstance(value, dict)),
                "original Solver reflection is already sealed; preserve the existing receipt and return")
        fb = read(contract["materials"][1])
        _, _, checked_actions = reflection_operations(view, training, req["task_id"], fb, contract)
    records = material_records(contract["materials"])
    reads, _, missing = material_read_status(view, records, standalone=True)
    if missing:
        if contract.get("reflection_mode") == "solver-session":
            if any(item["path"] == contract["materials"][0] for item in missing):
                raise ReflectionBoundaryError("required Solver role was not fully observed before judge; this attempt cannot be repaired after scoring", "pre_judge")
            early_candidate = next((pair["call"]["payload"]["tool_call_id"] for pair, _, _, action in checked_actions
                                    if action["kind"] == "candidate-write"), None)
            if early_candidate:
                raise ReflectionBoundaryError("full raw feedback must be observed before candidate writing", "post_judge", early_candidate)
        return {"op": "seal-role-output", "complete": False, "sealed": False,
                "missing_reads": missing[:64], "missing_read_count": len(missing), "more_missing_reads": len(missing) > 64,
                "next_action": "In this same leaf, read each listed offset in one standalone literal helper exec; await complete output, then retry sealing to check any remaining offsets. Do not rerun Solver/judge or ask the parent to edit content."}
    validation = contract["validation"]
    if contract.get("reflection_mode") == "solver-session":
        fb = read(contract["materials"][1])
        try:
            verify_solver_reflection(view, fb, require_seal=False)
        except ReflectionDraftError as exc:
            return {"op": "seal-role-output", "complete": False, "sealed": False,
                    "code": "candidate_draft_invalid", "recoverable": True, "phase": "post_judge",
                    "task_id": req["task_id"], "session_id": session_id, "error": str(exc),
                    "output_path": contract["output"], "write_call": candidate_write_call(contract["output"]),
                    "next_action": "Correct only your own unsealed candidate JSON: replace write_call.arguments.content with the complete corrected array as JSON text and call native write_file directly at the supplied path, or, under native-draft-v1, use native edit_file on this exact unsealed output; then seal in this same Session. Use your existing history and contract examples; do not read/edit through Python, shell or read_file, query services, rescore, or read source/trace files. Existing extra sha256 fields on answer/feedback evidence are ignored; do not remove them merely for format."}
    elif contract["role"] == "reflector":
        validate_candidate_file(validation)
    else:
        validate_decisions(validation)
    receipt = role_seal_receipt(contract, session_id, reads, digest(regular(contract["output"]).read_bytes()))
    for pair in view["pairs"]:
        if standalone_helper_request(pair, view):
            for prior in json_values(pair["observation"]["payload"]["output"]):
                if isinstance(prior, dict) and prior.get("op") == "seal-role-output" and prior.get("complete") is True:
                    require(prior == receipt, "role output or materials changed after successful sealing")
    return receipt


def audit_role(req):
    view = trace_view(req["traces"], req["binding"], req.get("path_map"))
    contract = guarded_role_contract(req)
    if contract and contract.get("reflection_mode") == "solver-session":
        fb = read(req.get("path_map", {}).get(contract["materials"][1], contract["materials"][1]))
        verify_solver_reflection(view, fb, req.get("path_map"))
    materials = contract["materials"] if contract else req["materials"]
    reads, last_read = verify_material_records(view, material_records(materials, req.get("path_map")), standalone=contract is not None)
    outputs = []
    positions = observation_positions(view)
    for path in req.get("outputs", []):
        receipt = {"file_path": path, "sha256": digest(regular(req.get("path_map", {}).get(path, path)).read_bytes())}
        expected = role_seal_receipt(contract, req["binding"]["session_id"], reads, receipt["sha256"]) if contract else receipt
        if contract:
            previous = [value for pair in view["pairs"] if standalone_helper_request(pair, view)
                        for value in json_values(pair["observation"]["payload"]["output"])
                        if isinstance(value, dict) and value.get("op") == "seal-role-output" and value.get("complete") is True]
            require(previous and all(value == expected for value in previous),
                    "role output must remain as first sealed in native trace")
        require(any(called > last_read and (standalone_helper_request(pair, view) if contract else helper_observation(pair, view))
                    and contains_json(pair["observation"]["payload"]["output"], expected)
                    for pair, (called, _) in zip(view["pairs"], positions)),
                "role output must be sealed in native trace after all assigned materials were read")
        outputs.append(receipt)
    return {"binding": req["binding"], "sources": view["sources"], "materials": reads,
            "outputs": outputs, "complete": True}


def verify_workspace_inputs(view, paths, workspace, records=None):
    root = Path(workspace).resolve()
    for name in paths:
        path = Path(name) if records is not None else regular(name)
        require(path.resolve().is_relative_to(root), "workspace input escapes public statement workspace")
        lines = (records[name] if records is not None else path.read_text()).splitlines()
        observed = {}
        for pair in view["pairs"]:
            call = pair["call"]["payload"]
            if call["name"] != "read_file":
                continue
            args = json.loads(call["arguments"])
            target = Path(args.get("file_path", ""))
            if not target.is_absolute():
                target = root / target
            if target.resolve() != path.resolve():
                continue
            for line in pair["observation"]["payload"]["output"].splitlines():
                match = re.match(r"^\s*(\d+)\t(.*)$", line)
                if match:
                    observed[int(match[1])] = match[2]
        require(all(observed.get(i) == line for i, line in enumerate(lines, 1)),
                "workspace input not fully read through native read_file")


def verify_training_root(contexts, roles, path_map=None):
    """Continuous HTTP learning must belong to one native training root.

    Parent segments may differ after compaction; the native Session identity
    and State/runtime remain the same. Generic workspace evaluation is separate.
    """
    http = [c for c in contexts if c["feedback"].get("source") == "evo-judge"]
    if not http:
        return None
    require(len(http) == len(contexts), "continuous training cannot mix feedback protocols")
    root = None
    fields = ("session_id", "source", "agent_state", "workspace", "provider", "model_id")
    for binding in [c["trace"]["binding"] for c in contexts] + [r["binding"] for r in roles]:
        require(binding["source"] == "subagent", "continuous training requires native child roles")
        parent = binding["parent_trace"]
        rows = [json.loads(line) for line in regular((path_map or {}).get(parent, parent)).read_bytes().splitlines()]
        metas = [r["payload"] for r in rows if r.get("type") == "session_meta" and not r.get("origin")]
        require(len(metas) == 1 and metas[0].get("source") in ("cli", "benchmark"), "invalid native training root")
        identity = {key: metas[0][key] for key in fields}
        require(all(identity[key] == binding[key] for key in ("agent_state", "workspace", "provider", "model_id")),
                "role State/runtime differs from training root")
        require(root is None or root == identity, "roles belong to different training roots")
        root = identity
    return root


def guarded_training_runtime(contexts):
    """Read the hashed authority before any guarded training export is written."""
    snapshots = []
    for context in contexts:
        feedback = context["feedback"]
        if feedback.get("source") == "evo-judge":
            raw = regular(feedback["training_snapshot"]).read_bytes()
            require(digest(raw) == feedback["training_sha256"], "portable frozen training snapshot changed")
            snapshots.append((raw, json.loads(raw)))
    if not any(snapshot.get("guard_version") == 2 for _, snapshot in snapshots):
        return None
    require(len(snapshots) == len(contexts)
            and all(snapshot.get("guard_version") == 2 for _, snapshot in snapshots),
            "guarded training requires one frozen profile for every context")
    first_raw, first = snapshots[0]
    require(all(raw == first_raw for raw, _ in snapshots), "guarded training snapshots differ")
    require(isinstance(first.get("runtime"), dict) and first["runtime"], "missing frozen runtime")
    return first["runtime"]


def seal_training(req):
    root = Path(req["private_learning"]).resolve()
    def rel(path):
        p = regular(path).resolve()
        require(p.is_relative_to(root), "private evidence must stay under private_learning")
        return p.relative_to(root).as_posix()
    contexts = [read(p) for p in req["contexts"]]
    frozen_runtime = guarded_training_runtime(contexts)
    guarded = frozen_runtime is not None
    if guarded:
        require(encoded(req.get("runtime")) == encoded(frozen_runtime),
                "seal runtime must exactly match the complete frozen runtime; no export was written")
    validated = validate_contexts(contexts, req["train_ids"])
    training_root = verify_training_root(contexts, req["roles"])
    validate_candidates(read(req["candidates"]), validated)
    roles, trace_paths, reflected = [], set(), []
    sessions = {c["trace"]["binding"]["session_id"] for c in contexts}
    for role in req["roles"]:
        audit_role({**role, "require_role_seal": guarded})
        require(role["role"] in ("reflector", "evolver"), "invalid learning role")
        require(role["role_text_path"] in role["materials"], "role text missing")
        require(len(role["outputs"]) == 1, "one role output required")
        original = next((c for c in contexts if c["task_id"] == role.get("task_id")), None)
        in_solver = (role["role"] == "reflector" and original is not None
                     and original.get("reflection", {}).get("mode") == "solver-session")
        if in_solver:
            require(role["binding"] == original["trace"]["binding"]
                    and role["traces"] == [s["path"] for s in original["trace"]["sources"]],
                    "reflection must retain the original Solver history and Session")
            require({k: v for k, v in role.items() if k != "op"} ==
                    {k: v for k, v in original["reflection"]["audit_request"].items() if k != "op"},
                    "reflection audit differs from captured original Solver")
        else:
            require(role["binding"]["session_id"] not in sessions, "learning roles require fresh sessions")
            sessions.add(role["binding"]["session_id"])
        if role["role"] == "reflector":
            context_path = next(p for p, c in zip(req["contexts"], contexts) if c["task_id"] == role["task_id"])
            require(in_solver or context_path in role["materials"], "Reflector did not read assigned context")
            proposals = read(role["outputs"][0])
            require(all(c["task_id"] == role["task_id"] for c in proposals), "Reflector output crosses task boundary")
            reflected.extend(proposals)
        else:
            require(role["outputs"][0] == req["decisions"], "Evolver output mismatch")
            require(set(req["contexts"] + [req["candidates"], req["library"]]) <= set(role["materials"]),
                    "Evolver did not read complete batch evidence and library")
        for path in role["traces"]:
            trace_paths.add(path)
        if role["binding"].get("parent_trace"):
            trace_paths.add(role["binding"]["parent_trace"])
        roles.append({**role, "material_files": {p: rel(p) for p in role["materials"]},
                      "output_files": {p: rel(p) for p in role["outputs"]}})
    for binding in [c["trace"]["binding"] for c in contexts] + [r["binding"] for r in roles]:
        require(all(binding[k] == req["runtime"][k] for k in ("provider", "model_id")), "frozen runtime mismatch")
    require(reflected == read(req["candidates"]), "candidate concatenation differs from Reflector outputs")
    require(sum(r["role"] == "evolver" for r in roles) == 1, "one Evolver required")
    require([r["task_id"] for r in roles if r["role"] == "reflector"] ==
            [c["task_id"] for c in contexts if c["negative"]], "Reflector coverage mismatch")
    members = {}
    for path, c in zip(req["contexts"], contexts):
        trace_paths.update(s["path"] for s in c["trace"]["sources"])
        trace_paths.update(s["path"] for s in c.get("completion", {}).get("sources", []))
        if c["trace"]["binding"].get("parent_trace"):
            trace_paths.add(c["trace"]["binding"]["parent_trace"])
        members[c["task_id"]] = {"answer_sha256": c["answer_sha256"], "attempt_id": c["attempt_id"],
            "context_file": rel(path), "container_context_path": path, "context_sha256": digest(regular(path).read_bytes()),
            "feedback_sha256": digest(encoded(c["feedback"])), "solver_session_id": c["trace"]["binding"]["session_id"]}
        if c["feedback"].get("source") == "evo-judge":
            fb = c["feedback"]
            require(digest(regular(fb["training_snapshot"]).read_bytes()) == fb["training_sha256"],
                    "portable frozen training snapshot changed")
            members[c["task_id"]]["judge_evidence_files"] = {
                fb["training_path"]: rel(fb["training_snapshot"]),
                c["judge_request"]["path"]: rel(c["judge_request"]["path"]),
                fb["feedback_path"]: rel(fb["feedback_path"])}
    verify_compilation({**req, "path_map": {}})
    record = {"complete": True, "runtime": req["runtime"], "members": members, "roles": roles, "trace_paths": sorted(trace_paths),
              "candidates_file": rel(req["candidates"]), "candidates_sha256": digest(regular(req["candidates"]).read_bytes()),
              "container_candidates_path": req["candidates"], "container_library_path": req["library"],
              "library_file": rel(req["library"]), "library_sha256": digest(regular(req["library"]).read_bytes()),
              "limits": req["limits"], "max_bytes": req["max_bytes"],
              "decisions_file": rel(req["decisions"]), "decisions_sha256": digest(regular(req["decisions"]).read_bytes()),
              "skill_sha256": validate_package(req["skill"])["sha256"]}
    if training_root is not None:
        record["training_root"] = training_root
    if all(c.get("reflection", {}).get("mode") == "solver-session" for c in contexts):
        record["reflection_mode"] = "solver-session"
    write_once(req["output"], encoded(record))
    return record


def capture(req):
    require(req["split"] == "train" and req["task_id"] in req["train_ids"], "learning is train only")
    task, attempt = ident(req["task_id"]), ident(req["attempt_id"])
    answer_bytes = regular(req["answer"]).read_bytes()
    if req.get("op") == "capture-evaluation":
        raw = regular(req["evaluation"]).read_text()
        result = json.loads(raw)
        require(result["status"] == "ok", "failed evaluation has no learning feedback")
        finite(result["score"], high=100)
        require(result["session_id"] == req["binding"]["session_id"], "evaluator/solver Session mismatch")
        fb = {"source": "penguin-evaluation", "task_id": task, "attempt_id": attempt,
              "answer_sha256": digest(answer_bytes), "status": "ok", "raw_response": raw,
              "score": result["score"] / 100, "correct": result["score"] == 100,
              "evaluation_identity": req["evaluation_identity"]}
    else:
        fb = read(req["feedback"])
        require(fb.get("source") == "evo-judge", "HTTP capture requires canonical judge receipt")
    negative = feedback(fb, task, attempt, digest(answer_bytes))
    require(fb["status"] == "ok", "judge error blocks reflection and compilation")
    view = trace_view(req["traces"], req["binding"])
    if fb.get("source") == "penguin-evaluation":
        verify_workspace_inputs(view, req["inputs"], req["binding"]["workspace"])
    else:
        grant = {"evo_task": {"split": "train", "task_id": task, "attempt_id": attempt, "inputs": req["inputs"]}}
        require(any(m["payload"].get("role") == "user" and m["payload"].get("type") == "text"
                    and contains_json(m["payload"]["text"], grant) for m in view["messages"]),
                "input files not bound to the native task delegation")
    # The result must originate from the exact single-operation helper call.
    judge_receipt = None
    if fb.get("source") != "penguin-evaluation":
        require(req["answer"] == read(req["judge_request"])["answer"]
                and req["feedback"] == fb["feedback_path"], "capture paths differ from judged attempt")
        judge_receipt = verify_judge_evidence(view, fb, req["judge_request"])
        verify_solver_skill(view, fb)
        require(fb["request"]["answer"] == json.loads(answer_bytes), "judge submitted a different answer")
        verify_solver_inputs(view, material_records(req["inputs"]), judge_receipt["tool_call_id"])
    inputs = []
    for source in req["inputs"]:
        raw = regular(source).read_bytes()
        inputs.append({"source": source, "sha256": digest(raw), "text": raw.decode()})
    record = {"schema": VERSION, "split": "train", "task_id": task, "attempt_id": attempt,
              "inputs": inputs, "answer": answer_bytes.decode() if fb.get("source") == "penguin-evaluation" else json.loads(answer_bytes),
              "answer_text": answer_bytes.decode(),
              "answer_sha256": digest(answer_bytes),
              "feedback": fb, "negative": negative, "trace": view}
    if judge_receipt is not None:
        record["judge_request"] = judge_receipt
        reflection = verify_solver_reflection(view, fb)
        if reflection is not None:
            record["reflection"] = reflection
            record["completion"] = solver_completion(view)
    write_once(req["output"], encoded(record))
    return {"path": req["output"], "sha256": digest(encoded(record)), "negative": negative}


def validate_contexts(contexts, train_ids, path_map=None):
    require(len(train_ids) == len(set(train_ids)) and train_ids, "invalid training roster")
    require(len(contexts) == len(train_ids), "batch incomplete")
    seen, sessions = set(), set()
    for c in contexts:
        require(c["split"] == "train" and c["task_id"] in train_ids and c["task_id"] not in seen,
                "foreign, test or duplicate context")
        seen.add(c["task_id"])
        require(c["trace"]["binding"]["session_id"] not in sessions, "Solver tasks require fresh sessions")
        sessions.add(c["trace"]["binding"]["session_id"])
        require(c["feedback"]["status"] == "ok", "judge error blocks the batch")
        negative = feedback(c["feedback"], c["task_id"], c["attempt_id"], c["answer_sha256"])
        require(c["negative"] == negative, "negative feedback flag mismatch")
        require(c["trace"]["truncated"] is False, "incomplete context")
        expected_answer = c["answer_text"] if c["feedback"].get("source") == "penguin-evaluation" else json.loads(c["answer_text"])
        require(digest(c["answer_text"].encode()) == c["answer_sha256"]
                and expected_answer == c["answer"], "captured answer changed")
        for source in c["inputs"]:
            require(digest(source["text"].encode()) == source["sha256"], "captured input changed")
        for source in c["trace"]["sources"]:
            require(digest(regular((path_map or {}).get(source["path"], source["path"])).read_bytes()) == source["sha256"],
                    "raw solver trace changed after capture")
        actual = trace_view([s["path"] for s in c["trace"]["sources"]], c["trace"]["binding"], path_map)
        require(actual == c["trace"], "captured trace differs from raw evidence")
        if c["feedback"].get("source") == "penguin-evaluation":
            require(json.loads(c["feedback"]["raw_response"])["session_id"] == c["trace"]["binding"]["session_id"],
                    "evaluation session mismatch")
            verify_workspace_inputs(actual, [s["source"] for s in c["inputs"]], c["trace"]["binding"]["workspace"],
                                    {s["source"]: s["text"] for s in c["inputs"]})
        else:
            grant = {"evo_task": {"split": "train", "task_id": c["task_id"], "attempt_id": c["attempt_id"],
                                  "inputs": [s["source"] for s in c["inputs"]]}}
            require(any(m["payload"].get("role") == "user" and m["payload"].get("type") == "text"
                        and contains_json(m["payload"]["text"], grant) for m in actual["messages"]), "input grant changed")
            try:
                verify_solver_inputs(actual, [(s["source"], s["text"], s["sha256"]) for s in c["inputs"]],
                                     c["judge_request"]["tool_call_id"])
            except ValueError as exc:
                raise ValueError("captured input absent from Solver observations") from exc
            require(verify_judge_evidence(actual, c["feedback"], c["judge_request"]["path"], path_map)
                    == c["judge_request"], "captured judge operation changed")
            verify_solver_skill(actual, c["feedback"], path_map)
            require(c["feedback"]["request"]["answer"] == c["answer"], "judge submitted a different answer")
            reflection = verify_solver_reflection(actual, c["feedback"], path_map, input_records=c["inputs"])
            if reflection is not None:
                require(reflection == c.get("reflection"), "captured Solver reflection changed")
                require(solver_completion(actual, path_map, c.get("completion")) == c.get("completion"),
                        "captured Solver terminal evidence changed")
    return {(c["task_id"], c["attempt_id"]): c for c in contexts}


def validate_candidates(candidates, contexts):
    result = {}
    for c in candidates:
        key = ident(c["id"])
        require(key not in result, "duplicate candidate")
        ctx = contexts[(c["task_id"], c["attempt_id"])]
        require(ctx["negative"], "successful execution is not a reflection source")
        for field in ("lesson", "trigger", "scope_hint"):
            nonempty(c[field], field)
        require(c["evidence"], "candidate needs evidence")
        for ref in c["evidence"]:
            require(ref["kind"] in ("input", "tool", "answer", "feedback"), "unknown evidence type")
            nonempty(ref["claim"], "evidence claim")
            require(ref["epistemic"] in ("observed", "inferred"), "label fact versus inference")
            if ref["kind"] == "tool":
                require(ref["tool_call_id"] in {p["call"]["payload"]["tool_call_id"]
                        for p in ctx["trace"]["pairs"]}, "evidence tool not in attempt")
            elif ref["kind"] == "input":
                require(ref["sha256"] in {i["sha256"] for i in ctx["inputs"]}, "input evidence mismatch")
        result[key] = c
    return result


def validate_entries(entries, limits):
    counts, seen = {}, set()
    for entry in entries:
        key = ident(entry["id"])
        require(key not in seen, "duplicate entry")
        seen.add(key)
        require(entry["level"] in ("general", "topic"), "invalid harness level")
        for field in ("trigger", "scope", "body"):
            nonempty(entry[field], field)
        if entry["level"] == "general":
            general_description(entry.get("description"))
        topic = ident(entry["topic"]) if entry["level"] == "topic" else "general"
        bucket = (entry["level"], topic)
        counts[bucket] = counts.get(bucket, 0) + 1
        require(counts[bucket] <= limits[entry["level"]], "skill slot budget exceeded; merge or skip")


def compile_batch(req):
    contexts = validate_contexts([read(p) for p in req["contexts"]], req["train_ids"], req.get("path_map"))
    return compile_validated_contexts(req, contexts)


def compile_validated_contexts(req, contexts):
    """Shared data compiler; callers must establish their own protocol evidence.

    Not an execute() operation and not a validation override for live training.
    The separate offline-replay protocol supplies original-version verified,
    immutable source contexts rather than revalidating old traces as new runs.
    """
    candidates = validate_candidates(read(req["candidates"]), contexts)
    current = read(req["library"])
    validate_entries(current, req["limits"])
    entries = {e["id"]: e for e in current}
    decisions = read(req["decisions"])
    covered, targets, layer_coverage = set(), set(), {"general": set(), "topic": set()}
    for edit in decisions:
        op = edit["op"]
        require(op in ("ADD", "MERGE", "REVISE", "SKIP"), "unknown evolver operation")
        nonempty(edit["reason"], "decision reason")
        ids = edit["candidate_ids"]
        require(ids and len(ids) == len(set(ids)) and set(ids) <= candidates.keys(), "unknown candidate")
        layer = edit.get("level", "all") if op == "SKIP" else edit["entry"]["level"]
        require(layer in ("general", "topic", "all"), "invalid compilation layer")
        for level in (("general", "topic") if layer == "all" else (layer,)):
            require(not layer_coverage[level].intersection(ids), "candidate decided twice in one layer")
            layer_coverage[level].update(ids)
        covered.update(ids)
        if op == "SKIP":
            continue
        entry = edit["entry"]
        target = ident(entry["id"])
        require(target not in targets, "combine edits to the same entry")
        targets.add(target)
        if op == "ADD":
            require(target not in entries, "ADD target already exists")
        else:
            require(target in entries, "MERGE/REVISE target does not exist")
        # The Evolver supplies the complete replacement; scripts do not invent lessons.
        entries[target] = {k: entry[k] for k in ("id", "level", "trigger", "scope", "body")}
        if entry["level"] == "topic":
            entries[target]["topic"] = entry["topic"]
        else:
            entries[target]["description"] = general_description(entry.get("description"))
    require(covered == candidates.keys(), "every candidate needs an explicit decision")
    result = sorted(entries.values(), key=lambda e: e["id"])
    validate_entries(result, req["limits"])
    write_once(req["output"], encoded(result))
    return {"entries": len(result), "candidates": len(candidates), "sha256": digest(encoded(result))}


def validate_candidate_file(req):
    """Validate a role's draft without changing it or admitting a new attempt."""
    contexts = validate_contexts([read(p) for p in req["contexts"]], req["train_ids"], req.get("path_map"))
    values = read(req["candidates"])
    candidates = validate_candidates(values, contexts)
    if "candidate_prefix" in req:
        prefix = nonempty(req["candidate_prefix"], "candidate namespace")
        require(all(key.startswith(prefix) and len(key) > len(prefix) for key in candidates),
                "candidate id must use the assigned task/attempt namespace")
    return {"complete": True, "candidates": len(candidates), "sha256": digest(regular(req["candidates"]).read_bytes())}


def validate_decisions(req):
    """Dry-run the exact compiler/exporter so the Evolver can fix its own draft."""
    with tempfile.TemporaryDirectory(prefix="evo-decisions-check-") as temp:
        library = str(Path(temp) / "compiled.json")
        compiled = compile_batch({**req, "output": library})
        package = export_skill({**req, "library": library, "output": str(Path(temp) / "skill")})
    return {"complete": True, "entries": compiled["entries"], "candidates": compiled["candidates"],
            "decisions_sha256": digest(regular(req["decisions"]).read_bytes()), "skill_sha256": package["sha256"]}


def verify_compilation(req):
    """Rebuild the data-only package from audited role outputs, without any model call."""
    with tempfile.TemporaryDirectory(prefix="evo-rebuild-") as tmp:
        library = str(Path(tmp) / "compiled.json")
        compile_batch({**req, "output": library})
        expected = export_skill({**req, "library": library, "output": str(Path(tmp) / "skill")})
        require(expected == validate_package(req["skill"]), "exported Skill differs from audited compilation")
    return expected


LEGACY_READER = '''---
name: evo-harness-learned
description: Before task work read this Reader and follow all its native read_file calls; context bodies and general descriptions only.
version: 2026.10.22.10
---

For the frozen GDPevo adapter, this Reader is:
/opt/penguin-bench/home/default_project/agents/default_agent/agent_state/skills/evo-harness-learned/SKILL.md

Finish every page of this Reader, then issue every native read_file request in
read_calls, in order. The list supplies exact absolute file_path, offset and
limit for every current-context/topic body page. Await each actual output and
complete any truncated page through native continuation. Do not guess paths,
select entries, load other groups or substitute summaries for required bodies.
All general descriptions are already complete in this Reader; never read their
SKILL.md bodies. All five tests load this same frozen set. Empty entries and
read_calls are normal H0: finish the Reader, with no additional method bodies.

Prefer this native read plan before payload inspection. One complete foreground
cat of one exact required file may accompany supported read-only discovery.
Preserve exact, untruncated stdout; business actions require a later tool call
after the complete output is observed. Neither a directory listing nor an
instruction/config read proves a Skill read. Do not change State or output limits.

Preparation may read exact own State AGENTS.md and public task files. A guarded
training Solver may also read its exact own toolkit/training.json; tests gain
no training-file access. Use native reads or simple literal pwd/ls/find/cat/head/
echo, initial cd and public filename sorting, e.g. find /app/input -type f | sort.
The fixed public JSON display form is also supported:
cd input/payloads && for f in *.json; do echo "=== $f ==="; cat "$f"; echo; done
Finish the Skill plan before other programs, business APIs, task execution,
answer writes or final answers. Material and post-scoring restrictions remain.

A loaded method does not establish its premises. Check fields, entities, time
basis, granularity and necessary conditions against the current request and
authorized facts; apply the supported steps. Obtain thresholds, defaults,
labels and source priorities from those facts. Transfer a condition between
fields only by an applicable definition or valid inference. For unresolved
premises, use a supported checking branch or leave the method unused. Preserve
correct work, produce only requested outputs, and do not obtain training
answers or learn from test feedback.

## Frozen group Skills and native read_file requests

'''

READER = """---
name: evo-harness-learned
description: Frozen group methods; current-context bodies and general descriptions only.
version: 2026.10.22.12
---

The deterministic install-context entry supplies this Reader, all general
descriptions below and every current-context/topic body in the native initial
system context. No selector, extra file read or manual loading is required when
that frozen context is present. General bodies are not injected. All five tests
receive the identical set. The manifest also retains exact native read plans
for explicit file inspection; inspection does not choose or replace methods.

A loaded method is conditional: check its fields, entities, time basis, grain
and necessary premises against the current request and authorized facts.
Obtain unknown thresholds, labels, defaults and priority there. Preserve useful
queries, joins and calculations; an unresolved premise needs a bounded branch,
not a mandatory guess. Produce only requested outputs. No training answers or
test feedback are learning material. Empty entries are the normal H0.

## Frozen group Skills

"""

CONTEXT_TRANSPORT = "agents-md-v1"
CONTEXT_OPEN = "<!-- evo-frozen-context-v1\n"
CONTEXT_CLOSE = "\n/evo-frozen-context-v1 -->"


def model_context(files):
    """Render precisely the frozen loading set; no model choice or text editing."""
    manifest = validate_package_files(files)
    loaded = {"SKILL.md": files["SKILL.md"], **{e["path"]: files[e["path"]]
              for e in manifest["entries"] if e["load_mode"] == "body"}}
    text = "\n".join(raw.decode() for raw in loaded.values())
    metadata = {"transport": CONTEXT_TRANSPORT,
                "loading_policy": "official-cl-context-body-general-description",
                "files": {name: digest(raw) for name, raw in loaded.items()},
                "bytes": len(text.encode()), "sha256": digest(text.encode())}
    require(CONTEXT_OPEN not in text and CONTEXT_CLOSE not in text, "reserved context framing in Skill")
    block = CONTEXT_OPEN + json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n" + text + CONTEXT_CLOSE
    require(len(block.encode()) <= manifest["max_bytes"], "actual context with metadata exceeds frozen byte budget")
    return {**metadata, "text": text, "block": block, "injected_bytes": len(block.encode())}


def split_context(text):
    """Remove exactly one own block, preserving all other native instructions."""
    if CONTEXT_OPEN not in text and CONTEXT_CLOSE not in text:
        return text, None
    require(text.count(CONTEXT_OPEN) == text.count(CONTEXT_CLOSE) == 1, "ambiguous frozen context block")
    begin, end = text.index(CONTEXT_OPEN), text.index(CONTEXT_CLOSE) + len(CONTEXT_CLOSE)
    return text[:begin] + text[end:], text[begin:end]


def install_context(req):
    """Prepare one State before snapshotting; never dispatch or mutate a live run."""
    state = Path(req["state"])
    require(state.is_dir() and not state.is_symlink(), "native State required")
    reader = regular(state / "skills/evo-harness-learned/SKILL.md").read_bytes()
    manifest = parse_reader(reader)
    files = {"SKILL.md": reader, **{entry["path"]: regular(state / entry["state_path"]).read_bytes()
                                for entry in manifest["entries"]}}
    validate_package_files(files)
    context = model_context(files)
    agents = regular(state / "AGENTS.md")
    config_path = regular(state / "system_config.yaml")
    config = config_path.read_text()
    blocks = list(re.finditer(r"(?m)^skills:\n(?:[ \t].*\n|\n)*", config))
    if blocks:
        require(len(blocks) == 1 and len(re.findall(r"(?m)^  enabled: (?:true|false)$", blocks[0][0])) == 1,
                "prepare native emitted skills.enabled configuration before installing context")
        block = re.sub(r"(?m)^  enabled: (?:true|false)$", "  enabled: false", blocks[0][0])
        config = config[:blocks[0].start()] + block + config[blocks[0].end():]
    else:
        require(not re.search(r"(?m)^skills:", config), "unsupported native skills configuration")
        config = config.rstrip("\n") + "\nskills:\n  enabled: false\n"
    base, old = split_context(agents.read_text())
    # New blocks always occupy the original trailing position. No outside text
    # normalization, no runtime config changes, and no mutable sidecar receipt.
    updated = base + context["block"]
    if config_path.read_text() != config:
        atomic(config_path, config.encode())
    if agents.read_text() != updated:
        atomic(agents, updated.encode())
    return {key: value for key, value in context.items() if key not in ("text", "block")}


class IncompleteContextObservation(ValueError):
    """Native prompt metadata absent; no claim about actual loaded text."""


def context_observation(metas, files):
    context = model_context(files)
    if not metas or any(meta.get("system_prompt") is None for meta in metas):
        raise IncompleteContextObservation("missing native model-context metadata")
    for meta in metas:
        prompt = meta["system_prompt"]
        require(isinstance(prompt, str) and prompt.count(CONTEXT_OPEN) == prompt.count(CONTEXT_CLOSE) == 1,
                "frozen Skill context absent or duplicated in actual model system prompt")
        _, actual = split_context(prompt)
        require(actual == context["block"], "actual model Skill text/length/hash differs from frozen context")
    return {key: value for key, value in context.items() if key not in ("text", "block")}


FULL_SKILL_SCHEMA = "evo-multi-skill-v1"
FULL_SKILL_OPEN = "<!-- evo-multi-skill-v1\n"
FULL_SKILL_CLOSE = "-->\n"


def validate_skill_readability(raw):
    """No native read_file physical line may be silently shortened."""
    require(b"\0" not in raw and b"\r" not in raw,
            "SKILL.md must be native-readable UTF-8 text with LF line endings, without NUL or bare CR")
    for number, line in enumerate(raw.decode("utf-8").split("\n"), 1):
        require(len(line.encode("utf-16-le")) // 2 <= 2000,
                f"SKILL.md line {number} exceeds native read_file's 2000 UTF-16 unit limit; wrap without omitting content")


def method_name(entry_id):
    return "evo-method-" + digest(ident(entry_id).encode())[:20]


def general_description(value):
    return nonempty(value, "general description")


def method_prefix(entry_id, level, description=None):
    name = method_name(entry_id)
    description = (general_description(description) if level == "general" else
                   "Frozen current-context Evo-Harness method; read its complete body with the group Reader.")
    return (f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n"
            f"version: 2026.10.22.10\n---\n\n# {entry_id}\n\nLevel: {level}\n")


GDPEVO_STATE = "/opt/penguin-bench/home/default_project/agents/default_agent/agent_state"


def context_read_calls(entries, files):
    """Native read_file arguments only; no model content or automatic execution."""
    calls = []
    for entry in entries:
        if entry["load_mode"] != "body":
            continue
        lines = len(files[entry["path"]].decode().split("\n")) - 1
        for offset in range(1, lines + 1, 1000):
            calls.append({"file_path": GDPEVO_STATE + "/" + entry["state_path"],
                          "offset": offset, "limit": min(1000, lines - offset + 1)})
    return calls


def render_package(entries, max_bytes):
    """Package each complete authored method; never choose or rewrite its rules."""
    require(type(max_bytes) is int and 0 < max_bytes <= 16000, "invalid byte budget; group maximum is 16000 UTF-8 bytes")
    validate_entries(entries, {"general": len(entries), "topic": len(entries)})
    manifest = {"schema": FULL_SKILL_SCHEMA, "max_bytes": max_bytes, "entries": [], "read_calls": []}
    files = {}
    for entry in sorted(entries, key=lambda e: e["id"]):
        name = method_name(entry["id"])
        path, state_path = f"methods/{name}/SKILL.md", f"skills/{name}/SKILL.md"
        topic = f"Topic: {entry['topic']}\n" if entry["level"] == "topic" else ""
        raw = (method_prefix(entry["id"], entry["level"], entry.get("description")) + topic +
               f"Trigger: {entry['trigger']}\nScope: {entry['scope']}\n\n{entry['body']}\n").replace("\r\n", "\n").encode()
        require(path not in files, "native method name collision")
        validate_skill_readability(raw)
        files[path] = raw
        manifest["entries"].append({"id": entry["id"], "level": entry["level"], "name": name,
                                    "path": path, "state_path": state_path,
                                    "bytes": len(raw), "sha256": digest(raw),
                                    "load_mode": "description" if entry["level"] == "general" else "body",
                                    **({"description": entry["description"]} if entry["level"] == "general" else {})})
    manifest["read_calls"] = context_read_calls(manifest["entries"], files)
    files["SKILL.md"] = READER.encode() + FULL_SKILL_OPEN.encode() + encoded(manifest) + FULL_SKILL_CLOSE.encode()
    validate_package_files(files)
    model_context(files)  # include actual injection framing/metadata before freeze
    return files


def render_skill(entries, max_bytes):
    """Compatibility accessor for the fixed Reader, not all method bodies."""
    return render_package(entries, max_bytes)["SKILL.md"]


def parse_reader(raw):
    """Validate the fixed Reader and its complete, deterministic package manifest."""
    prefixes = [(reader + FULL_SKILL_OPEN).encode() for reader in (READER, LEGACY_READER)]
    prefix = next((value for value in prefixes if raw.startswith(value)), prefixes[0])
    require(raw.startswith(prefix) and raw.endswith(FULL_SKILL_CLOSE.encode()), "learned reader or multi-Skill format changed")
    manifest_raw = raw[len(prefix):-len(FULL_SKILL_CLOSE.encode())]
    manifest = json.loads(manifest_raw)
    require(isinstance(manifest, dict) and set(manifest) == {"schema", "max_bytes", "entries", "read_calls"}
            and manifest["schema"] == FULL_SKILL_SCHEMA
            and type(manifest["max_bytes"]) is int and 0 < manifest["max_bytes"] <= 16000
            and isinstance(manifest["entries"], list) and isinstance(manifest["read_calls"], list), "invalid multi-Skill manifest")
    require(encoded(manifest) == manifest_raw, "noncanonical multi-Skill manifest")
    validate_skill_readability(raw)
    ids, names = [], []
    for entry in manifest["entries"]:
        keys = {"id", "level", "name", "path", "state_path", "bytes", "sha256", "load_mode"}
        require(isinstance(entry, dict) and set(entry) == keys | ({"description"} if entry.get("level") == "general" else set()),
                "invalid multi-Skill entry manifest")
        require(entry["load_mode"] == ("description" if entry["level"] == "general" else "body"),
                "Skill level/loading policy mismatch")
        if entry["level"] == "general":
            general_description(entry["description"])
        name = method_name(entry["id"])
        require(entry["name"] == name and entry["path"] == f"methods/{name}/SKILL.md"
                and entry["state_path"] == f"skills/{name}/SKILL.md", "noncanonical native Skill mapping")
        require(entry["level"] in ("general", "topic") and type(entry["bytes"]) is int and entry["bytes"] > 0
                and isinstance(entry["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), "invalid multi-Skill entry")
        ids.append(entry["id"]); names.append(name)
    require(ids == sorted(set(ids)) and len(names) == len(set(names)), "duplicate or unordered multi-Skill entries")
    require(len(raw) + sum(entry["bytes"] for entry in manifest["entries"]) <= manifest["max_bytes"],
            "complete Skill package exceeds frozen byte budget; compact the unsealed draft without truncation")
    return manifest


def parse_skill(raw):
    """Compatibility alias: parse the Reader, never treat it as method bodies."""
    return parse_reader(raw)


def validate_package_files(files):
    require(isinstance(files, dict) and "SKILL.md" in files and all(isinstance(raw, bytes) for raw in files.values()),
            "complete package byte map required")
    manifest = parse_reader(files["SKILL.md"])
    require(set(files) == {"SKILL.md", *(entry["path"] for entry in manifest["entries"])}, "missing or unlisted Skill file")
    for entry in manifest["entries"]:
        raw = files[entry["path"]]
        require(len(raw) == entry["bytes"] and digest(raw) == entry["sha256"], "Skill body fingerprint mismatch")
        require(raw.startswith(method_prefix(entry["id"], entry["level"], entry.get("description")).encode()), "native Skill identity mismatch")
        validate_skill_readability(raw)
    require(manifest["read_calls"] == context_read_calls(manifest["entries"], files),
            "native context read plan differs from complete frozen files")
    require(sum(map(len, files.values())) <= manifest["max_bytes"], "complete Skill package exceeds frozen byte budget")
    return manifest


def package_state_files(files):
    manifest = validate_package_files(files)
    return {"skills/evo-harness-learned/SKILL.md": files["SKILL.md"],
            **{entry["state_path"]: files[entry["path"]] for entry in manifest["entries"]}}


def rename_new(source, destination):
    """Linux atomic rename that never replaces even an empty destination."""
    return rename_new_at(-100, source, -100, destination)


def rename_new_at(source_fd, source, destination_fd, destination):
    """The same no-replace operation, optionally anchored to opened directories."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    require(rename is not None, "atomic no-replace export requires Linux renameat2")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    # AT_FDCWD, RENAME_NOREPLACE. Do not fall back to os.rename's overwrite.
    if rename(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def placeholder_marker_fd(directory_fd):
    """Inspect the exact opened directory, without following marker links."""
    require(os.listdir(directory_fd) == [".keep"], "export destination is not the GDPevo empty .keep placeholder")
    fd = os.open(".keep", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size == 0 and info.st_nlink == 1
                and stream.read(1) == b"", "GDPevo .keep must be an ordinary empty single-link file")
    require(os.listdir(directory_fd) == [".keep"], "placeholder changed during inspection")
    identity = os.fstat(directory_fd)
    return {"directory_device": identity.st_dev, "directory_inode": identity.st_ino,
            "directory_mode": stat.S_IMODE(identity.st_mode),
            "marker_device": info.st_dev, "marker_inode": info.st_ino,
            "marker_mode": stat.S_IMODE(info.st_mode), "marker_bytes": 0, "marker_sha256": digest(b"")}


def same_directory(directory, fd):
    current, opened = directory.lstat(), os.fstat(fd)
    return (stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino))


def placeholder_marker(directory):
    """Recognize only convert.py's exact empty .keep directory, never clean it."""
    require(directory.is_dir() and not directory.is_symlink(), "export destination already exists")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        marker = placeholder_marker_fd(fd)
        require(same_directory(directory, fd), "placeholder directory changed during inspection")
        return marker
    finally:
        os.close(fd)


def copy_empty_placeholder(directory, original):
    """Preserve its only permitted bytes/modes; inode identity stays in receipt."""
    directory.mkdir()
    write_once(directory / ".keep", b"")
    os.chmod(directory / ".keep", original["marker_mode"])
    os.chmod(directory, original["directory_mode"])
    return placeholder_marker(directory)


def remove_empty_placeholder(directory, backup, expected, record):
    """Reserve the marker file, then rmdir; never rename a lower-layer directory.

    Overlayfs can refuse a rename of image-layer directories with EXDEV even
    between siblings, but supports file rename/copy-up. Retaining the actual
    marker preserves even a late write between inspection and rename. A foreign
    entry makes rmdir fail; directory-parent ownership remains the cooperative
    training root's responsibility (rmdir has no inode-conditional POSIX form).
    """
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    backup_fd = marker_fd = None
    try:
        backup_fd = os.open(backup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        require(placeholder_marker_fd(fd) == expected and same_directory(directory, fd),
                "placeholder changed before removal")
        require(same_directory(backup, backup_fd) and not os.listdir(backup_fd), "placeholder backup changed")
        marker_fd = os.open(".keep", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        opened = os.fstat(marker_fd)
        require(stat.S_ISREG(opened.st_mode) and opened.st_size == 0 and opened.st_nlink == 1
                and (opened.st_dev, opened.st_ino) == (expected["marker_device"], expected["marker_inode"])
                and os.read(marker_fd, 1) == b"", "placeholder marker changed before reservation")
        rename_new_at(fd, ".keep", backup_fd, ".keep")
        record["marker_removed"] = True
        retained = os.fstat(marker_fd)
        record["retained_marker_fd"] = {"device": retained.st_dev, "inode": retained.st_ino,
                                        "bytes": retained.st_size, "mode": stat.S_IMODE(retained.st_mode)}
        saved = placeholder_marker_fd(backup_fd)
        record["backup_placeholder"] = saved
        require(all(saved[key] == expected[key] for key in expected if key.startswith("marker_")),
                "placeholder marker changed while being reserved")
        require((saved["marker_device"], saved["marker_inode"]) == (retained.st_dev, retained.st_ino),
                "reserved marker differs from retained original file")
        record["backup_verified"] = True
        require(same_directory(directory, fd), "placeholder directory changed before removal")
        os.rmdir(directory)
        record["placeholder_removed"] = True
    finally:
        if marker_fd is not None:
            os.close(marker_fd)
        if backup_fd is not None:
            os.close(backup_fd)
        os.close(fd)


def restore_empty_marker(directory, expected):
    """Repair only our original still-empty directory after a failed rmdir."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require((info.st_dev, info.st_ino) == (expected["directory_device"], expected["directory_inode"])
                and same_directory(directory, fd) and not os.listdir(fd), "placeholder changed before rollback")
        marker_fd = os.open(".keep", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            expected["marker_mode"], dir_fd=fd)
        try:
            os.fchmod(marker_fd, expected["marker_mode"])
        finally:
            os.close(marker_fd)
        require(same_directory(directory, fd), "placeholder changed during rollback")
        return placeholder_marker_fd(fd)
    finally:
        os.close(fd)


def export_placeholder(dest):
    """An installed, immutable training profile binds the one allowed placeholder."""
    require(dest.is_absolute() and dest == dest.resolve() and ".." not in dest.parts,
            "placeholder destination must be canonical without symlinks")
    helper = regular(__file__).resolve()
    training_path = helper.parent.parent / "training.json"
    require(training_path.is_file(), "export destination already exists; no installed GDPevo training profile")
    raw = regular(training_path).read_bytes()
    training = json.loads(raw)
    workspace = Path(training["workspace"])
    private = Path(training["private_learning"])
    require(training.get("guard_version") == 2
            and training["train_ids"] == [f"train_{i:03d}" for i in range(1, 6)]
            and re.fullmatch(r"[0-9a-f]{64}", training["config_sha256"])
            and workspace.is_absolute() and workspace == workspace.resolve()
            and dest == workspace / "submission/skill"
            and Path(training["answer_dir"]) == workspace / "submission/answers",
            "only the frozen GDPevo training submission/skill placeholder is supported")
    require(private.is_absolute() and private == private.resolve() and private.is_dir()
            and not private.is_relative_to(dest.parent), "placeholder evidence must stay in private_learning")
    marker = placeholder_marker(dest)
    receipt = private / "skill-export-publication.json"
    require(not receipt.exists() and not receipt.is_symlink(), "prior export publication exists; reconcile without overwriting")
    return {"schema": "evo-gdpevo-placeholder-publication-v2", "output": str(dest),
            "source": "tools/gdpevo/convert.py:write_training_run:workspace/submission/skill/.keep",
            "training_path": str(training_path), "training_sha256": digest(raw),
            "config_sha256": training["config_sha256"], "placeholder": marker,
            "receipt": str(receipt)}


def check_prior_publication(dest):
    """A failed publication stays reserved even if both publish and rollback fail."""
    training_path = Path(__file__).resolve().parent.parent / "training.json"
    if not training_path.is_file():
        return
    training = read(regular(training_path))
    if training.get("guard_version") != 2 or "workspace" not in training:
        return
    bound = Path(training["workspace"]) / "submission/skill"
    if dest.resolve() != bound.resolve():
        return  # validate-decisions and verify_compilation use fresh temp slots.
    receipt = Path(training["private_learning"]) / "skill-export-publication.json"
    require(not receipt.exists() and not receipt.is_symlink(),
            "prior export publication exists; reconcile without overwriting")


def publish_skill(stage, dest, placeholder):
    if placeholder is None:
        if sys.platform.startswith("linux"):
            rename_new(stage, dest)
        else:
            # Generic workspace/H0 exports retain their existing native support
            # outside GDPevo's Linux containers; no libc extension is required.
            require(not dest.exists() and not dest.is_symlink(), "export destination already exists")
            os.rename(stage, dest)
        return
    package = validate_package(stage)
    # Never rename the image-layer directory: overlayfs may return EXDEV even
    # within /app. Reserve only its marker file before removing the empty slot,
    # then atomically publish only our newly created upper-layer stage.
    backup_root = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".evo-skill-placeholder-"))
    backup = backup_root / "original"
    receipt = Path(placeholder["receipt"])
    backup.mkdir()
    os.chmod(backup, placeholder["placeholder"]["directory_mode"])
    record = {**placeholder, "backup": str(backup), "backup_placeholder": None, "backup_verified": False,
              "backup_kind": "reserved_marker_file", "strategy": "reserve_marker_then_remove_empty_directory",
              "skill_sha256": package["sha256"], "status": "prepared",
              "marker_removed": False, "placeholder_removed": False}
    write_once(receipt, encoded(record))
    published = False
    try:
        require(placeholder_marker(dest) == placeholder["placeholder"], "placeholder changed before publication")
        require(digest(regular(placeholder["training_path"]).read_bytes()) == placeholder["training_sha256"],
                "training profile changed before export")
        remove_empty_placeholder(dest, backup, placeholder["placeholder"], record)
        rename_new(stage, dest)
        published = True
        record["status"] = "published"
        atomic(receipt, encoded(record))
    except BaseException as error:
        record.update(status="publication_failed", error_type=type(error).__name__, skill_published=published)
        if record["marker_removed"] and not record["backup_verified"]:
            # The actual marker may contain another writer's bytes or be a link.
            # Keep it intact for reconciliation; do not synthesize an empty restore.
            record["placeholder_restored"] = False
        elif record["marker_removed"] and not published:
            try:
                if record["placeholder_removed"]:
                    # Preserve the backup even after rollback. This second empty
                    # copy is upper-layer too; no image-layer rename is needed.
                    restore = backup_root / "restore"
                    copy_empty_placeholder(restore, placeholder["placeholder"])
                    rename_new(restore, dest)
                    restored = placeholder_marker(dest)
                else:
                    restored = restore_empty_marker(dest, placeholder["placeholder"])
                record["placeholder_restored"] = True
                record["restored_placeholder"] = restored
            except (OSError, ValueError) as rollback_error:
                # Another writer owns dest: preserve it and the verified backup.
                record.update(placeholder_restored=False, rollback_error_type=type(rollback_error).__name__)
        try:
            atomic(receipt, encoded(record))
        except OSError:
            pass  # The initial receipt and same-filesystem backup remain.
        raise


def export_skill(req):
    entries = read(req["library"])
    validate_entries(entries, req["limits"])
    files = render_package(entries, req["max_bytes"])
    dest = Path(req["output"])
    require(not dest.is_symlink(), "export destination is a symlink")
    check_prior_publication(dest)
    placeholder = export_placeholder(dest) if dest.exists() else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".skill-") as tmp:
        stage = Path(tmp) / "skill"
        stage.mkdir()
        for path, raw in files.items():
            write_once(stage / path, raw)
        publish_skill(stage, dest, placeholder)
    return validate_package(dest)


def validate_package(root):
    root = Path(root)
    files = filemap(root)
    raw_files = {path: regular(root / path).read_bytes() for path in files}
    manifest = validate_package_files(raw_files)
    directories = {parent.as_posix() for path in files for parent in Path(path).parents if parent != Path(".")}
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()} == directories,
            "unlisted directory in learned package")
    return {"files": files, "sha256": digest(encoded(files)), "empty": not manifest["entries"],
            "format": FULL_SKILL_SCHEMA, "max_bytes": manifest["max_bytes"], "entries": len(manifest["entries"])}


def journal(req):
    """Controller-authored dispatch receipts. Never chooses or runs another stage."""
    path = Path(req["journal"])
    with locked(str(path) + ".lock"):
        if req["action"] == "init":
            require(not path.exists(), "journal already exists")
            finite(req["budget_usd"], low=0.01)
            state = {"schema": VERSION, "config_sha256": req["config_sha256"],
                     "controller_session_id": ident(req["controller_session_id"]),
                     "budget_usd": req["budget_usd"], "known_cost_usd": 0, "operations": {}}
        else:
            state = read(path)
            require(req["config_sha256"] == state["config_sha256"], "frozen configuration changed")
            if req["action"] == "reserve":
                key = ident(req["operation_id"])
                require(key not in state["operations"], "operation already reserved; reconcile, never redispatch")
                slot = nonempty(req.get("slot", key), "semantic operation slot")
                require(not any(o["slot"] == slot or o["request_sha256"] == req["request_sha256"]
                                for o in state["operations"].values()), "semantic operation already reserved")
                reserve = finite(req["reserve_usd"], low=0.01)
                committed = sum(o["reserve_usd"] for o in state["operations"].values() if o["status"] != "complete")
                require(state["known_cost_usd"] + committed + reserve <= state["budget_usd"], "budget exhausted")
                state["operations"][key] = {"status": "reserved", "reserve_usd": reserve,
                    "request_sha256": req["request_sha256"], "controller_trace": req["controller_trace"],
                    "role": req["role"], "slot": slot, "external_id": None}
            elif req["action"] == "bind":
                op = state["operations"][req["operation_id"]]
                require(op["status"] == "reserved", "operation already bound")
                op.update(status="running", external_id=nonempty(req["external_id"], "external id"))
            elif req["action"] == "settle":
                op = state["operations"][req["operation_id"]]
                require(op["status"] in ("reserved", "running"), "operation already settled")
                require(req["terminated"] is True, "must confirm real process/session termination")
                cost = req.get("cost_usd")
                if cost is None:
                    op.update(status="cost_pending", evidence=req["evidence"])
                else:
                    finite(cost)
                    op.update(status="complete", cost_usd=cost, evidence=req["evidence"])
                    state["known_cost_usd"] += cost
            elif req["action"] == "reconcile_cost":
                op = state["operations"][req["operation_id"]]
                require(op["status"] == "cost_pending", "no pending cost")
                cost = finite(req["cost_usd"])
                op.update(status="complete", cost_usd=cost, evidence=req["evidence"])
                state["known_cost_usd"] += cost
            elif req["action"] != "status":
                raise ValueError("unknown journal action")
        atomic(path, encoded(state))
        return state


def execute(req, request_path=None):
    actions = {"judge": lambda r: judge(r, request_path), "prepare-judge-request": prepare_judge_request,
               "capture": capture, "capture-evaluation": capture, "compile": compile_batch,
               "validate-candidates": validate_candidate_file, "validate-decisions": validate_decisions,
               "read-material": material, "audit-role": audit_role,
               "seal-role-output": lambda r: seal_role_output(r, request_path),
               "reflection-evidence": lambda r: reflection_evidence(r, request_path),
               "seal-training": seal_training,
               "file-digest": lambda r: {"file_path": r["path"], "sha256": digest(regular(r["path"]).read_bytes())},
               "export": export_skill, "journal": journal, "install-context": install_context,
               "verify-package": lambda r: validate_package(r["path"]),
               "fingerprint": lambda r: {"files": filemap(r["path"]),
                    "sha256": digest(encoded(filemap(r["path"])))} }
    return actions[req["op"]](req)


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 2, "usage: python3 -B evo.py REQUEST.json")
        request = read(sys.argv[1])
        result = execute(request, sys.argv[1])
        print(json.dumps(judge_stdout(result) if request["op"] == "judge" else result,
                         ensure_ascii=False, allow_nan=False))
    except (ValueError, KeyError, OSError, TypeError, IndexError) as exc:
        error = {"error": str(exc)}
        if isinstance(exc, ReflectionBoundaryError):
            error.update(code="reflection_boundary_violation", recoverable=False, phase=exc.phase,
                         tool_call_id=exc.tool_call_id,
                         next_action="Stop this attempt and return this error to the parent. Preserve evidence; do not retry seal, answer, judge or reflection.")
        elif isinstance(exc, ReflectionEntryNotReady):
            error.update(code="reflection_evidence_unready", recoverable=True,
                         next_action="Use existing history, or correct only this bound local-data request after full feedback. This entry made no business query, answer write or scoring call.")
        elif isinstance(locals().get("request"), dict) and request.get("op") == "reflection-evidence":
            error.update(code="reflection_evidence_invalid", recoverable=False,
                         next_action="Preserve the invalid history/evidence and report it; do not retry answer, judge or reflection.")
        elif isinstance(exc, ReflectionDraftError):
            error.update(code="candidate_draft_invalid", recoverable=True, phase="post_judge",
                         next_action="Correct only the original Solver's own unsealed candidate JSON using the supplied schema and evidence identifiers, then seal in this same Session. No queries, scoring, source or trace reads.")
        print(json.dumps(error), file=sys.stderr)
        sys.exit(2)
