from __future__ import annotations

import ast
import re
from pathlib import Path

import coding_agent_cli

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_python_examples_compile_and_import_public_names() -> None:
    readme = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```python\s*\n(.*?)\n```", readme, re.DOTALL)
    assert blocks
    exports = set(coding_agent_cli.__all__)
    for source in blocks:
        compile(source, "README.md", "exec")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "coding_agent_cli"
            for alias in node.names
        }
        assert imported <= exports


def test_readme_documents_v1_migration_without_private_or_removed_protocol_terms() -> None:
    readme = README.read_text(encoding="utf-8")
    assert re.search(r"^## Migrating from 0\.1\s*$", readme, re.MULTILINE)
    assert "codex_rollout" not in readme
    assert "_process" not in readme
    assert not re.search(r"public\s+`?Driver`?\s+protocol", readme, re.IGNORECASE)
