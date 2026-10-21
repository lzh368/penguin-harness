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
    messages, sources, metas = [], [], []
    for path in paths:
        raw = regular((path_map or {}).get(str(path), path)).read_bytes()
        sources.append({"path": str(path), "sha256": digest(raw), "bytes": len(raw)})
        for line_no, line in enumerate(raw.splitlines(), 1):
            row = json.loads(line)
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
    return {"binding": expected, "sources": sources, "truncated": False,
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


def verify_judge_evidence(view, fb, request_path, path_map=None):
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
    for pair in chunked:
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


def verify_solver_inputs(view, records, before_call_id=None):
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
    role_file = str(private / "roles" / (role + ".md"))
    contexts = [str(private / "contexts" / (task + ".json")) for task in training["train_ids"]]
    if role == "reflector":
        require(task_id in training["train_ids"], "role task is outside the frozen batch")
        context = str(private / "contexts" / (task_id + ".json"))
        output = str(private / "roles" / task_id / "candidates.json")
        materials = [role_file, context]
        validation = {"op": "validate-candidates", "contexts": [context], "train_ids": [task_id],
                      "candidates": output, "candidate_prefix": task_id + "-" + training["attempt_id"] + "-"}
    else:
        require(task_id is None, "batch Evolver has no task_id")
        output = str(private / "decisions.json")
        materials = [role_file, str(private / "candidates.json"), str(private / "prebatch-library.json"), *contexts]
        validation = {"op": "validate-decisions", "contexts": contexts, "train_ids": training["train_ids"],
                      "candidates": materials[1], "library": materials[2], "decisions": output,
                      **{key: training[key] for key in ("limits", "max_entries", "max_bytes")}}
    return {"schema": 1, "role": role, "task_id": task_id, "config_sha256": training["config_sha256"],
            "materials": materials, "output": output, "validation": validation}


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
    session_id = ident(os.environ.get("PENGUIN_SESSION_ID", ""))
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
        if ticket.get("phase") == contract["role"] and ticket.get("task_id") == contract["task_id"] and ticket.get("call", {}).get("prompt") == prompts[0]:
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
    require(standalone_helper_request({"call": {"payload": active}}, {"binding": binding}) == str(request_path),
            "seal must be one literal foreground helper command in the current native Session")
    view = trace_view([str(path) for path in traces], binding, active_call=active["tool_call_id"])
    records = material_records(contract["materials"])
    reads, _, missing = material_read_status(view, records, standalone=True)
    if missing:
        return {"op": "seal-role-output", "complete": False, "sealed": False,
                "missing_reads": missing[:64], "missing_read_count": len(missing), "more_missing_reads": len(missing) > 64,
                "next_action": "In this same leaf, read each listed offset in one standalone literal helper exec; await complete output, then retry sealing to check any remaining offsets. Do not rerun Solver/judge or ask the parent to edit content."}
    validation = contract["validation"]
    if contract["role"] == "reflector":
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
        require(role["binding"]["session_id"] not in sessions, "learning roles require fresh sessions")
        sessions.add(role["binding"]["session_id"])
        if role["role"] == "reflector":
            context_path = next(p for p, c in zip(req["contexts"], contexts) if c["task_id"] == role["task_id"])
            require(context_path in role["materials"], "Reflector did not read assigned context")
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
              "limits": req["limits"], "max_entries": req["max_entries"], "max_bytes": req["max_bytes"],
              "decisions_file": rel(req["decisions"]), "decisions_sha256": digest(regular(req["decisions"]).read_bytes()),
              "skill_sha256": validate_package(req["skill"])["sha256"]}
    if training_root is not None:
        record["training_root"] = training_root
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
            require(c["feedback"]["request"]["answer"] == c["answer"], "judge submitted a different answer")
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
        topic = ident(entry["topic"]) if entry["level"] == "topic" else "general"
        bucket = (entry["level"], topic)
        counts[bucket] = counts.get(bucket, 0) + 1
        require(counts[bucket] <= limits[entry["level"]], "skill slot budget exceeded; merge or skip")


def compile_batch(req):
    contexts = validate_contexts([read(p) for p in req["contexts"]], req["train_ids"], req.get("path_map"))
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


READER = '''---
name: evo-harness-learned
description: Select relevant frozen Evo-Harness procedures for the current task.
version: 2026.10.21.7
---

Before business API calls or writing the task answer, use native read_file to read
this entire SKILL.md and index.json beside it, requesting all remaining lines if
truncated. Before selection, use only native read_file for task/reader material
and simple pwd, ls or find commands for directory inspection, plus the selector
budget retries described below. Choose only entries whose trigger and scope match the current task
(zero is valid), in order of relevance. Execute one foreground native exec_command:
`python3 -B /absolute/path/to/this/Skill/select.py --ids <comma-separated IDs>`.
Use --ids '' for an empty selection. Alternatively set exec_command.workdir to this
Skill directory and use select.py, or use `cd ABS_SKILL_DIR && python3 -B select.py
--ids IDS`. Keep selector stdout in the tool result; do not redirect it, pipe it,
wrap it in another program, or print a fabricated receipt. The frozen native
State supplies enough output capacity for the complete JSON. If successful
selector stdout is truncated, report a setup failure; do not change State or
rerun a successful selection. The selector enforces frozen entry and UTF-8 byte budgets
and returns the complete selected guidance and its hashes. If it reports only a
selection-budget error, choose fewer entries and retry before solving; otherwise
report the error. Exactly one successful selection is required. Observe the whole
successful output before business API calls or writing the answer. Never read
entry bodies separately to bypass the budget. The native trace retains the receipt;
you do not need to create another receipt file.
The library is frozen: do not edit it, learn from test feedback, or fetch training
answers. Current task instructions and observed facts take precedence over a
procedure whose preconditions are not met. Empty guidance is normal cold start.
'''

SELECTOR = '''#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path

def select(root, ids):
    index = json.loads((root / "index.json").read_text())
    assert len(ids) == len(set(ids)), "duplicate selection"
    assert len(ids) <= index["max_entries"], "entry budget exceeded"
    available = {e["id"]: e for e in index["entries"]}
    parts, hashes = [], {}
    for key in ids:
        e = available[key]
        path = root / e["file"]
        assert not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == e["sha256"], "frozen entry changed"
        parts.append(raw.decode())
        hashes[key] = e["sha256"]
    text = "\\n\\n".join(parts)
    assert len(text.encode()) <= index["max_bytes"], "context budget exceeded; select fewer entries"
    return {"selected": ids, "entry_hashes": hashes, "text": text,
            "bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ids", required=True)
    args = p.parse_args()
    print(json.dumps(select(Path(__file__).resolve().parent,
                           args.ids.split(",") if args.ids else []), ensure_ascii=False))
'''


def validate_index_readability(raw):
    """Match native read_file's 2000 UTF-16-code-unit physical line limit.

    Check actual JSON, including indentation/quoting/escaping. Python's
    splitlines() is unsuitable here: JS read_file splits bytes only on LF, so
    an embedded U+2028 separator is still part of the same physical line.
    """
    for number, line in enumerate(raw.decode("utf-8").split("\n"), 1):
        require(len(line.encode("utf-16-le")) // 2 <= 2000,
                f"index.json line {number} exceeds native read_file's 2000 UTF-16 unit limit; shorten trigger/scope metadata")


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
    require(type(req["max_entries"]) is int and 0 < req["max_entries"] <= 5, "invalid injection count")
    require(type(req["max_bytes"]) is int and req["max_bytes"] > 0, "invalid byte budget")
    dest = Path(req["output"])
    require(not dest.is_symlink(), "export destination is a symlink")
    check_prior_publication(dest)
    placeholder = export_placeholder(dest) if dest.exists() else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".skill-") as tmp:
        stage = Path(tmp) / "skill"
        stage.mkdir()
        index = {"schema": VERSION, "max_entries": req["max_entries"], "max_bytes": req["max_bytes"], "entries": []}
        for e in sorted(entries, key=lambda item: item["id"]):
            path = f"{e['level']}/{e['id']}.md"
            raw = (f"# {e['id']}\n\nTrigger: {e['trigger']}\nScope: {e['scope']}\n\n{e['body']}\n").encode()
            require(len(raw) <= req["max_bytes"], "entry cannot fit injection budget")
            write_once(stage / path, raw)
            index["entries"].append({"id": e["id"], "level": e["level"], "trigger": e["trigger"],
                                     "scope": e["scope"], "file": path, "sha256": digest(raw)})
        write_once(stage / "SKILL.md", READER.encode())
        write_once(stage / "select.py", SELECTOR.encode())
        index_raw = encoded(index)
        validate_index_readability(index_raw)
        write_once(stage / "index.json", index_raw)
        publish_skill(stage, dest, placeholder)
    return {"files": filemap(dest), "sha256": digest(encoded(filemap(dest))), "empty": not entries}


def validate_package(root):
    root = Path(root)
    files = filemap(root)
    require((root / "SKILL.md").read_text() == READER, "learned reader changed")
    require((root / "select.py").read_text() == SELECTOR, "learned executable changed")
    index_raw = regular(root / "index.json").read_bytes()
    validate_index_readability(index_raw)
    index = json.loads(index_raw)
    require(index["schema"] == VERSION and type(index["max_entries"]) is int
            and 0 < index["max_entries"] <= 5 and type(index["max_bytes"]) is int
            and index["max_bytes"] > 0, "invalid selection budgets")
    allowed, ids = {"SKILL.md", "select.py", "index.json"}, set()
    for e in index["entries"]:
        ident(e["id"])
        require(e["id"] not in ids, "duplicate package entry")
        ids.add(e["id"])
        require(e["level"] in ("general", "topic"), "invalid level")
        require(e["file"] == f"{e['level']}/{e['id']}.md", "invalid entry path")
        require(files.get(e["file"]) == e["sha256"], "entry fingerprint mismatch")
        require((root / e["file"]).stat().st_size <= index["max_bytes"], "entry exceeds byte budget")
        allowed.add(e["file"])
    require(set(files) == allowed, "unlisted support file or raw evidence in learned package")
    return {"files": files, "sha256": digest(encoded(files)), "empty": not ids}


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
               "seal-training": seal_training,
               "file-digest": lambda r: {"file_path": r["path"], "sha256": digest(regular(r["path"]).read_bytes())},
               "export": export_skill, "journal": journal,
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
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(2)
