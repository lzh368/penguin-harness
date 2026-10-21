#!/usr/bin/env python3
"""Single-operation Evo-Harness tools. No model calls or experiment scheduler.

Invoke with python3 -B evo.py REQUEST.json. Requests/results are JSON. Private
contexts and journals stay outside the exported, data-only skill directory.
"""
import sys
sys.dont_write_bytecode = True

import contextlib
import ast
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
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


def trace_view(paths, expected, path_map=None):
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


def verify_material_records(view, records):
    # Build this once per trace. Searching every output anew for every chunk
    # made a one-megabyte captured context take seconds to audit, and five
    # contexts could exceed the native hook timeout during sealing/freezing.
    indexed = {}
    positions = observation_positions(view)
    for pair, (_, observed) in zip(view["pairs"], positions):
        if not helper_observation(pair, view):
            continue
        for value in json_values(pair["observation"]["payload"]["output"]):
            if not isinstance(value, dict):
                continue
            key = (value.get("material_path"), value.get("sha256"), value.get("offset"))
            if isinstance(key[0], str) and isinstance(key[1], str) and type(key[2]) is int:
                indexed.setdefault(key, []).append((value, observed))
    receipts = []
    last_read = -1
    for path, content, fingerprint in records:
        # Complete fixed-size chunks must be present as actual tool outputs.
        for offset in range(0, max(len(content), 1), 4000):
            end = min(offset + 4000, len(content))
            expected = {"material_path": str(path), "sha256": fingerprint, "offset": offset,
                        "end": end, "total": len(content), "text": content[offset:end]}
            observed = [position for value, position in indexed.get((str(path), fingerprint, offset), []) if value == expected]
            require(observed, f"material not fully read: {path} offset {offset}")
            last_read = max(last_read, min(observed))
        receipts.append({"path": str(path), "sha256": fingerprint})
    return receipts, last_read


def material_records(paths, path_map=None):
    records = []
    for path in paths:
        raw = regular((path_map or {}).get(str(path), path)).read_bytes()
        records.append((str(path), raw.decode(), digest(raw)))
    return records


def verify_reads(view, paths, path_map=None):
    return verify_material_records(view, material_records(paths, path_map))[0]


def audit_role(req):
    view = trace_view(req["traces"], req["binding"], req.get("path_map"))
    reads, last_read = verify_material_records(view, material_records(req["materials"], req.get("path_map")))
    outputs = []
    positions = observation_positions(view)
    for path in req.get("outputs", []):
        receipt = {"file_path": path, "sha256": digest(regular(req.get("path_map", {}).get(path, path)).read_bytes())}
        require(any(called > last_read and helper_observation(pair, view)
                    and contains_json(pair["observation"]["payload"]["output"], receipt)
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


def seal_training(req):
    root = Path(req["private_learning"]).resolve()
    def rel(path):
        p = regular(path).resolve()
        require(p.is_relative_to(root), "private evidence must stay under private_learning")
        return p.relative_to(root).as_posix()
    contexts = [read(p) for p in req["contexts"]]
    validated = validate_contexts(contexts, req["train_ids"])
    training_root = verify_training_root(contexts, req["roles"])
    validate_candidates(read(req["candidates"]), validated)
    roles, trace_paths, reflected = [], set(), []
    sessions = {c["trace"]["binding"]["session_id"] for c in contexts}
    for role in req["roles"]:
        audit_role(role)
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
        verify_reads(view, req["inputs"])
    # The result must originate from the exact single-operation helper call.
    judge_receipt = None
    if fb.get("source") != "penguin-evaluation":
        require(req["answer"] == read(req["judge_request"])["answer"]
                and req["feedback"] == fb["feedback_path"], "capture paths differ from judged attempt")
        judge_receipt = verify_judge_evidence(view, fb, req["judge_request"])
        require(fb["request"]["answer"] == json.loads(answer_bytes), "judge submitted a different answer")
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
                verify_material_records(actual, [(s["source"], s["text"], s["sha256"]) for s in c["inputs"]])
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
version: 2026.10.21.3
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


def export_skill(req):
    entries = read(req["library"])
    validate_entries(entries, req["limits"])
    require(type(req["max_entries"]) is int and 0 < req["max_entries"] <= 5, "invalid injection count")
    require(type(req["max_bytes"]) is int and req["max_bytes"] > 0, "invalid byte budget")
    dest = Path(req["output"])
    require(not dest.exists(), "export destination already exists")
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
        os.rename(stage, dest)
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
