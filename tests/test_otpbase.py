import json
import unittest
from unittest.mock import patch

from src.core.airwallet_otpbase import OTPBaseClient, OTPBaseCursor, OTPBaseError, OTPBaseTimeoutError, DEFAULT_OTPBASE_KEY


class OTPBaseTests(unittest.TestCase):
    def setUp(self):
        self.client = OTPBaseClient('test-key-not-a-real-credential', poll_interval=0.001)

    def test_no_embedded_default_key_is_used(self):
        self.assertEqual(DEFAULT_OTPBASE_KEY, '')
        with self.assertRaises(OTPBaseError):
            OTPBaseClient('')

    def test_stale_message_is_rejected(self):
        response = dict(found=True, seq=7, phone='07012345678', otp='1234')
        self.assertIsNone(self.client._code_from_otp_response(response, '07012345678', OTPBaseCursor(7)))

    def test_wrong_phone_is_rejected_even_with_new_sequence(self):
        response = dict(found=True, seq=8, phone='07099999999', otp='1234')
        self.assertIsNone(self.client._code_from_otp_response(response, '07012345678', OTPBaseCursor(7)))

    def test_international_phone_matches_same_domestic_number(self):
        response = dict(found=True, seq=8, phone='+817012345678', otp='1234')
        self.assertEqual(self.client._code_from_otp_response(response, '07012345678', OTPBaseCursor(7)), '1234')

    def test_coin_message_fallback_with_fullwidth_digits(self):
        response = dict(found=True, seq=8, phone='07012345678', otp='', message='COIN+認証コード：１２３４')
        self.assertEqual(self.client._code_from_otp_response(response, '07012345678', OTPBaseCursor(7)), '1234')

    def test_unrelated_sms_is_not_used_as_coin_otp(self):
        response = dict(found=True, seq=8, phone='07012345678', otp='', message='Other service code 1234')
        self.assertIsNone(self.client._code_from_otp_response(response, '07012345678', OTPBaseCursor(7)))

    def test_polling_sends_baseline_and_only_returns_new_message(self):
        replies = [dict(found=True, seq=7, phone='07012345678', otp='9999'),
                   dict(found=True, seq=8, phone='07012345678', otp='1234')]
        with patch.object(self.client, '_otp', side_effect=replies) as request:
            self.assertEqual(self.client.wait_for_otp('07012345678', OTPBaseCursor(7), timeout=0.1), '1234')
            self.assertTrue(all(call.kwargs['after_seq'] == 7 for call in request.call_args_list))

    def test_capture_cursor_uses_largest_provider_sequence(self):
        with patch.object(self.client, '_otp', return_value=dict(found=False, seq=3, last_seq=10)):
            self.assertEqual(self.client.capture_cursor('07012345678').seq, 10)

    def test_no_code_before_deadline_has_distinct_timeout_error(self):
        with self.assertRaises(OTPBaseTimeoutError):
            self.client.wait_for_otp('07012345678', OTPBaseCursor(7), timeout=0)
