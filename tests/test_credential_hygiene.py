"""Credential hygiene: redaction, private file modes, and the removed PIN-on-disk path.

`frida` is not installed on every workstation, and `airwallet_dom_runner` /
`airwallet_full_flow` import it transitively. A stub module is installed into
`sys.modules` before those imports so these tests cover the real code paths
anywhere, without a device.
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frida_stub import install_frida_stub

install_frida_stub()  # no-op when the real frida is installed

from src.utils.redaction import PLACEHOLDER, Redactor, redact
from src.utils.secret_files import is_owner_only, reap_stale, write_private_json

PROFILE = {
    'phone_number': '07012345678',
    'account_password': 'TestOnly123',
    'password': 'DifferentMail123',
    'pin': '0042',
    'kana_sei': 'テスト',
    'kana_mei': 'タロウ',
    'birthday': '1999-02-03',
}


class RedactorTests(unittest.TestCase):
    def test_removes_every_profile_secret(self):
        redactor = Redactor().add_profile(PROFILE)
        text = ("login failed for pin=0042 password=TestOnly123 "
                "mail=DifferentMail123 phone=07012345678")
        scrubbed = redactor.scrub(text)
        for secret in ('0042', 'TestOnly123', 'DifferentMail123'):
            self.assertNotIn(secret, scrubbed)
        self.assertIn(PLACEHOLDER, scrubbed)
        # phone_number is not in PROFILE_SECRET_KEYS, so it survives by design.
        self.assertIn('07012345678', scrubbed)

    def test_longest_value_is_replaced_first(self):
        """A password containing the PIN must not leave the PIN behind."""
        redactor = Redactor().add_profile({'pin': '1234', 'account_password': 'abc1234xyz'})
        scrubbed = redactor.scrub('tried abc1234xyz')
        self.assertNotIn('1234', scrubbed)
        self.assertEqual(scrubbed, f'tried {PLACEHOLDER}')

    def test_ignores_values_too_short_to_redact_safely(self):
        redactor = Redactor()
        redactor.add('')
        redactor.add('ab')
        redactor.add(None)
        self.assertEqual(len(redactor), 0)
        self.assertEqual(redactor.scrub('ab is everywhere'), 'ab is everywhere')

    def test_picks_up_settings_and_environment_credentials(self):
        with patch.dict(os.environ, {'AIRWALLET_OTPBASE_API_KEY': 'env-key-123456'}, clear=False):
            message = redact('GET https://api/x?key=env-key-123456 cfg=cfg-key-98765',
                             settings={'otpbase_api_key': 'cfg-key-98765'})
        self.assertNotIn('env-key-123456', message)
        self.assertNotIn('cfg-key-98765', message)

    def test_never_raises_on_odd_input(self):
        class Hostile:
            def __str__(self):
                raise RuntimeError('nope')

        redactor = Redactor().add_profile(PROFILE)
        self.assertEqual(redactor.scrub(Hostile()), PLACEHOLDER)
        self.assertEqual(redact(None), 'None')

    def test_non_dict_sources_are_ignored(self):
        redactor = Redactor().add_profile('not a dict').add_settings(None)
        self.assertEqual(len(redactor), 0)


class PrivateFileTests(unittest.TestCase):
    def test_json_is_written_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_private_json(Path(directory) / 'nested' / 'p.json', PROFILE, indent=2)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), PROFILE)
            self.assertTrue(is_owner_only(path),
                            'a file holding a PIN must not be readable by other accounts')
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_owner_only_check_notices_a_loosened_acl(self):
        """The check must be able to fail, or it proves nothing."""
        with tempfile.TemporaryDirectory() as directory:
            path = write_private_json(Path(directory) / 'p.json', {'pin': '0042'})
            self.assertTrue(is_owner_only(path))
            if os.name == 'nt':
                subprocess.run(['icacls', str(path), '/grant', 'Everyone:(R)'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                os.chmod(path, 0o644)
            self.assertFalse(is_owner_only(path))

    def test_reap_stale_removes_only_old_matching_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'input_a.json'
            fresh = root / 'input_b.json'
            other = root / 'keep.txt'
            for path in (old, fresh, other):
                path.write_text('{}', encoding='utf-8')
            os.utime(old, (0, 0))  # epoch == very old
            removed = reap_stale(root, 'input_*.json', 3600)
            self.assertEqual(removed, ['input_a.json'])
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(other.exists())

    def test_reap_stale_is_silent_on_a_missing_directory(self):
        self.assertEqual(reap_stale(Path('does/not/exist'), '*.json', 60), [])

    def test_reap_stale_disabled_by_zero_age(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input_a.json'
            path.write_text('{}', encoding='utf-8')
            os.utime(path, (0, 0))
            self.assertEqual(reap_stale(Path(directory), 'input_*.json', 0), [])
            self.assertTrue(path.exists())


class InMemoryProfileTests(unittest.TestCase):
    """The PIN must never be written to disk just to be read back in-process."""

    def test_dom_runner_prefers_an_in_memory_profile(self):
        import airwallet_dom_runner as runner

        args = SimpleNamespace(action='run', profile_json='/does/not/exist.json',
                               profile_data=dict(PROFILE), device_id='auto',
                               fresh_launch=False, proxy_prepared=True, bundle_id='x')
        with patch.object(runner, 'load_profile') as load, \
             patch('flow.build_otp_client', return_value=object()), \
             patch('flow.load_settings', return_value={}), \
             patch.object(runner, 'import_frida', return_value=None), \
             patch('inputs.select_device_id', return_value='auto'):
            with self.assertRaises(RuntimeError):
                runner.run_with_frida(args)   # stops at "cannot import frida"
        load.assert_not_called()

    def test_dom_runner_still_reads_a_file_when_no_profile_is_supplied(self):
        import airwallet_dom_runner as runner

        args = SimpleNamespace(action='run', profile_json='/some/profile.json',
                               device_id='auto', fresh_launch=False,
                               proxy_prepared=True, bundle_id='x')
        with patch.object(runner, 'load_profile', return_value=dict(PROFILE)) as load, \
             patch('flow.build_otp_client', return_value=object()), \
             patch('flow.load_settings', return_value={}), \
             patch.object(runner, 'import_frida', return_value=None), \
             patch('inputs.select_device_id', return_value='auto'):
            with self.assertRaises(RuntimeError):
                runner.run_with_frida(args)
        load.assert_called_once_with('/some/profile.json')

    def test_full_flow_no_longer_writes_a_profile_file(self):
        """Guard the fix: the SIGKILL-proof property is 'no file at all'."""
        source = (ROOT / 'scripts' / 'airwallet_full_flow.py').read_text(encoding='utf-8')
        self.assertNotIn('runtime_profile', source)
        self.assertIn('parsed_dom_args.profile_data = profile', source)
        self.assertIn("reap_stale", source)

    def test_outer_handlers_redact(self):
        """Structural floor only -- the behavioural checks are in RedactingBoundaryTests."""
        for name in ('main.py', 'scripts/airwallet_dom_runner.py'):
            source = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('redact(', source, f'{name} must scrub secrets before printing')


class RedactingBoundaryTests(unittest.TestCase):
    """Run the real entry points and read what they actually printed.

    A mutation check showed the structural test above still passes when the
    `redact(...)` call is removed, because the import line keeps the word in the
    file. These tests fail in that case.
    """

    SECRET = 'env-otpbase-key-9f3a1c'

    def test_main_does_not_print_a_credential_from_an_exception(self):
        import main as entry

        errors = io.StringIO()
        argv = ['main.py', 'snapshot']
        with patch.dict(os.environ, {'AIRWALLET_OTPBASE_API_KEY': self.SECRET}, clear=False), \
             patch.object(sys, 'argv', argv), \
             patch.object(entry, 'load_config',
                          side_effect=RuntimeError(f'GET https://api.example/x?key={self.SECRET}')), \
             contextlib.redirect_stderr(errors), \
             contextlib.redirect_stdout(io.StringIO()):
            code = entry.main()

        printed = errors.getvalue()
        self.assertEqual(code, 1)
        self.assertNotIn(self.SECRET, printed, 'the API key reached stderr')
        self.assertIn('[redacted]', printed)

    def test_dom_runner_does_not_print_a_credential_from_an_exception(self):
        import airwallet_dom_runner as runner

        errors = io.StringIO()
        args = runner.build_parser().parse_args(['--action', 'screen'])
        with patch.dict(os.environ, {'AIRWALLET_OTPBASE_API_KEY': self.SECRET}, clear=False), \
             patch.object(sys, 'argv', ['airwallet_dom_runner.py', '--action', 'screen']), \
             patch.object(runner, 'build_parser') as parser, \
             patch.object(runner, 'import_frida', return_value=object()), \
             patch.object(runner, 'run_with_frida',
                          side_effect=RuntimeError(f'proxy auth failed for {self.SECRET}')), \
             contextlib.redirect_stderr(errors), \
             contextlib.redirect_stdout(io.StringIO()):
            parser.return_value.parse_args.return_value = args
            code = runner.main()

        printed = errors.getvalue()
        self.assertEqual(code, 1)
        self.assertNotIn(self.SECRET, printed, 'the API key reached stderr')
        self.assertIn('[redacted]', printed)

    def test_profile_secrets_are_redacted_too(self):
        """Not just env credentials: a PIN quoted by an exception must go as well."""
        from src.utils.redaction import redact

        message = redact('row rejected: pin=0042 password=TestOnly123',
                         profile={'pin': '0042', 'account_password': 'TestOnly123'})
        self.assertNotIn('0042', message)
        self.assertNotIn('TestOnly123', message)


class ProfileExportTests(unittest.TestCase):
    """`write_profile_json` is the persistent PIN-on-disk path (S4)."""

    def test_exported_profile_is_owner_only_and_warns(self):
        from airwallet_profile_from_excel import write_profile_json

        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'current_profile.json'
            with contextlib.redirect_stderr(errors):
                write_profile_json(dict(PROFILE), path)

            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), PROFILE)
            self.assertTrue(is_owner_only(path),
                            'the exported profile holds a PIN and must not be world-readable')

        warning = errors.getvalue()
        self.assertIn('plaintext', warning, 'the operator must be told what the file holds')
        self.assertIn('pin', warning)

    def test_a_profile_without_secrets_is_not_warned_about(self):
        from airwallet_profile_from_excel import write_profile_json

        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'p.json'
            with contextlib.redirect_stderr(errors):
                write_profile_json({'phone_number': '07012345678'}, path)
        self.assertEqual(errors.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
