import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from airwallet_dom_runner import PERMISSION_POLICY_JS


class PermissionAlertTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed to run the native alert policy')
    def test_localized_denials_and_unrelated_or_ambiguous_alerts(self):
        app = 'エアウォレット'
        def actions(*titles):
            return [{'title': title, 'enabled': True} for title in titles]

        cases = [
            ('Vietnamese notifications', f'“{app}” Muốn Gửi Thông báo cho Bạn', '',
             actions('Từ chối', 'Cho phép'), 0),
            ('English notifications', f'“{app}” Would Like to Send You Notifications', '',
             actions('Don’t Allow', 'Allow'), 0),
            ('Japanese notifications', f'“{app}”は通知を送信します。よろしいですか？', '',
             actions('許可しない', '許可'), 0),
            ('Vietnamese tracking', f'Cho phép “{app}” theo dõi hoạt động của bạn?', '',
             actions('Cho phép', 'Yêu cầu ứng dụng không theo dõi'), 1),
            ('English tracking', f'Allow “{app}” to Track Your Activity?', 'Tracking permission',
             actions('Ask App Not to Track', 'Allow'), 0),
            ('Japanese tracking', f'“{app}”によるトラッキングを許可しますか？', '',
             actions('Appにトラッキングしないように要求', '許可'), 0),
            ('User tracking label', f'“{app}”', 'Quyền theo dõi',
             actions('Không cho phép theo dõi', 'Cho phép'), 0),
            ('Unrelated app', '“Other App” Would Like to Send You Notifications', '',
             actions("Don't Allow", 'Allow'), -1),
            ('Unrelated permission', f'“{app}” Would Like to Access Your Camera', '',
             actions("Don't Allow", 'Allow'), -1),
            ('App error', f'“{app}”', 'Service unavailable', actions('Từ chối', 'OK'), -1),
            ('Disabled denial', f'“{app}” Notifications', '',
             [{'title': "Don't Allow", 'enabled': False}, {'title': 'Allow', 'enabled': True}], -1),
            ('Ambiguous denial', f'“{app}” Notifications', '',
             actions('Từ chối', "Don't Allow", 'Allow'), -1),
        ]
        payload = [[title, message, app, buttons] for _, title, message, buttons, _ in cases]
        code = PERMISSION_POLICY_JS + '''
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(cases.map(args => permissionDenialIndex(...args))));
'''
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'permission-policy.cjs'
            script.write_text(code, encoding='utf-8')
            result = subprocess.run([shutil.which('node'), str(script)], input=json.dumps(payload),
                                    text=True, capture_output=True, encoding='utf-8', check=True)
        actual = json.loads(result.stdout)
        for case, answer in zip(cases, actual):
            with self.subTest(case=case[0]):
                self.assertEqual(answer, case[-1])


if __name__ == '__main__':
    unittest.main()
