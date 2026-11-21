"""Keep the native-probe exemption narrow and its validation mandatory."""
import pathlib
import re
import json
import os
import subprocess
import sys
import textwrap
import unittest


WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / '.github/workflows/omarchy-ci.yml'


class ProbeRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()

    def job(self, name):
        return re.search(r'^  ' + name + r':\n(.*?)(?=^  [\w-]+:|\Z)', self.text,
                         re.M | re.S).group(1)

    def test_only_standalone_probe_paths_are_exempt(self):
        section = re.search(r'^            native_probe:\n(.*?)(?=^            \w+:)',
                            self.text, re.M | re.S).group(1)
        self.assertEqual(re.findall(r'"([^"]+)"', section), [
            'scripts/omarchy-config-probe.py', 'scripts/tests/test_omarchy_config_probe.py'])
        for pattern in ('scripts/**', 'plugins/**', '.agents/**', '.claude-plugin/**',
                        'agtermCore/**', 'agterm-linux/**', 'linux/**', 'packaging/linux/**',
                        '.github/workflows/omarchy-ci.yml'):
            self.assertIn('"' + pattern + '"', self.text)

    def test_existing_validation_jobs_are_retained(self):
        scripts = self.job('scripts')
        self.assertNotRegex(scripts, re.compile(r'^    if:', re.M))
        self.assertIn("unittest discover -s scripts/tests -p 'test_*.py'", scripts)
        self.assertIn("if: needs.changes.outputs.swift == 'true'", self.job('lint-linux'))
        for job in ('test-linux', 'build-linux', 'ghostty-patch-tests', 'zmx-patch-tests'):
            self.assertIn("if: needs.changes.outputs.product == 'true'", self.job(job))
        self.assertEqual(self.text.count('if: needs.changes.outputs.product'), 4)
        gate = self.job('ci')
        self.assertIn('!cancelled()', gate)
        code = re.search("python3 - <<'PYTHON'\n(.*?)^          PYTHON", gate, re.M | re.S).group(1)
        for status in ('success', 'skipped', 'failure', 'cancelled'):
            results = {'upstream-ci-disabled': {'result': 'success'}, 'changes': {'result': status}}
            result = subprocess.run([sys.executable, '-c', textwrap.dedent(code)],
                                    env={**os.environ, 'RESULTS': json.dumps(results)},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, status in ('success', 'skipped'), result.stderr)

    def test_main_push_compares_the_exact_head_with_the_release_base(self):
        changes = self.job('changes')
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'", changes)
        self.assertIn('id: release_base', changes)
        self.assertIn('python3 -I scripts/omarchy-auto-release.py ci-release-base "$GITHUB_SHA"', changes)
        self.assertIn('          base: ${{ steps.release_base.outputs.sha }}', changes)
        self.assertIn("          ref: ${{ steps.release_base.outputs.sha != '' && github.sha || '' }}", changes)
        self.assertIn("  cancel-in-progress: true\n", self.text.split('\nenv:', 1)[0])
        self.assertIn("swift: ${{ github.event_name == 'workflow_dispatch' || steps.release_base.outputs.full_build == 'true'",
                      changes)

    def test_full_build_runs_cookbook_when_the_path_filter_would_skip_it(self):
        self.assertIn("      cookbook: ${{ steps.release_base.outputs.full_build == 'true' || steps.filter.outputs.cookbook == 'true' }}",
                      self.job('changes'))
        self.assertIn("if: needs.changes.outputs.cookbook == 'true'", self.job('cookbook'))

    def test_actual_expression_routes_counts_and_manual_dispatch(self):
        expression = re.search(r'^      product: \$\{\{ (.*) \}\}$', self.text, re.M).group(1)
        replacements = {'github.event_name': 'event', 'steps.filter.outputs.swift_count': 'heavy',
                        'steps.filter.outputs.native_probe_count': 'probe',
                        'steps.filter.outputs.swift': 'swift',
                        'steps.release_base.outputs.full_build': 'full_build',
                        'steps.filter.outputs.all_changed_count': 'total'}
        for source, target in replacements.items():
            expression = expression.replace(source, target)
        expression = expression.replace('||', 'or').replace('&&', 'and')
        # These operands are strings in Actions; the expression uses only equality and Boolean operators.
        for event, swift, heavy, probe, total, full_build, expected in (
            ('pull_request', 'true', '1', '1', 1, '', False),
            ('push', 'true', '2', '2', 2, 'false', False),
            ('push', 'true', '1', '0', 2, 'false', True),
            ('push', 'false', '0', '0', 1, 'false', False),
            ('push', 'false', '0', '0', 1, 'true', True),
            ('pull_request', 'true', '2', '1', 2, '', True),
            ('pull_request', 'true', '1', '0', 1, '', True),
            ('pull_request', 'false', '0', '0', 0, '', False),
            ('pull_request', 'true', '2', '2', 3000, '', True),
            ('pull_request', 'true', '2', '2', 2999, '', False),
            ('workflow_dispatch', '', '', '', 0, '', True),
        ):
            with self.subTest(event=event, heavy=heavy, probe=probe, full_build=full_build):
                self.assertEqual(eval(expression, {'__builtins__': {}}, dict(
                    event=event, swift=swift, heavy=heavy, probe=probe, total=total, full_build=full_build)), expected)


if __name__ == '__main__':
    unittest.main()
