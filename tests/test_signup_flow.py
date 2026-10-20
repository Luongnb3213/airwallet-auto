import copy
import hashlib
import json
from types import SimpleNamespace
import unittest

from flow import run_signup, validate_profile
from src.core.airwallet_otpbase import OTPBaseCursor, OTPBaseError, OTPBaseTimeoutError

PROFILE = dict(phone_number='07012345678', account_password='TestOnly123', password='DifferentMail123',
               kana_sei='テスト', kana_mei='タロウ', birthday='1999-02-03', pin='0042')
ORDER = ['INTRO_1', 'INTRO_2', 'INTRO_3', 'NOTIFICATION', 'ACCOUNT', 'PHONE', 'SMS',
         'PASSWORD', 'NAME', 'BIRTHDAY', 'PIN', 'RE_PIN', 'BIOMETRIC', 'COMPLETE',
         'INVITATION', 'SETUP', 'RECRUIT', 'HOME']
CONTROLLERS = dict(NOTIFICATION='Sgc.PushNotificationAppealViewController', ACCOUNT='Sgc.AccountCreateViewController',
                   PHONE='Sgc.InputPhoneViewController', SMS='Sgc.SMSValidationRegistrationViewController',
                   PASSWORD='Sgc.InputPasswordViewController', NAME='Sgc.InputNameViewController',
                   BIRTHDAY='Sgc.InputBirthdayViewController', PIN='Sgc.PasscodeSetupViewController',
                   RE_PIN='Sgc.PasscodeSetupViewController', COMPLETE='Sgc.RegistrationCompleteViewController',
                   INVITATION='Sgc.RegistrationInputInvitationCodeRouter', SETUP='Sgc.BankAccountEKYCPromotionViewController',
                   RECRUIT='FloatingPanel.FloatingPanelController', HOME='Sgc.MasterTopViewController')


def screen(state):
    vc = 'Sgc.TutorialViewController' if state.startswith('INTRO') else CONTROLLERS.get(state, 'UIAlertController')
    result = dict(topVC=vc, title='', alert=None, fields=[], buttons=[], labels=[], pages=[])
    title = dict(NOTIFICATION='次へ進む', ACCOUNT='アカウントの新規作成', PHONE='SMSでの認証へ進む',
                 SMS='次へ進む', PASSWORD='次へ進む', NAME='次へ進む', BIRTHDAY='上記に同意して次へ進む',
                 COMPLETE='次へ進む', INVITATION='次へ進む', SETUP='あとで設定する', RECRUIT='今は連携しない').get(state)
    if state.startswith('INTRO'):
        result['pages'] = [{'current': int(state[-1]) - 1, 'count': 3}]
        title = 'はじめる' if state == 'INTRO_3' else '次へ進む'
    if title:
        result['buttons'] = [dict(title=title, enabled=True)]
    if state in ('PHONE', 'SMS', 'PASSWORD', 'BIRTHDAY', 'NAME'):
        result['fields'] = [dict(filled=False)] * (2 if state == 'NAME' else 1)
    if state in ('PIN', 'RE_PIN'):
        result['title'] = '暗証番号の入力' if state == 'PIN' else '暗証番号の再入力'
        result['buttons'] = [dict(title=str(i), enabled=True) for i in range(10)]
    if state == 'BIOMETRIC':
        result['alert'] = dict(message='暗証番号のかわりにTouch IDを利用しますか？', buttons=['利用しない', '利用する'])
    if state == 'RECRUIT':
        result['labels'] = ['リクルートIDの連携']
    if state == 'COMPLETE':
        result['labels'] = ['アカウント作成完了', 'アカウントが作成されました', 'Visaバーチャルカードの番号が発行されました。']
    return result


class FakeAPI:
    def __init__(self, start='INTRO_1', events=None):
        self.index = ORDER.index(start)
        self.events = events if events is not None else []
        self.values = {}
        self.override = None
        self.fail_input = False
        self.after_sms_alert = None
        self.delay_password = False
        self._delayed = False

    @property
    def state(self):
        return ORDER[self.index]

    def snapshot(self):
        if self.override:
            return copy.deepcopy(self.override)
        result = screen(self.state)
        if self.state == 'PASSWORD' and self.delay_password and not self._delayed:
            self._delayed = True
            result['fields'] = []
        return result

    def type_field(self, index, value, vc):
        assert vc == screen(self.state)['topVC']
        self.events.append(('type', self.state, index))
        self.values[self.state, index] = value
        return not self.fail_input

    def tap_button(self, title, vc):
        expected = screen(self.state)
        assert expected['topVC'] == vc
        assert any(b['title'] == title for b in expected['buttons'])
        self.events.append(('tap', self.state, title))
        if self.state == 'PHONE' and self.after_sms_alert:
            self.override = dict(topVC='UIAlertController', alert={'message': self.after_sms_alert}, labels=[], buttons=[])
        else:
            self.index += 1
        return True

    def set_birthday(self, year, month, day, vc):
        assert self.state == 'BIRTHDAY'
        self.values['birthday'] = (year, month, day)
        return True

    def enter_pin(self, value, title):
        assert self.state in ('PIN', 'RE_PIN') and screen(self.state)['title'] == title
        self.values[self.state] = value
        self.events.append(('pin', self.state))
        self.index += 1
        return True

    def choose_alert(self, title, message):
        assert self.state == 'BIOMETRIC' and title == '利用しない'
        self.events.append(('alert', title))
        self.index += 1
        return True


class FakeOTP:
    def __init__(self, events=None, error=False, seq=10):
        self.events = events if events is not None else []
        self.error = error
        self.seq = seq
        self.waited = []

    def capture_cursor(self, phone):
        self.events.append(('cursor',))
        return OTPBaseCursor(self.seq)

    def wait_for_otp(self, phone, cursor):
        self.waited.append((phone, cursor.seq))
        if self.error:
            raise OTPBaseTimeoutError('OTP timeout for test')
        return '1234'


def options(**values):
    defaults = dict(wait_timeout_ms=30, poll_ms=1, max_steps=40, no_submit=False, single_step=False, settings={})
    defaults.update(values)
    return SimpleNamespace(**defaults)


class SignupFlowTests(unittest.TestCase):
    def test_full_verified_sequence_reaches_home(self):
        events = []
        api, otp = FakeAPI(events=events), FakeOTP(events=events)
        result = run_signup(api, profile=PROFILE, args=options(), otp_client=otp)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['account_created'])
        self.assertEqual(api.state, 'HOME')
        self.assertEqual(api.values['PASSWORD', 0], PROFILE['account_password'])
        self.assertNotEqual(api.values['PASSWORD', 0], PROFILE['password'])
        self.assertEqual(api.values['birthday'], (1999, 2, 3))
        self.assertEqual(api.values['PIN'], '0042')
        self.assertEqual(api.values['RE_PIN'], '0042')
        self.assertLess(events.index(('cursor',)), events.index(('tap', 'PHONE', 'SMSでの認証へ進む')))
        for secret in ('TestOnly123', 'DifferentMail123', '0042', '1234'):
            self.assertNotIn(secret, json.dumps(result))

    def test_already_home_does_not_mark_a_new_account_as_created(self):
        api = FakeAPI('HOME')
        result = run_signup(api)
        self.assertEqual(result['status'], 'already_home')
        self.assertFalse(result['complete'])
        self.assertFalse(result['account_created'])
        self.assertEqual(api.events, [])

    def test_missing_data_stops_before_any_action(self):
        api = FakeAPI('ACCOUNT')
        result = run_signup(api, profile={})
        self.assertEqual(result['status'], 'invalid_profile')
        self.assertEqual(api.events, [])

    def test_missing_api_key_stops_before_typing_or_sending_sms(self):
        api = FakeAPI('PHONE')
        result = run_signup(api, profile=PROFILE, args=options())
        self.assertEqual(result['status'], 'otp_not_configured')
        self.assertEqual(api.events, [])

    def test_input_mismatch_stops_before_next(self):
        api = FakeAPI('PASSWORD')
        api.fail_input = True
        result = run_signup(api, profile=PROFILE, args=options())
        self.assertEqual(result['status'], 'input_mismatch')
        self.assertFalse(any(e[0] == 'tap' for e in api.events))

    def test_used_phone_preserves_alert_without_retry(self):
        api, otp = FakeAPI('PHONE'), FakeOTP()
        api.after_sms_alert = 'この電話番号はすでに他のアカウントで使われています'
        result = run_signup(api, profile=PROFILE, args=options(), otp_client=otp)
        self.assertEqual(result['status'], 'phone_already_used')
        self.assertEqual(sum(e[0] == 'tap' for e in api.events), 1)
        self.assertEqual(otp.waited, [])
        self.assertEqual(result['resume']['otp_after_seq'], 10)

    def test_otp_timeout_keeps_resume_cursor_and_does_not_resend(self):
        api, otp = FakeAPI('PHONE'), FakeOTP(error=True)
        result = run_signup(api, profile=PROFILE, args=options(), otp_client=otp)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'otp_timeout')
        self.assertTrue(result['message'].startswith('[OTP_TIMEOUT]'))
        self.assertEqual(api.state, 'SMS')
        self.assertEqual(result['resume']['otp_after_seq'], 10)
        self.assertEqual(sum(e[:2] == ('tap', 'PHONE') for e in api.events), 1)

    def test_resume_otp_uses_baseline_without_sending_sms_again(self):
        profile = dict(PROFILE, otp_after_seq=10,
                       phone_fingerprint=hashlib.sha256(PROFILE['phone_number'].encode()).hexdigest())
        api, otp = FakeAPI('SMS'), FakeOTP()
        result = run_signup(api, profile=profile, args=options(), otp_client=otp)
        self.assertTrue(result['complete'])
        self.assertEqual(otp.waited, [(PROFILE['phone_number'], 10)])
        self.assertFalse(any(e[:2] == ('tap', 'PHONE') for e in api.events))

    def test_resume_with_wrong_phone_context_is_rejected(self):
        api = FakeAPI('SMS')
        result = run_signup(api, profile=dict(PROFILE, otp_after_seq=10, phone_fingerprint='wrong'), args=options())
        self.assertEqual(result['status'], 'otp_context_mismatch')
        self.assertEqual(api.events, [])

    def test_missing_sms_baseline_does_not_fetch_arbitrary_latest_otp(self):
        api, otp = FakeAPI('SMS'), FakeOTP()
        result = run_signup(api, profile=PROFILE, args=options(), otp_client=otp)
        self.assertEqual(result['status'], 'otp_context_missing')
        self.assertEqual(otp.waited, [])

    def test_explicit_manual_otp_can_resume_without_provider(self):
        result = run_signup(FakeAPI('SMS'), profile=dict(PROFILE, otp='1234'), args=options())
        self.assertTrue(result['complete'])

    def test_no_submit_stops_before_pin_confirmation(self):
        api = FakeAPI('PASSWORD')
        result = run_signup(api, profile=PROFILE, args=options(no_submit=True))
        self.assertEqual(result['status'], 'ready_to_submit')
        self.assertEqual(api.state, 'RE_PIN')
        self.assertNotIn('RE_PIN', api.values)

    def test_no_submit_also_stops_at_biometric_dialog_if_resuming_there(self):
        api = FakeAPI('BIOMETRIC')
        result = run_signup(api, profile=PROFILE, args=options(no_submit=True))
        self.assertEqual(result['status'], 'ready_to_submit')
        self.assertEqual(api.events, [])

    def test_manual_code_passed_before_new_sms_is_not_used(self):
        api, otp = FakeAPI('PHONE'), FakeOTP()
        result = run_signup(api, profile=dict(PROFILE, otp='9999'), args=options(), otp_client=otp)
        self.assertTrue(result['complete'])
        self.assertEqual(len(otp.waited), 1)
        self.assertEqual(api.values['SMS', 0], '1234')

    def test_single_step_returns_resume_metadata(self):
        api = FakeAPI('PHONE')
        result = run_signup(api, profile=PROFILE, args=options(single_step=True, max_steps=1), otp_client=FakeOTP())
        self.assertEqual(result['status'], 'step_complete')
        self.assertEqual(api.state, 'SMS')
        self.assertEqual(result['resume']['otp_after_seq'], 10)

    def test_waits_for_transient_empty_form_before_filling_it(self):
        api = FakeAPI('SMS')
        api.delay_password = True
        result = run_signup(api, profile=dict(PROFILE, otp='1234'), args=options())
        self.assertTrue(result['complete'])
        self.assertTrue(api._delayed)

    def test_invalid_provider_baseline_stops_before_sms(self):
        api = FakeAPI('PHONE')
        result = run_signup(api, profile=PROFILE, args=options(), otp_client=FakeOTP(seq=-1))
        self.assertEqual(result['status'], 'otp_baseline_missing')
        self.assertFalse(any(e[0] == 'tap' for e in api.events))

    def test_password_is_not_silently_taken_from_other_password_column(self):
        profile = dict(PROFILE)
        profile.pop('account_password')
        with self.assertRaisesRegex(ValueError, 'account_password'):
            validate_profile(profile)
