"""Exercise the real selector without importing GI or starting an application."""

import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "agterm-linux/tests/atspi_smoke.py"
TREE = ast.parse(SOURCE.read_text())
FUNCTIONS = [node for node in TREE.body
             if isinstance(node, ast.FunctionDef) and node.name == "collect"]
assert len(FUNCTIONS) == 1, "expected exactly one collect helper"
CODE = compile(ast.Module(body=FUNCTIONS, type_ignores=[]), str(SOURCE), "exec")


class Accessible:
    def __init__(self, name="", role="panel", children=()):
        self.name = name
        self.role = role
        self.children = list(children)

    def get_name(self):
        return self.name

    def get_role_name(self):
        return self.role

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]


class DisappearingAccessible(Accessible):
    def get_role_name(self):
        raise RuntimeError("node disappeared during traversal")


class CollectTests(unittest.TestCase):
    def setUp(self):
        namespace = {}
        exec(CODE, namespace)
        self.collect = namespace["collect"]

    def test_button_roles_match_both_alias_directions(self):
        for requested in ("button", "push button"):
            for observed in ("button", "push button"):
                with self.subTest(requested=requested, observed=observed):
                    node = Accessible("Submit from DOM", observed)
                    self.assertEqual(self.collect(node, requested, "Submit from DOM"), [node])

    def test_button_aliases_do_not_admit_other_roles(self):
        for button in ("button", "push button"):
            for other in ("label", "check box", "toggle button", "radio button",
                          "menu item", "Button", "push-button", "button "):
                with self.subTest(button=button, other=other):
                    self.assertEqual(self.collect(Accessible(role=other), role=button), [])
                    self.assertEqual(self.collect(Accessible(role=button), role=other), [])

    def test_unrelated_roles_still_match_exactly(self):
        for role in ("label", "frame", "check box", "toggle button", "custom role"):
            with self.subTest(role=role):
                node = Accessible(role=role)
                self.assertEqual(self.collect(node, role=role), [node])

    def test_name_filter_remains_exact_for_both_button_aliases(self):
        for role in ("button", "push button"):
            node = Accessible("Rename from DOM", role)
            for wrong in ("Rename", "rename from DOM", "Rename from DOM ", ""):
                with self.subTest(role=role, wrong=wrong):
                    self.assertEqual(self.collect(node, role="push button", name=wrong), [])
            self.assertEqual(self.collect(node, role="push button", name=None), [node])

    def test_missing_name_is_treated_as_empty(self):
        node = Accessible(None, "button")
        self.assertEqual(self.collect(node, role="push button", name=""), [node])
        self.assertEqual(self.collect(node, role="push button", name="named"), [])

    def test_unfiltered_collection_keeps_depth_first_order(self):
        nested = Accessible("nested", "button")
        first = Accessible("first", "panel", [nested])
        second = Accessible("second", "label")
        root = Accessible("root", "frame", [first, second])
        self.assertEqual(self.collect(root), [root, first, nested, second])

    def test_role_and_name_filters_do_not_prune_nonmatching_parents(self):
        target = Accessible("Submit from DOM", "button")
        other_name = Accessible("Other", "push button")
        other_role = Accessible("Submit from DOM", "label")
        container = Accessible("container", "panel", [target, other_name, other_role])
        root = Accessible("window", "frame", [container])
        self.assertEqual(self.collect(root, "push button", "Submit from DOM"), [target])

    def test_search_remains_scoped_to_supplied_subtree(self):
        inside = Accessible("Close", "button")
        outside = Accessible("Close", "push button")
        subtree = Accessible(children=[inside])
        root = Accessible(children=[subtree, outside])
        self.assertEqual(self.collect(subtree, "push button", "Close"), [inside])
        self.assertEqual(self.collect(root, "push button", "Close"), [inside, outside])

    def test_existing_output_list_is_retained(self):
        previous = Accessible("previous", "button")
        current = Accessible("current", "push button")
        output = [previous]
        self.assertIs(self.collect(current, role="button", out=output), output)
        self.assertEqual(output, [previous, current])

    def test_separate_calls_do_not_share_output(self):
        first = Accessible("first", "button")
        second = Accessible("second", "push button")
        first_result = self.collect(first, role="button")
        second_result = self.collect(second, role="button")
        self.assertEqual(first_result, [first])
        self.assertEqual(second_result, [second])
        self.assertIsNot(first_result, second_result)

    def test_disappearing_child_does_not_hide_other_siblings(self):
        before = Accessible("before", "button")
        lost = DisappearingAccessible(children=[Accessible("lost", "button")])
        after = Accessible("after", "push button")
        root = Accessible(children=[before, lost, after])
        self.assertEqual(self.collect(root, role="push button"), [before, after])


if __name__ == "__main__":
    unittest.main()
