"""Check every st.<fn>(...) / container.<fn>(...) keyword argument against the installed Streamlit.

Catches API drift (e.g. kwargs added in newer Streamlit) before deploying.
    python scripts/check_streamlit_api.py
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "streamlit_app.py", *sorted((ROOT / "app_pages").glob("*.py")), *sorted((ROOT / "mediverse").glob("*.py"))]


def resolve(node: ast.AST):
    """Return the Streamlit callable for st.x / st.x.y, or a DeltaGenerator method for <var>.x."""
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "st":
            return getattr(st, node.attr, None), f"st.{node.attr}"
        if isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name) and node.value.value.id == "st":
            parent = getattr(st, node.value.attr, None)
            return getattr(parent, node.attr, None), f"st.{node.value.attr}.{node.attr}"
    return None, None


def main() -> int:
    problems = []
    for f in FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            fn, name = resolve(call.func)
            if name is None:
                continue
            if fn is None:
                problems.append(f"{f.name}:{call.lineno} {name} does not exist")
                continue
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):
                continue
            if any(p.kind == p.VAR_KEYWORD for p in params.values()):
                continue
            for kw in call.keywords:
                if kw.arg and kw.arg not in params:
                    problems.append(f"{f.name}:{call.lineno} {name}(... {kw.arg}=) not supported")
    print(f"streamlit {st.__version__}: {len(problems)} problem(s)")
    print("\n".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
