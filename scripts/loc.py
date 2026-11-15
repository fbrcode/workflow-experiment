"""Count each implementation's lines by role, for C4 (authoring effort).

    make loc     # writes docs/comparison/data/loc-<date>.json and prints a table

Code lines exclude blank lines, comments and Python docstrings (found with `tokenize` and `ast`).
In YAML, shell, Dockerfiles and TOML a comment is a line that starts with `#`; block scalars
(SQL and commands inside the Kestra flows) count as code. The buckets are defined below, so a
reader can check what was put where.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
import tokenize
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING}

# role -> files. The roles line up across the tools: Kestra keeps its SQL and service calls in the
# flow YAML, Prefect in sql.py and clients.py, so those count as orchestration on both sides.
BUCKETS: dict[str, dict[str, list[str]]] = {
    "kestra": {
        "orchestration": ["kestra/flows/*.yml"],
        "ported n8n logic": [
            "kestra/files/summarizer/imap_simple.py",
            "kestra/files/summarizer/ported.py",
            "kestra/files/summarizer/jscompat.py",
        ],
        "glue": ["kestra/files/summarizer/task.py", "kestra/files/summarizer/__init__.py"],
        "stack and deployment": [
            "kestra/compose.yaml",
            "kestra/Dockerfile",
            "kestra/deploy.sh",
            "kestra/secrets-entrypoint.sh",
        ],
        "unit tests": ["kestra/tests/**/*.py"],
        "run helper": ["scripts/kestra_run.py"],
    },
    "prefect": {
        "orchestration": [
            "prefect/src/email_summarizer/flows.py",
            "prefect/src/email_summarizer/sql.py",
            "prefect/src/email_summarizer/clients.py",
        ],
        "ported n8n logic": [
            "prefect/src/email_summarizer/imap.py",
            "prefect/src/email_summarizer/ingest.py",
            "prefect/src/email_summarizer/summarize.py",
            "prefect/src/email_summarizer/markdown.py",
            "prefect/src/email_summarizer/js.py",
        ],
        "glue": [
            "prefect/src/email_summarizer/config.py",
            "prefect/src/email_summarizer/bootstrap.py",
            "prefect/src/email_summarizer/__init__.py",
        ],
        "stack and deployment": [
            "prefect/compose.yaml",
            "prefect/Dockerfile",
            "prefect/deploy.sh",
            "prefect/prefect.yaml",
        ],
        "unit tests": ["prefect/tests/**/*.py"],
        "run helper": ["scripts/prefect_run.py"],
    },
}
DRIVERS = {"kestra": "KestraDriver", "prefect": "PrefectDriver"}  # tests/parity/harness/drivers.py


def python_lines(text: str) -> tuple[int, int]:
    """(code, comment) line counts; docstrings count as comments."""
    docstrings: set[int] = set()
    for node in ast.walk(ast.parse(text)):
        body = getattr(node, "body", None)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    code: set[int] = set()
    comment: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            comment.add(token.start[0])
        elif token.type not in SKIP:
            for line in range(token.start[0], token.end[0] + 1):
                (comment if line in docstrings else code).add(line)
    return len(code), len(comment - code)


def count(text: str, python: bool) -> dict[str, int]:
    lines = text.splitlines()
    blank = sum(not line.strip() for line in lines)
    if python:
        code, comment = python_lines(text)
    else:
        comment = sum(line.strip().startswith("#") for line in lines)
        code = len(lines) - blank - comment
    return {"lines": len(lines), "code": code, "comment": comment, "blank": blank}


def driver_source(tool: str) -> str:
    path = REPO / "tests" / "parity" / "harness" / "drivers.py"
    text = path.read_text()
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.ClassDef) and n.name == DRIVERS[tool])
    return "\n".join(text.splitlines()[node.lineno - 1 : node.end_lineno])


def measure() -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for tool, buckets in BUCKETS.items():
        result[tool] = {}
        for role, patterns in buckets.items():
            files = sorted({p for pattern in patterns for p in REPO.glob(pattern) if p.is_file()})
            per_file = {str(p.relative_to(REPO)): count(p.read_text(), p.suffix == ".py") for p in files}
            totals = {k: sum(f[k] for f in per_file.values()) for k in ("lines", "code", "comment", "blank")}
            result[tool][role] = {**totals, "files": per_file}
        source = driver_source(tool)
        result[tool]["parity driver"] = {
            **count(source, python=True),
            "files": {f"tests/parity/harness/drivers.py::{DRIVERS[tool]}": count(source, python=True)},
        }
    return result


def main() -> int:
    now = datetime.now().astimezone()
    commit = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    counts = measure()
    document = {
        "date": now.isoformat(timespec="seconds"),
        "commit": commit,
        "command": "make loc",
        "method": __doc__.strip().splitlines()[2:],
        "buckets": BUCKETS,
        "counts": counts,
    }
    out = REPO / "docs" / "comparison" / "data" / f"loc-{now:%Y-%m-%d}.json"
    out.write_text(json.dumps(document, indent=2) + "\n")

    roles = list(dict.fromkeys(role for tool in counts.values() for role in tool))
    print(f"{'code lines (all lines)':28}" + "".join(f"{tool:>18}" for tool in counts))
    for role in roles:
        cells = "".join(f"{f'{c[role]["code"]} ({c[role]["lines"]})':>18}" for c in counts.values())
        print(f"{role:28}{cells}")
    print(f"wrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
