"""Shared literal read-only inspection grammar; never executes a command.

Defaults describe a Target's public input directory. Training may supply an
exact granted file set and its one member input root, never all train members.
This is cooperative trace evidence validation, not a general shell sandbox.
"""
import json
from pathlib import PurePosixPath
import re
import shlex


def require(value, message):
    if not value:
        raise ValueError(message)


def _path(value, workspace):
    require(isinstance(value, str) and value, "missing evidence path")
    path = PurePosixPath(value)
    require(".." not in path.parts, "noncanonical evidence path")
    return str(path if path.is_absolute() else PurePosixPath(workspace) / path)


def _shell_commands(call, workspace):
    """Parse a deliberately small literal shell grammar, never execute it.

    Quotes/escapes are handled by shlex. Expansion, background execution,
    subshells, and arbitrary redirections are outside this evidence interface.
    The returned commands still need their program/operand allowlist checked.
    """
    require(call.get("name") == "exec_command", "not a shell observation")
    args = json.loads(call["arguments"])
    require(not args.get("run_in_background"), "background inspection is not a foreground read")
    command = args.get("cmd", args.get("command", ""))
    require(isinstance(command, str) and command and len(command) <= 32768
            and not any(c in command for c in "\n\r\0$`{}()"), "unsupported inspection shell syntax")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    require(tokens and len(tokens) <= 512, "empty/oversized inspection command")
    # Retain just enough quote provenance for find -name '*.json'. A second
    # non-POSIX lex preserves whole quoted words. Only an unambiguous, fully
    # quoted token at the same position may carry wildcard characters; mixed
    # quoted/unquoted concatenation is deliberately outside this grammar.
    quoted_lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|<>")
    quoted_lexer.whitespace_split = True
    quoted_lexer.commenters = ""
    quoted_tokens = list(quoted_lexer)
    # shlex removes quoting. Requiring the actual literal fd spelling prevents
    # accepting `2 > /dev/null` or `"2">/dev/null` as stderr redirection.
    stderr_count = len(re.findall(r"(?<![^\s;&|])2>\s*(?:/dev/null|'/dev/null'|\"/dev/null\")(?=$|[\s;&|])", command))
    commands, argv, patterns, previous, redirects = [], [], [], None, 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == ">":
            require(argv and argv[-1] == "2" and i + 1 < len(tokens) and tokens[i + 1] == "/dev/null",
                    "only literal stderr redirection to /dev/null is allowed")
            argv.pop(); redirects += 1; i += 2
            continue
        if token in (";", "&&", "||", "|"):
            require(argv, "empty inspection command")
            commands.append({"argv": argv, "connector": previous, "quoted_patterns": patterns})
            argv, patterns, previous = [], [], token
        else:
            require(not re.fullmatch(r"[;&|<>]+", token)
                    and not any(c in token for c in ";&|<>~"), "unsupported inspection operator/expansion")
            if any(c in token for c in "*?[]"):
                original = quoted_tokens[i] if len(quoted_tokens) == len(tokens) else ""
                require(len(original) >= 2 and original[0] in "'\"" and original[-1] == original[0]
                        and shlex.split(original) == [token], "unquoted inspection expansion")
                patterns.append(len(argv))
            argv.append(token)
        i += 1
    require(argv or previous == ";", "unfinished inspection command")
    if argv:
        commands.append({"argv": argv, "connector": previous, "quoted_patterns": patterns})
    require(redirects == stderr_count, "stderr redirection must use literal 2>/dev/null")
    return commands, _path(args.get("workdir", workspace), workspace), redirects


def _file_operands(argv):
    """Operands of cat/head; no stdin pseudo-file or option that names a file."""
    name, rest = argv[0], argv[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    elif name == "head":
        while rest and rest[0].startswith("-"):
            option = rest.pop(0)
            if option == "--":
                break
            if option in ("-n", "-c", "--lines", "--bytes"):
                require(rest and re.fullmatch(r"[0-9]+", rest[0]), "head needs a literal nonnegative bound")
                rest.pop(0)
            else:
                require(re.fullmatch(r"-(?:[nc])?[0-9]+|--(?:lines|bytes)=[0-9]+|-[qv]", option),
                        "unsupported head option")
    require(all(value != "-" and not value.startswith("-") for value in rest), "unsupported read operand")
    return rest


def _public_file(path, workspace, public_files=None):
    if public_files is not None:
        return path in public_files
    root = workspace.rstrip("/")
    return path in (root + "/instruction.md", root + "/environment_access.md") or path.startswith(root + "/input/")


def _public_name_enumeration(argv, workdir, workspace, public_input=None):
    """Whether an already validated find emits only public input file names.

    This deliberately excludes content streams, other roots, NUL output and
    repeated predicates. It is used only to authorize a following bare stdin
    sort, never as evidence that any file's contents were read.
    """
    if argv[0] != "find":
        return False
    rest, roots = list(argv[1:]), []
    while rest and not rest[0].startswith("-"):
        roots.append(rest.pop(0))
    if len(roots) != 1:
        return False
    root = _path(roots[0], workdir)
    public = public_input if public_input is not None else workspace.rstrip("/") + "/input"
    if root != public and not root.startswith(public + "/"):
        return False
    predicates = {}
    while rest:
        option = rest.pop(0)
        if option in predicates:
            return False
        if option in ("-type", "-maxdepth", "-mindepth", "-name", "-iname") and rest:
            predicates[option] = rest.pop(0)
        elif option == "-print" and not rest:
            # find actions execute at their expression position. Printing
            # before -type/-name would include names those filters exclude.
            predicates[option] = True
        else:
            return False
    return predicates.get("-type") == "f"


def _readonly_commands(call, workspace, slot, public_files=None, public_input=None, *, skill_files=None):
    """Allow only public-file reads, fixed Skill reads and discovery.

    Pipes can feed a bounded stdin head, or a bare stdin sort immediately after
    public file-name enumeration. Every branch of &&/||/; must itself be
    read-only. This is an evidence grammar, not a general shell sandbox.
    """
    commands, workdir, redirects = _shell_commands(call, workspace)
    reads = []
    fixed = {slot + "/SKILL.md"}
    if skill_files is not None:
        require(isinstance(skill_files, (set, list, tuple)) and all(isinstance(p, str) and p.startswith("/") and _path(p, workspace) == p for p in skill_files),
                "frozen Skill files must be canonical absolute paths")
        fixed.update(skill_files)
    for position, item in enumerate(commands):
        argv = item["argv"]
        program = argv[0]
        require(program in ("pwd", "ls", "find", "cat", "head", "echo", "sort"), "only literal read-only inspection may precede the complete Skill read")
        require(all(program == "find" and index >= 2 and argv[index - 1] in ("-name", "-iname")
                    for index in item["quoted_patterns"]), "quoted patterns are only find name predicates")
        if program in ("cat", "head"):
            operands = _file_operands(argv)
            require(operands or program == "head" and item["connector"] == "|", "read needs a literal file")
            paths = [_path(value, workdir) for value in operands]
            require(all(path in fixed or _public_file(path, workspace, public_files) for path in paths),
                    "inspection may not read unlisted Skill or nonpublic files")
            reads.extend(paths)
        elif program == "pwd":
            require(all(value in ("-L", "-P") for value in argv[1:]), "unsupported pwd operand")
            operands = []
        elif program == "echo":
            operands = []  # Literal strings cannot prove a file read.
        elif program == "sort":
            require(argv == ["sort"] and item["connector"] == "|" and position > 0
                    and _public_name_enumeration(commands[position - 1]["argv"], workdir, workspace, public_input),
                    "sort only accepts stdin from literal public file-name enumeration")
            operands = []
        elif program == "ls":
            operands = []
            options = True
            for value in argv[1:]:
                if options and value == "--":
                    options = False
                elif options and value.startswith("-"):
                    require(re.fullmatch(r"-[aAlhRFd1tSrp]+|--(?:all|almost-all|recursive|directory|color=(?:never|auto|always))", value),
                            "unsupported ls option")
                else:
                    operands.append(value)
        else:
            operands, tail = [], list(argv[1:])
            while tail and not tail[0].startswith("-"):
                operands.append(tail.pop(0))
            while tail:
                option = tail.pop(0)
                if option in ("-type", "-maxdepth", "-mindepth", "-name", "-iname"):
                    require(tail, "missing find predicate")
                    value = tail.pop(0)
                    require((option == "-type" and value in ("f", "d", "l"))
                            or (option in ("-maxdepth", "-mindepth") and re.fullmatch(r"[0-9]+", value))
                            or (option in ("-name", "-iname") and not value.startswith("-")), "unsupported find predicate")
                else:
                    require(option in ("-print", "-print0"), "find action may not precede the complete Skill read")
        # Directory/name discovery exposes no entry bodies. Preserve ordinary
        # pwd/ls/find discovery outside /app, including the native skills root.
        if item["connector"] == "|" and program != "sort":
            require(program == "head" and not operands, "inspection pipes only feed stdin head")
    return commands, workdir, redirects, reads


def complete_file_stdout(call, stdout, expected, target, workspace, slot,
                         public_files=None, public_input=None, *, skill_files=None):
    """Exact whole-file stdout from one final cat, optionally after discovery.

    This recognizes a finite foreground read protocol, not arbitrary shell
    effects. Native completion, image absence and observation order belong to
    the caller. The existing inspection grammar still checks every operation.
    """
    try:
        if not isinstance(stdout, str) or not isinstance(expected, bytes) or not expected:
            return False
        content = expected.decode("utf-8")
        commands, workdir, redirects, _ = _readonly_commands(
            call, workspace, slot, public_files, public_input, skill_files=skill_files)
        args = json.loads(call["arguments"])
        command = args.get("cmd", args.get("command", ""))
        if redirects:
            return False
        if any(item["connector"] != "&&" for item in commands[1:]):
            return False
        final = commands[-1]["argv"]
        if final[0] != "cat" or [_path(path, workdir) for path in _file_operands(final)] != [target]:
            return False
        for item in commands[:-1]:
            argv = item["argv"]
            if argv[0] in ("pwd", "ls"):
                continue
            if argv[0] != "echo" or not (len(argv) == 1 or len(argv) == 2 and re.fullmatch(r"[-=_.: ]{1,64}", argv[1])):
                return False
        if len(commands) == 1:
            # Preserve the prior exact single-cat form, including one terminal
            # semicolon already accepted by the shared inspection parser.
            return stdout == content
        if command.rstrip().endswith(";"):
            return False
        if not stdout.endswith(content) or stdout.find(content) != stdout.rfind(content):
            return False
        prefix = stdout[:-len(content)]
        return (not prefix or prefix.endswith("\n")) and not re.search(
            r"\[output (?:truncated:|archived[ :(])|\[tool (?:timeout:|error\])", prefix)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return False
