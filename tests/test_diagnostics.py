import unittest

from diagnostics import diagnose


def snap(*labels, alert=None, **other):
    return {'topVC': 'Sgc.UnknownController', 'labels': list(labels), 'buttons': [], 'alert': alert, **other}


class DiagnosticsTests(unittest.TestCase):
    def test_service_unavailable_does_not_claim_verified_proxy_cause(self):
        result = diagnose(snap(alert={'title': '', 'message': '現在サービスをご利用いただけません'}))
        self.assertEqual(result['conditions'][0]['kind'], 'service_unavailable')
        self.assertIn('chưa xác định', result['conditions'][0]['message'])

    def test_phone_already_used_is_recognized(self):
        result = diagnose(snap(alert={'message': 'この電話番号はすでに他のアカウントで使われています'}))
        self.assertEqual(result['conditions'][0]['kind'], 'phone_already_used')

    def test_card_maintenance_without_completion_is_not_account_success(self):
        self.assertEqual(diagnose(snap('メンテナンス中', 'Visaバーチャルカード'))['conditions'], [])

    def test_explicit_completion_with_card_notice_is_preserved(self):
        result = diagnose(snap('アカウントが作成されました', 'Visaバーチャルカードの番号を発行できませんでした'))
        self.assertEqual(result['conditions'][0]['kind'], 'account_created_card_notice')

    def test_successfully_issued_card_is_not_reported_as_maintenance(self):
        result = diagnose(snap('アカウント作成完了', 'アカウントが作成されました',
                               'Visaバーチャルカードの\n番号が発行されました。'))
        self.assertEqual(result['conditions'], [])
        self.assertFalse(result['needs_review'])

    def test_sms_otp_phone_numbers_and_xiaowei_codes_are_not_error_codes(self):
        result = diagnose(snap('認証コード 123456', '電話番号 09012345678', 'code: 10000 / 10001'))
        self.assertEqual(result['displayed_error_codes'], [])

    def test_explicit_error_code_is_captured_without_guessing_meaning(self):
        result = diagnose(snap(alert={'message': 'エラーコード：E-12345'}))
        self.assertEqual(result['displayed_error_codes'], ['E-12345'])
        self.assertEqual(result['conditions'][0]['kind'], 'unmapped_alert')

    def test_common_next_label_does_not_identify_otp_screen(self):
        self.assertIsNone(diagnose(snap('次へ進む'))['stage'])

    def test_tutorial_uses_current_page_when_all_three_labels_remain_in_hierarchy(self):
        result = diagnose(snap('エアウォレットなら', 'お店でもネットでも', '安心のセキュリティ対策と',
                               topVC='Sgc.TutorialViewController', pages=[{'current': 1, 'count': 3}]))
        self.assertEqual(result['stage']['state'], 'INTRO_2')

    def test_home_is_not_confused_by_birthday_and_settings_news_text(self):
        result = diagnose(snap('生年月日または住所を登録しましょう', '設定・アカウント',
                               topVC='Sgc.MasterTopViewController'))
        self.assertEqual(result['stage']['state'], 'HOME_SCREEN')
        self.assertFalse(result['needs_review'])

    def test_invitation_is_not_confused_by_settings_in_help_text(self):
        result = diagnose(snap('招待コード', 'あとから設定できます',
                               topVC='Sgc.RegistrationInputInvitationCodeRouter'))
        self.assertEqual(result['stage']['state'], 'INVITATION_SCREEN')

    def test_pin_and_confirmation_share_controller_but_have_different_titles(self):
        for title, state in [('暗証番号の入力', 'PIN_SCREEN'), ('暗証番号の再入力', 'RE_PIN_SCREEN')]:
            with self.subTest(title=title):
                result = diagnose(snap(topVC='Sgc.PasscodeSetupViewController', title=title))
                self.assertEqual(result['stage']['state'], state)


if __name__ == '__main__':
    unittest.main()
