#!/usr/bin/env python3
"""Local WikiSkill bookkeeping. Never runs a model or touches Penguin live State.

Paths are confined to --out and symlinks are rejected. This is integrity checking,
not a sandbox against a hostile concurrent process. Requires Python 3.10+ on Unix.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                      default=lambda item: str(item) if isinstance(item, Decimal) else _unsupported(item)) + "\n"


def _unsupported(item):
    raise TypeError(f"cannot serialize {type(item).__name__}")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def score(value, maximum):
    require(not isinstance(value, bool), "score must be a number")
    try:
        result = Decimal(str(value))
        maximum = Decimal(str(maximum))
    except (ValueError, TypeError, InvalidOperation):
        raise Invalid("score must be a number") from None
    require(result.is_finite() and 0 <= result <= maximum,
            f"score must be finite and in 0..{maximum}")
    return result


def no_symlink_chain(path):
    for part in [path, *path.parents]:
        require(not part.is_symlink(), f"symlink refused: {part}")


class Store:
    def __init__(self, out, create=False):
        self.root = Path(os.path.abspath(out))
        no_symlink_chain(self.root)
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        require(self.root.is_dir(), "OUT must be an existing directory")

    def path(self, value):
        require(isinstance(value, str) and value and "\x00" not in value,
                "path must be a nonempty string")
        raw = Path(value)
        require(".." not in raw.parts, "path traversal refused")
        path = raw if raw.is_absolute() else self.root / raw
        path = Path(os.path.abspath(path))
        require(path.is_relative_to(self.root) and path != self.root,
                "path must be inside OUT")
        no_symlink_chain(path)
        return path

    def relative(self, value):
        return self.path(value).relative_to(self.root).as_posix()

    def read(self, value):
        path = self.path(value)
        require(path.is_file(), f"missing regular file: {value}")
        return path.read_text(encoding="utf-8")

    def json(self, value):
        return json.loads(self.read(value), object_pairs_hook=unique_keys)

    def digest(self, value):
        path = self.path(value)
        require(path.is_file(), f"missing regular file: {value}")
        return sha(path.read_bytes())

    def atomic(self, value, text):
        path = self.path(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        no_symlink_chain(path.parent)
        # Keep crash-left temporary files outside immutable skill/candidate trees.
        # OUT and its managed directories must be on the same filesystem.
        fd, temporary = tempfile.mkstemp(prefix=".wikiskill-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            parent_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def transaction(self, writes, directories=()):
        require(not self.path(".transaction.json").exists(), "pending transaction; run recover")
        require("state.json" in writes, "transaction must commit state.json")
        before = {}
        for key, value in writes.items():
            require(isinstance(value, str), "transaction values must be text")
            path = self.path(key)
            before[key] = self.digest(key) if path.exists() else None
        journal = {"format": 1, "writes": writes, "before": before,
                   "directories": list(directories)}
        self.atomic(".transaction.json", dump(journal))
        self.recover()

    def recover(self):
        journal_path = self.path(".transaction.json")
        if not journal_path.exists():
            return False
        journal = self.json(".transaction.json")
        require(journal.get("format") == 1, "unknown transaction format")
        writes = journal["writes"]
        require(isinstance(writes, dict) and "state.json" in writes,
                "invalid transaction writes")
        for key, value in writes.items():
            require(key not in (".transaction.json", ".lock") and isinstance(value, str),
                    "invalid transaction destination")
            path = self.path(key)
            current = self.digest(key) if path.exists() else None
            require(current in (journal["before"][key], sha(value)),
                    f"transaction conflicts with an external edit: {key}")
        for directory in journal["directories"]:
            self.path(directory).mkdir(parents=True, exist_ok=True)
        for key in [key for key in writes if key != "state.json"] + ["state.json"]:
            if not self.path(key).exists() or self.digest(key) != sha(writes[key]):
                self.atomic(key, writes[key])
        journal_path.unlink()
        root_fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        return True

    @contextlib.contextmanager
    def lock(self):
        path = self.path(".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def tree(self, relative):
        base = self.path(relative)
        require(base.is_dir(), f"missing directory: {relative}")
        files = {}
        for parent, dirs, names in os.walk(base, followlinks=False):
            for name in dirs + names:
                child = Path(parent) / name
                require(not child.is_symlink(), f"symlink refused: {child}")
            for name in names:
                path = Path(parent) / name
                require(path.is_file(), f"not a regular file: {path}")
                files[path.relative_to(base).as_posix()] = path.read_text(encoding="utf-8")
        return files


SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
CANDIDATE = re.compile(r"c[0-9]{4,}\Z")


def validate_skill(slug, skill_md, purpose_md):
    require(SLUG.fullmatch(slug), "skill must be a lowercase hyphenated slug")
    require(isinstance(skill_md, str) and isinstance(purpose_md, str) and purpose_md.strip(),
            "SKILL.md and nonempty PURPOSE.md are required")
    require(all(marker not in skill_md for marker in
                ("<!-- WIKISKILL_DERIVED_BEGIN -->", "<!-- WIKISKILL_DERIVED_END -->")),
            "SKILL.md must not contain reserved WIKISKILL_DERIVED boundary markers")
    lines = skill_md.splitlines()
    require(lines and lines[0] == "---", "SKILL.md must start with YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise Invalid("SKILL.md frontmatter is not closed") from None
    fields = {}
    for line in lines[1:end]:
        match = re.match(r"^(name|description):\s*(.*?)\s*$", line)
        if match:
            key, value = match.groups()
            require(key not in fields, f"duplicate frontmatter {key}")
            fields[key] = value.strip("\"'")
    require(fields.get("name") == slug, "frontmatter name must equal skill slug")
    require(fields.get("description", "").strip() not in ("", "|", ">", "|-", ">-"),
            "description must be a nonempty single-line frontmatter value")
    require("\n".join(lines[end + 1:]).strip(), "SKILL.md body cannot be empty")


def validate_tree(files):
    skills = set()
    for key in files:
        parts = key.split("/")
        require(len(parts) == 2 and SLUG.fullmatch(parts[0]) and
                parts[1] in ("SKILL.md", "PURPOSE.md"), f"unexpected skill file: {key}")
        skills.add(parts[0])
    for slug in skills:
        require(f"{slug}/SKILL.md" in files and f"{slug}/PURPOSE.md" in files,
                f"incomplete skill: {slug}")
        validate_skill(slug, files[f"{slug}/SKILL.md"], files[f"{slug}/PURPOSE.md"])


def manifest(files):
    return {name: sha(text) for name, text in sorted(files.items())}


def tree_info(files):
    hashes = manifest(files)
    return {"manifest": hashes, "sha256": sha(dump(hashes))}


def full_diff(old, new):
    result = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key, ""), new.get(key, "")
        if before == after:
            continue
        # Split/rejoin keeps patch boundaries readable for text lacking a final LF.
        before_lines, after_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
        for line in difflib.unified_diff(before_lines, after_lines,
                                         fromfile=f"accepted/{key}", tofile=f"candidate/{key}",
                                         n=max(len(before_lines), len(after_lines))):
            result.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(result)


def load_verified(store):
    require(not store.path(".transaction.json").exists(),
            "incomplete transaction detected; run recover before continuing")
    state = store.json("state.json")
    require(state.get("format_version") == 1, "unknown state format")
    require(isinstance(state["round"], int) and state["round"] >= 0, "invalid round")
    maximum = Decimal(str(state["score_max"]))
    require(maximum.is_finite() and maximum > 0, "invalid score_max")
    best = score(state["baseline_score"], maximum)
    for parent, dirs, names in os.walk(store.root, followlinks=False):
        for name in dirs + names:
            require(not (Path(parent) / name).is_symlink(), "OUT contains a symlink")
    for path in ("wiki/index.md", "wiki/logs.md", "wiki/skill-impact.md"):
        store.read(path)
    require(store.path("wiki/patterns").is_dir(), "missing wiki/patterns")
    accepted = store.tree("accepted/skills")
    validate_tree(accepted)
    require(tree_info(accepted) == state["accepted"], "accepted content hash mismatch")
    known = state["candidates"]
    actual = {p.name for p in store.path("candidates").iterdir()}
    require(actual == set(known), "unregistered or missing candidate directory")
    for candidate, record in known.items():
        require(CANDIDATE.fullmatch(candidate), "invalid candidate id")
        prefix = f"candidates/{candidate}"
        require(store.digest(f"{prefix}/meta.json") == record["meta_sha256"],
                f"candidate metadata hash mismatch: {candidate}")
        meta = store.json(f"{prefix}/meta.json")
        contents = store.tree(f"{prefix}/skills")
        validate_tree(contents)
        require(tree_info(contents) == meta["content"], f"candidate hash mismatch: {candidate}")
        require(meta["candidate_id"] == candidate and meta["round"] <= state["round"],
                "candidate identity mismatch")
        require(store.digest(f"{prefix}/diff.patch") == meta["diff_sha256"], "diff hash mismatch")
        require(store.digest(f"{prefix}/proposal.json") == meta["proposal_sha256"], "proposal hash mismatch")
        for evidence, digest in meta["evidence"].items():
            require(evidence.startswith("raw/") and store.digest(evidence) == digest,
                    f"raw evidence changed: {evidence}")
    pending = state["pending"]
    require(pending is None or pending in known, "unknown pending candidate")
    require([key for key, rec in known.items() if rec["status"] == "pending"] ==
            ([] if pending is None else [pending]), "pending status mismatch")
    accepted_hash = tree_info({})["sha256"]
    seen = set()
    for decision in state["decisions"]:
        evaluation_id = decision["evaluation_id"]
        require(evaluation_id not in seen, "duplicate evaluation id")
        seen.add(evaluation_id)
        value = score(decision["score"], maximum)
        require(score(decision.get("previous_best"), maximum) == best,
                "decision previous_best disagrees with history")
        improves = value > best
        require(decision["accepted"] is improves, "decision disagrees with strict improvement")
        candidate = decision["candidate_id"]
        require(known[candidate]["status"] == ("accepted" if improves else "rejected"),
                "decision/status mismatch")
        if improves:
            best = value
            accepted_hash = store.json(f"candidates/{candidate}/meta.json")["content"]["sha256"]
    decided = [item["candidate_id"] for item in state["decisions"]]
    require(len(decided) == len(set(decided)) and set(decided) ==
            {key for key, record in known.items() if record["status"] != "pending"},
            "decided candidates and decision records disagree")
    rounds = [store.json(f"candidates/{key}/meta.json")["round"] for key in known]
    rounds += [item["round"] for item in state["no_actions"]]
    require(sorted(rounds) == list(range(1, state["round"] + 1)), "round history has gaps or duplicates")
    if pending:
        require(store.json(f"candidates/{pending}/meta.json")["base"] == state["accepted"],
                "pending candidate no longer matches accepted base")
    require(score(state["best_score"], maximum) == best and state["accepted"]["sha256"] == accepted_hash,
            "best score or accepted package disagrees with decisions")
    require(all(record["status"] in ("pending", "accepted", "rejected") for record in known.values()),
            "invalid candidate status")
    return state


def init(store, args):
    require(not store.path("state.json").exists(), "OUT already initialized")
    require(not store.path(".transaction.json").exists(), "incomplete init; run recover")
    managed = ("accepted", "candidates", "wiki", "raw", "proposals", "rendered")
    require(not any(store.path(name).exists() for name in managed),
            "init refuses existing managed directories: accepted/candidates/wiki/raw/proposals/rendered")
    for parent, dirs, names in os.walk(store.root, followlinks=False):
        for name in dirs + names:
            require(not (Path(parent) / name).is_symlink(), "OUT contains a symlink")
    maximum = Decimal(str(args.score_max))
    require(maximum.is_finite() and maximum > 0, "score_max must be finite and positive")
    baseline = score(args.baseline_score, maximum)
    state = {"format_version": 1, "round": 0, "baseline_score": baseline,
             "best_score": baseline, "score_max": maximum, "accepted": tree_info({}),
             "pending": None, "candidates": {}, "decisions": [], "no_actions": []}
    writes = {"state.json": dump(state), "wiki/index.md": "# Wiki\n",
              "wiki/logs.md": f"# Round log\n\nInitialized {now()}; observed empty-skill baseline: {baseline}/{maximum}.\n",
              "wiki/skill-impact.md": "# Skill impact\n"}
    store.transaction(writes, ("accepted/skills", "candidates", "wiki/patterns", "raw", "proposals"))
    return {"status": "initialized", "round": 0, "best_score": baseline}


def stage(store, state, args):
    require(state["pending"] is None, "pending candidate must be decided first")
    proposal_path = store.relative(args.proposal)
    require(proposal_path.startswith("proposals/"), "proposal must be under OUT/proposals/")
    proposal = store.json(proposal_path)
    require(isinstance(proposal, dict), "proposal must be an object")
    action, slug = proposal.get("action"), proposal.get("skill")
    require(action in ("create", "patch") and isinstance(slug, str) and SLUG.fullmatch(slug),
            "proposal needs action create|patch and one lowercase skill slug")
    rationale = proposal.get("rationale")
    require(isinstance(rationale, str) and rationale.strip(), "rationale is required")
    evidence = proposal.get("evidence")
    require(isinstance(evidence, list) and len(evidence) >= 4, "at least four raw evidence files required")
    paths = [store.relative(path) for path in evidence]
    require(len(set(paths)) == len(paths) and all(path.startswith("raw/") for path in paths),
            "evidence must name distinct files under raw/")
    hashes = {path: store.digest(path) for path in paths}
    old = store.tree("accepted/skills")
    new = dict(old)
    if action == "create":
        require(f"{slug}/SKILL.md" not in old, "cannot create an existing skill")
        require("edits" not in proposal, "create cannot include edits")
        new[f"{slug}/SKILL.md"] = proposal.get("skill_md")
        new[f"{slug}/PURPOSE.md"] = proposal.get("purpose_md")
    else:
        require(f"{slug}/SKILL.md" in old, "patch requires an existing skill")
        require("skill_md" not in proposal and "purpose_md" not in proposal,
                "patch must use anchored edits")
        edits = proposal.get("edits")
        require(isinstance(edits, list) and edits, "patch needs nonempty edits")
        for edit in edits:
            require(isinstance(edit, dict) and edit.get("file") in ("SKILL.md", "PURPOSE.md"),
                    "patch file must be SKILL.md or PURPOSE.md")
            operation, text = edit.get("op"), edit.get("text")
            require(operation in ("append", "replace", "insert_after") and isinstance(text, str),
                    "invalid edit operation/text")
            key = f"{slug}/{edit['file']}"
            if operation == "append":
                require("old" not in edit, "append does not take old")
                new[key] += text
            else:
                anchor = edit.get("old")
                require(isinstance(anchor, str) and anchor and new[key].count(anchor) == 1,
                        "old must be a nonempty unique exact anchor")
                new[key] = new[key].replace(anchor, text if operation == "replace" else anchor + text, 1)
    validate_tree(new)
    require(old.get(f"{slug}/SKILL.md", "").strip() != new[f"{slug}/SKILL.md"].strip(),
            "proposal must change SKILL.md content, not only PURPOSE.md or trailing whitespace; use no-action")
    round_number = state["round"] + 1
    candidate = f"c{round_number:04d}"
    prefix = f"candidates/{candidate}"
    difference = full_diff(old, new)
    proposal_text = dump(proposal)
    meta = {"candidate_id": candidate, "round": round_number, "action": action,
            "skill": slug, "rationale": rationale, "evidence": hashes,
            "base": state["accepted"], "content": tree_info(new),
            "diff_sha256": sha(difference), "proposal_sha256": sha(proposal_text), "created": now()}
    meta_text = dump(meta)
    state["round"], state["pending"] = round_number, candidate
    state["candidates"][candidate] = {"status": "pending", "meta_sha256": sha(meta_text)}
    writes = {f"{prefix}/skills/{key}": value for key, value in new.items()}
    writes.update({f"{prefix}/meta.json": meta_text, f"{prefix}/proposal.json": proposal_text,
                   f"{prefix}/diff.patch": difference, "state.json": dump(state),
                   "wiki/logs.md": store.read("wiki/logs.md") +
                   f"\n## Round {round_number}: staged {candidate}\n\n{action} `{slug}`; awaiting validation.\n"})
    store.transaction(writes)
    return {"status": "pending", "candidate_id": candidate, "round": round_number,
            "sha256": meta["content"]["sha256"], "manifest": meta["content"]["manifest"],
            "diff": f"{prefix}/diff.patch", "skills": f"{prefix}/skills"}


def decide(store, state, args):
    value = score(args.score, state["score_max"])
    require(args.evaluation_id.strip(), "evaluation-id cannot be blank")
    prior = next((item for item in state["decisions"]
                  if item["evaluation_id"] == args.evaluation_id), None)
    if prior:
        require(score(prior["score"], state["score_max"]) == value and args.candidate == prior["candidate_id"],
                "evaluation-id reused with a different score or candidate")
        return {"status": "already_decided", **prior}
    candidate = args.candidate
    require(candidate is not None and candidate == state["pending"], "candidate is not pending")
    meta = store.json(f"candidates/{candidate}/meta.json")
    require(meta["base"] == state["accepted"], "accepted base changed during validation")
    improves = value > score(state["best_score"], state["score_max"])
    decision = {"candidate_id": candidate, "evaluation_id": args.evaluation_id,
                "score": value, "previous_best": state["best_score"], "accepted": improves, "time": now()}
    writes = {}
    if improves:
        contents = store.tree(f"candidates/{candidate}/skills")
        writes.update({f"accepted/skills/{key}": text for key, text in contents.items()})
        state["accepted"], state["best_score"] = meta["content"], value
    state["candidates"][candidate]["status"] = "accepted" if improves else "rejected"
    state["pending"] = None
    state["decisions"].append(decision)
    outcome = "ACCEPT" if improves else "REJECT"
    impact = (f"\n## Round {meta['round']}: {candidate} — {outcome}\n\n"
              f"Evaluation: {args.evaluation_id}\nScore: {value}/{state['score_max']}\n"
              f"Previous best: {decision['previous_best']}\n\nRationale:\n{meta['rationale']}\n\n"
              "Evidence (path and SHA-256):\n" +
              "".join(f"- `{path}`: `{digest}`\n" for path, digest in meta["evidence"].items()) +
              "\nFull diff:\n\n" + store.read(f"candidates/{candidate}/diff.patch") + "\n")
    writes.update({"state.json": dump(state),
                   "wiki/skill-impact.md": store.read("wiki/skill-impact.md") + impact,
                   "wiki/logs.md": store.read("wiki/logs.md") +
                   f"\nRound {meta['round']} complete: {candidate} {outcome}; score {value}; Wiki retained.\n"})
    store.transaction(writes)
    return {"status": "decided", **decision, "best_score": state["best_score"]}


def no_action(store, state, args):
    require(state["pending"] is None, "cannot no-action while a candidate is pending")
    require(args.reason.strip(), "reason cannot be blank")
    state["round"] += 1
    entry = {"round": state["round"], "reason": args.reason, "time": now()}
    state["no_actions"].append(entry)
    store.transaction({"state.json": dump(state), "wiki/logs.md": store.read("wiki/logs.md") +
                       f"\n## Round {entry['round']}: no action\n\n{args.reason}\n\n"
                       "No validation; accepted skills and score unchanged.\n"})
    return {"status": "no_action", "round": state["round"], "best_score": state["best_score"]}


def render_text(files, block=False):
    """Deterministic skill-only injection; PURPOSE is never given to Target."""
    parts = []
    for name, body in sorted(files.items()):
        if name.endswith("/SKILL.md"):
            slug = name.split("/")[0]
            newline = "" if body.endswith("\n") else "\n"
            parts.append(f"<!-- BEGIN WIKISKILL {slug} -->\n{body}{newline}<!-- END WIKISKILL {slug} -->\n")
    text = "\n".join(parts)
    if block:
        text = "<!-- WIKISKILL_DERIVED_BEGIN -->\n" + text + "<!-- WIKISKILL_DERIVED_END -->"
    return text


def render(store, state, args):
    source = args.source
    require(source == "accepted" or source in state["candidates"], "unknown render source")
    directory = "accepted/skills" if source == "accepted" else f"candidates/{source}/skills"
    files = store.tree(directory)
    text = render_text(files, block=getattr(args, "block", False))
    if args.output:
        destination = store.relative(args.output)
        require(destination.startswith("rendered/"), "--output must be under OUT/rendered/")
        store.atomic(destination, text)
        return {"status": "rendered", "source": source, "output": destination, "sha256": sha(text)}
    sys.stdout.write(text)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "stage", "decide", "no-action", "render", "verify", "recover"):
        sub = commands.add_parser(name)
        sub.add_argument("--out", required=True)
        if name == "init":
            sub.add_argument("--baseline-score", required=True)
            sub.add_argument("--score-max", default="100")
        elif name == "stage":
            sub.add_argument("--proposal", required=True, help="JSON file inside OUT")
        elif name == "decide":
            sub.add_argument("--score", required=True)
            sub.add_argument("--evaluation-id", required=True)
            sub.add_argument("--candidate", required=True)
        elif name == "no-action":
            sub.add_argument("--reason", required=True)
        elif name == "render":
            sub.add_argument("--source", required=True)
            sub.add_argument("--block", action="store_true", help="include the canonical WIKISKILL_DERIVED boundary markers")
            sub.add_argument("--output", help="optional destination under OUT/rendered/")
    args = parser.parse_args()
    try:
        store = Store(args.out, create=args.command == "init")
        with store.lock():
            if args.command == "init":
                result = init(store, args)
            elif args.command == "recover":
                recovered = store.recover()
                state = load_verified(store)
                result = {"status": "recovered" if recovered else "clean", "round": state["round"]}
            else:
                state = load_verified(store)
                if args.command == "verify":
                    result = {"status": "valid", "round": state["round"],
                              "pending": state["pending"], "best_score": state["best_score"],
                              "accepted_sha256": state["accepted"]["sha256"]}
                else:
                    result = {"stage": stage, "decide": decide, "no-action": no_action,
                              "render": render}[args.command](store, copy.deepcopy(state), args)
            if result is not None:
                sys.stdout.write(dump(result))
        return 0
    except (Invalid, InvalidOperation, OSError, ValueError, KeyError, TypeError) as error:
        sys.stderr.write(dump({"status": "error", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
