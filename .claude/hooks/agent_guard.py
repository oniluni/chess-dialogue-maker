#!/usr/bin/env python3
"""PreToolUse guard for the project's subagents (.claude/agents/*.md).

    agent_guard.py            block access to secrets (.env, *cookies*.txt) for Read/Grep/Glob/Bash/Edit/Write
    agent_guard.py --readonly additionally allow only verification commands in Bash (the QA reviewer)

Claude Code passes the tool call as JSON on stdin; exit code 2 blocks it and stderr is
shown to the agent. Exit 0 lets it through.
"""
from __future__ import annotations

import json
import re
import shlex
import sys

SECRET = re.compile(r"(?:^|[\s/'\"=])(?:\.env(?:\.(?!example\b)[\w.-]+)?|[\w.-]*cookies[\w.-]*\.txt)(?=$|[\s'\"/)])", re.I)

# --readonly: programs that only read; git limited to read subcommands; ruff only `check` without fixes
READ_PROGRAMS = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "stat", "file", "du", "diff", "sort", "uniq", "cut",
                 "jq", "echo", "pwd", "date", "tree", "ffprobe", "true", "basename", "dirname", "realpath"}
GIT_READ = {"diff", "log", "status", "show", "blame", "grep", "ls-files", "rev-parse", "describe", "shortlog"}
SAFE_REDIRECTS = re.compile(r"\s*(?:2>&1|2>\s*/dev/null|>\s*/dev/null)")


def block(msg: str) -> None:
    print(f"agent_guard: {msg}", file=sys.stderr)
    sys.exit(2)


def check_readonly(cmd: str) -> None:
    bare = SAFE_REDIRECTS.sub(" ", cmd)
    if re.search(r">|`|\$\(|<\(|\btee\b", bare):
        block("QA-ревьюер работает только на чтение: перенаправления в файл, tee и подстановки команд запрещены.")
    for seg in re.split(r"\|\||&&|;|\||\n", bare):
        seg = seg.strip()
        if not seg:
            continue
        try:
            words = shlex.split(seg)
        except ValueError:
            block(f"не удалось разобрать команду: {seg[:80]}")
        while words and re.fullmatch(r"[A-Z_][A-Z0-9_]*=\S*", words[0]):  # VAR=value prefixes
            words = words[1:]
        if not words:
            continue
        prog = words[0].rsplit("/", 1)[-1]
        if prog == "cd":
            continue
        if prog == "git":
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub not in GIT_READ:
                block(f"git {sub or '…'} не разрешён: только {', '.join(sorted(GIT_READ))}.")
            continue
        if prog == "uvx" and any(w.startswith("ruff") for w in words[1:2]):
            if "check" not in words or any(w.startswith(("--fix", "--unsafe-fixes", "--add-noqa")) for w in words):
                block("ruff разрешён только как `ruff check` без исправлений.")
            if "--no-cache" not in words:
                block("добавьте --no-cache к ruff check (иначе ruff пишет .ruff_cache).")
            continue
        if prog == "find" and not any(w in ("-delete", "-exec", "-execdir", "-ok", "-fprint", "-fprintf", "-fls") for w in words):
            continue
        if prog not in READ_PROGRAMS:
            block(f"команда `{prog}` не входит в список проверочных (QA только читает и запускает lint).")


def main() -> None:
    readonly = "--readonly" in sys.argv[1:]
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}
    text = " ".join(str(inp.get(k, "")) for k in ("command", "file_path", "path", "pattern", "glob"))
    if SECRET.search(" " + text):
        block("доступ к секретам (.env, *cookies*.txt) закрыт для субагентов — ключи и cookies не читаются и не выводятся.")
    if readonly and tool == "Bash":
        check_readonly(str(inp.get("command", "")))
    sys.exit(0)


if __name__ == "__main__":
    main()
