"""Regression: provisioning JSON step assignments must remain ORM-detectable."""
import ast
from pathlib import Path

def test_steps_assignments_are_snapshots():
    tree=ast.parse(Path('gateway/app/provision.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr in ('steps','provision_log') for t in node.targets):
            assert not (isinstance(node.value,ast.Name) and node.value.id=='steps'), 'Use list(steps): shared mutable JSON list loses subsequent changes'
