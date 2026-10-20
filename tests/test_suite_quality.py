"""Guards on the test suite itself.

Two failures this catches, both of which have already happened in this repo:

* A test method that asserts nothing. It passes whether or not the code works.
* A test module that forgets the frida bootstrap and silently drops out of the
  suite on a machine without frida.

A caveat worth stating plainly: "can reach an assertion" is a *floor*, not proof
that a test verifies behaviour. A test can assert something trivially true --
`test_main_detaches_the_session_even_if_the_guard_raises` did exactly that and
was removed. Only a mutation check proves a test can fail. See
`audit/RELEASE_READINESS.md`.
"""
import ast
import os
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

#: unittest assertion methods all start with this; plus the explicit escapes.
ASSERT_PREFIX = "assert"
ASSERT_NAMES = frozenset({"fail", "failIf", "failUnless", "skipTest"})


def _asserts_directly(node: ast.AST) -> bool:
    """Whether `node` contains an assertion, an explicit fail, or a raise."""
    for child in ast.walk(node):
        if isinstance(child, (ast.Assert, ast.Raise)):
            return True
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute):
            if func.attr.startswith(ASSERT_PREFIX) or func.attr in ASSERT_NAMES:
                return True
        elif isinstance(func, ast.Name) and func.id in ASSERT_NAMES:
            return True
    return False


def _called_helpers(node: ast.AST) -> tuple[set[str], set[str]]:
    """Names called as `self.x()` and as bare `x()` inside `node`."""
    on_self: set[str] = set()
    bare: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id == "self":
                on_self.add(func.attr)
        elif isinstance(func, ast.Name):
            bare.add(func.id)
    return on_self, bare


def find_tests_without_assertions(path: Path) -> list[str]:
    """`Class.method` names in `path` that cannot reach an assertion.

    Follows one level of indirection: a helper on `self`, a module-level
    function, or `setUp`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_funcs = {node.name: node for node in tree.body
                    if isinstance(node, ast.FunctionDef)}
    bare: list[str] = []

    for klass in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        methods = {node.name: node for node in klass.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name, node in methods.items():
            if not name.startswith("test"):
                continue
            if _asserts_directly(node):
                continue
            on_self, plain = _called_helpers(node)
            reachable = any(
                target is not None and _asserts_directly(target)
                for target in [methods.get(helper) for helper in on_self]
                + [module_funcs.get(helper) for helper in plain]
                + [methods.get("setUp")]
            )
            if not reachable:
                bare.append(f"{klass.name}.{name}")
    return bare


class AssertionCoverageTests(unittest.TestCase):
    def test_every_test_method_can_reach_an_assertion(self):
        offenders: list[str] = []
        counted = 0
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            counted += sum(
                1 for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test")
            )
            offenders.extend(f"{path.name}::{name}"
                             for name in find_tests_without_assertions(path))
        self.assertGreater(counted, 200, 'the scan found implausibly few tests')
        if offenders:
            self.fail("test methods that assert nothing:\n  " + "\n  ".join(offenders))

    def test_the_detector_itself_notices_a_bare_test(self):
        """A guard that cannot fail is worthless; prove this one can."""
        import tempfile

        sample = '''
import unittest

class Sample(unittest.TestCase):
    def test_asserts(self):
        self.assertTrue(True)

    def test_raises_is_a_check(self):
        raise RuntimeError('boom')

    def test_via_helper(self):
        self.check()

    def check(self):
        self.assertTrue(True)

    def test_bare(self):
        value = 1 + 1
        print(value)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test_sample.py"
            path.write_text(sample, encoding="utf-8")
            self.assertEqual(find_tests_without_assertions(path), ["Sample.test_bare"])


class FridaBoundaryTests(unittest.TestCase):
    """The mock/real boundary must be explicit, never implicit."""

    def test_stub_refuses_to_substitute_when_a_real_run_is_demanded(self):
        import os
        from unittest.mock import patch

        import frida_stub

        with patch.dict(os.environ, {frida_stub.REQUIRE_REAL_ENV: '1'}, clear=False), \
             patch.object(frida_stub, 'real_frida_available', return_value=False):
            with self.assertRaises(frida_stub.RealFridaRequired):
                frida_stub.install_frida_stub()

    def test_mode_reports_which_frida_the_run_used(self):
        import frida_stub

        self.assertIn(frida_stub.mode(), {'mock', 'real'})
        self.assertEqual(frida_stub.mode(), 'mock' if frida_stub.using_stub() else 'real')

    def test_real_integration_tests_are_skipped_not_passed_under_the_stub(self):
        """The NOT TESTED category has to be visible, not silently green."""
        import frida_stub

        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName('test_real_frida_integration')
        with open(os.devnull, 'w', encoding='utf-8') as sink:
            result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)

        self.assertGreater(result.testsRun, 0, 'the real-integration module holds no tests')
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])
        self.assertTrue(result.skipped, 'the NOT TESTED category produced no skips')

        if frida_stub.using_stub():
            # A class-level skip (setUpClass) is recorded in `skipped` but never
            # counted in `testsRun`, so compare against the skips that are real
            # TestCase instances. Any test that actually passed would push
            # testsRun above that count -- which is exactly what must not happen.
            method_skips = sum(1 for test, _ in result.skipped
                               if isinstance(test, unittest.TestCase))
            self.assertEqual(result.testsRun, method_skips,
                             'under the stub, no real-integration test may pass')



if __name__ == '__main__':
    unittest.main()
