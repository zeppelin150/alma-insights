"""G4: removing a Guru collection from the search scope must mean "stop
searching/watching it locally" and NOTHING else.

Guru has no structural protection like Asana's missing-DELETE-verb —
``GuruClient`` carries real write methods — so this guard asserts the
scope-editing surfaces (the collections picker dialog and the enablement
Settings page) reference none of them, the same shape as the Asana
board-removal guard. AST identifiers, so names held in strings don't count.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

_GURU_WRITES = {
    "create_card", "update_card", "create_draft", "delete_draft",
    "set_draft_context", "add_draft_collaborator", "create_folder",
    "rename_folder", "move_folder", "verify_card", "unverify_card",
    "create_card_comment", "delete_card_comment",
}

_SCOPE_SURFACES = [
    _REPO / "src" / "ui" / "dialogs" / "guru_collection_picker_dialog.py",
    _REPO / "src" / "ui" / "pages" / "enablement" / "settings.py",
]


def _identifiers(path: Path) -> set:
    tree = ast.parse(path.read_text("utf-8"))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def test_scope_surfaces_reference_no_guru_write():
    for path in _SCOPE_SURFACES:
        assert path.exists(), path
        hit = _GURU_WRITES & _identifiers(path)
        assert not hit, (
            f"{path.name} references Guru write method(s) {sorted(hit)} — "
            "scope removal must be a local settings change, never a Guru write")


def test_scope_picker_still_reads_and_persists():
    """Read-only is not read-nothing: the picker must still list collections
    and persist the scope through settings_manager."""
    names = _identifiers(_SCOPE_SURFACES[0])
    assert "list_collections" in names
    src = _SCOPE_SURFACES[0].read_text("utf-8")
    assert "settings_manager" in src
    assert "search_collections" in src
