import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "agterm-linux/tests/atspi_smoke.py"
HELPERS = {"collect", "named", "dismiss_toast"}
TREE = ast.parse(SOURCE.read_text())
CODE = compile(ast.Module(body=[node for node in TREE.body
                               if isinstance(node, ast.FunctionDef) and node.name in HELPERS],
                          type_ignores=[]), str(SOURCE), "exec")


class Accessible:
    def __init__(self, name="", role="panel", children=(), showing=True):
        self.name = name
        self.role = role
        self.children = list(children)
        self.showing = showing
        self.parent = None
        for child in self.children:
            child.parent = self

    def get_name(self):
        return self.name

    def get_role_name(self):
        return self.role

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_parent(self):
        return self.parent

    def get_state_set(self):
        return SimpleNamespace(contains=lambda state: self.showing)


class ToastDismissalTests(unittest.TestCase):
    def test_scoped_dismissal_and_fail_closed_traversal(self):
        failures = {"missing-control", "ambiguous", "unrelated-label", "stuck", "missing-window"}
        for mode in ("visible", "expired", "hidden-action", *sorted(failures)):
            with self.subTest(mode=mode):
                message = "command failed (exit 71): HUD Failure"
                label = Accessible(message, "label")
                close = Accessible(role="push button")
                children = [Accessible(children=[label])]
                if mode != "missing-control":
                    children.append(close)
                if mode in {"ambiguous", "hidden-action"}:
                    children.append(Accessible(role="push button", showing=mode != "hidden-action"))
                if mode == "unrelated-label":
                    children.append(Accessible("another message", "label"))
                toast = Accessible(children=children)
                window = Accessible("owner", "frame", [Accessible("Close", "push button")])
                if mode != "expired":
                    window.children.append(toast)
                    toast.parent = window
                activated = []
                waits = []

                def activate(button):
                    activated.append(button)
                    if button is close and mode != "stuck":
                        window.children.remove(toast)

                def wait_for(predicate, diagnostic):
                    waits.append(diagnostic)
                    self.assertTrue(predicate(), diagnostic)

                namespace = dict(activate=activate, wait_for=wait_for,
                                 Atspi=SimpleNamespace(StateType=SimpleNamespace(SHOWING=1)))
                exec(CODE, namespace)
                if mode in failures:
                    with self.assertRaises(AssertionError):
                        namespace["dismiss_toast"](None if mode == "missing-window" else window, message)
                    self.assertEqual(activated, [close] if mode == "stuck" else [])
                else:
                    namespace["dismiss_toast"](window, message)
                    self.assertEqual(activated, [] if mode == "expired" else [close])
                    self.assertEqual(len(waits), 1)


if __name__ == "__main__":
    unittest.main()
