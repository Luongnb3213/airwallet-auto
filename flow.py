"""AirWallet iOS registration through the screens verified in the paired run."""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import time

from diagnostics import diagnose, visible_text
from src.core.airwallet_otpbase import OTPBaseClient, OTPBaseCursor, OTPBaseError, OTPBaseTimeoutError, normalize_phone

FLOW_CONFIGURED = True
ROOT = Path(__file__).resolve().parent
DATA_FIELDS = ('phone_number', 'account_password', 'kana_sei', 'kana_mei', 'birthday', 'pin')
PIN_TITLE = '暗証番号の入力'
RE_PIN_TITLE = '暗証番号の再入力'


def ensure_configured():
    if not FLOW_CONFIGURED:
        raise RuntimeError('Luồng đăng ký AirWallet chưa được bật.')


def load_settings():
    path = ROOT / 'config.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def build_otp_client(settings=None):
    settings = settings if settings is not None else load_settings()
    key = os.environ.get('AIRWALLET_OTPBASE_API_KEY', '').strip() or str(settings.get('otpbase_api_key') or '').strip()
    if not key:
        return None
    return OTPBaseClient(key, timeout=float(settings.get('otp_timeout_seconds', 120)),
                         poll_interval=float(settings.get('otp_poll_seconds', 2)))


def normalize_profile(profile):
    result = {key: value for key, value in (profile or {}).items() if value is not None}
    result['phone_number'] = normalize_phone(result.get('phone_number') or result.get('phone') or '')
    result['birthday'] = str(result.get('birthday') or result.get('dob') or '').strip().replace('/', '-')
    for key in DATA_FIELDS:
        result[key] = str(result.get(key) or '').strip()
    return result


def validate_profile(profile):
    profile = normalize_profile(profile)
    missing = [key for key in DATA_FIELDS if not profile.get(key)]
    if missing:
        raise ValueError('Thiếu dữ liệu: ' + ', '.join(missing))
    if not re.fullmatch(r'0[0-9]{9,10}', profile['phone_number']):
        raise ValueError('phone_number cần là số Nhật dạng nội địa 10–11 chữ số, hoặc +81 tương ứng.')
    password = profile['account_password']
    if not (6 <= len(password) <= 20 and password.isascii() and re.search('[A-Za-z]', password) and re.search('[0-9]', password)):
        raise ValueError('account_password cần 6–20 ký tự half-width, có cả chữ Latin và số.')
    if not re.fullmatch('[0-9]{4}', profile['pin']):
        raise ValueError('pin cần đúng 4 chữ số; lưu dưới dạng text để giữ số 0 đầu.')
    try:
        birthday = date.fromisoformat(profile['birthday'])
    except ValueError:
        raise ValueError('birthday cần dạng YYYY-MM-DD hoặc YYYY/MM/DD.') from None
    if birthday >= date.today():
        raise ValueError('birthday phải là ngày trong quá khứ.')
    return profile


def _fingerprint(phone):
    return hashlib.sha256(phone.encode()).hexdigest()


def _stage(snapshot):
    alert = snapshot.get('alert') or {}
    message = str(alert.get('message') or '')
    if snapshot.get('alert') and '暗証番号のかわりに' in message and ('Touch ID' in message or 'Face ID' in message):
        if '利用しない' in alert.get('buttons', []):
            return 'BIOMETRIC_CHOICE'
    if snapshot.get('topVC') == 'FloatingPanel.FloatingPanelController':
        if 'リクルートID' in visible_text(snapshot) and any(b.get('title') == '今は連携しない' for b in snapshot.get('buttons', [])):
            return 'RECRUIT_ID'
    stage = diagnose(snapshot)['stage']
    return stage['state'] if stage else None


def _ready(snapshot, stage):
    if snapshot.get('alert'):
        return True
    required = {'PHONE_INPUT_SCREEN': 1, 'SMS_CODE_SCREEN': 1, 'PASSWORD_SCREEN': 1,
                'KANA_NAME_SCREEN': 2, 'BIRTHDAY_SCREEN': 1}
    if len(snapshot.get('fields', [])) < required.get(stage, 0):
        return False
    if stage in ('PIN_SCREEN', 'RE_PIN_SCREEN'):
        return len([b for b in snapshot.get('buttons', []) if str(b.get('title') or '').isdigit()]) >= 10
    return stage == 'HOME_SCREEN' or any(b.get('enabled') for b in snapshot.get('buttons', []))


class FlowStop(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def run_signup(api, *, profile=None, args=None, otp_client=None, on_step=None):
    """Run/resume one account. Never resend SMS or retry a failed submission implicitly."""
    ensure_configured()
    settings = getattr(args, 'settings', None) if args is not None else None
    profile = normalize_profile(profile)
    timeout = max(0.01, float(getattr(args, 'wait_timeout_ms', 15000)) / 1000)
    poll = max(0.001, float(getattr(args, 'poll_ms', 400)) / 1000)
    max_steps = max(1, int(getattr(args, 'max_steps', 40)))
    no_submit = bool(getattr(args, 'no_submit', False))
    single_step = bool(getattr(args, 'single_step', False))
    history, notices = [], []
    snapshot = {}
    cursor = None
    account_created = False
    client = otp_client

    def record(action, before, after):
        item = {'state': _stage(before) or 'UNKNOWN', 'action': action,
                'topVC': before.get('topVC'), 'next_state': _stage(after), 'ok': True}
        history.append(item)
        if on_step:
            on_step(item, after)

    def result(status, ok=True, **extra):
        resume = {}
        if cursor is not None:
            resume = {'otp_after_seq': cursor.seq, 'phone_fingerprint': _fingerprint(profile['phone_number'])}
        last = history[-1] if history else {'state': _stage(snapshot), 'action': status}
        return {'ok': ok, 'status': status, 'complete': status == 'complete',
                'account_created': account_created, 'history': history, 'last': last,
                'snapshot': snapshot, 'notices': notices, 'resume': resume, **extra}

    def check(snap):
        report = diagnose(snap)
        for condition in report['conditions']:
            if condition['severity'] == 'error':
                raise FlowStop(condition['kind'], condition['message'])
            if condition['kind'] == 'account_created_card_notice' and condition not in notices:
                notices.append(condition)
        if report['displayed_error_codes']:
            raise FlowStop('app_error_code', 'App hiển thị mã lỗi; xem dump để kiểm tra.')
        if snap.get('alert') and _stage(snap) != 'BIOMETRIC_CHOICE':
            raise FlowStop('unmapped_alert', 'Giữ nguyên alert để kiểm tra, không tự chọn nút.')

    def wait_for(states):
        nonlocal snapshot
        deadline = time.monotonic() + timeout
        while True:
            snapshot = api.snapshot()
            check(snapshot)
            stage = _stage(snapshot)
            if stage in states and _ready(snapshot, stage):
                return snapshot
            if time.monotonic() >= deadline:
                raise FlowStop('transition_timeout', 'Hết thời gian chờ màn tiếp theo; không bấm lại tự động.')
            time.sleep(poll)

    def typed(index, value, vc):
        if not api.type_field(index, value, vc):
            raise FlowStop('input_mismatch', 'Không đối chiếu được nội dung vừa nhập; dừng trước nút tiếp tục.')

    def tap(title, vc):
        if not api.tap_button(title, vc):
            raise FlowStop('button_not_found', 'Nút cần dùng không còn khả dụng.')

    try:
        snapshot = api.snapshot()
        check(snapshot)
        if _stage(snapshot) == 'HOME_SCREEN':
            return result('already_home')
        profile = validate_profile(profile)
        if profile.get('otp_after_seq') is not None:
            if profile.get('phone_fingerprint') != _fingerprint(profile['phone_number']):
                raise FlowStop('otp_context_mismatch', 'Mốc OTP không thuộc số điện thoại trong profile.')
            cursor = OTPBaseCursor(int(profile['otp_after_seq']))
        for _ in range(max_steps):
            check(snapshot)
            stage = _stage(snapshot)
            if stage == 'HOME_SCREEN':
                return result('complete')
            if stage is None:
                raise FlowStop('screen_not_mapped', 'Màn chưa được kiểm chứng; xem dump trước khi bổ sung luồng.')
            if not _ready(snapshot, stage):
                snapshot = wait_for({stage})
            before = snapshot
            vc = snapshot['topVC']
            if stage.startswith('INTRO_'):
                current = int(stage[-1])
                tap('次へ進む' if current < 3 else 'はじめる', vc)
                after = wait_for({f'INTRO_{current + 1}'} if current < 3 else {'NOTIFICATION_INFO', 'ACCOUNT'})
                action = 'tutorial_next'
            elif stage == 'NOTIFICATION_INFO':
                tap('次へ進む', vc)
                after = wait_for({'ACCOUNT'})
                action = 'notification_next'
            elif stage in ('ACCOUNT', 'ACCOUNT_CREATE_SCREEN'):
                tap('アカウントの新規作成', vc)
                after = wait_for({'PHONE_INPUT_SCREEN'})
                action = 'open_phone_form'
            elif stage == 'PHONE_INPUT_SCREEN':
                if client is None:
                    client = build_otp_client(settings)
                if client is None:
                    raise FlowStop('otp_not_configured', 'Chưa có AIRWALLET_OTPBASE_API_KEY hoặc otpbase_api_key; chưa gửi SMS.')
                typed(0, profile['phone_number'], vc)
                cursor = client.capture_cursor(profile['phone_number'])
                if cursor.seq < 0:
                    raise FlowStop('otp_baseline_missing', 'OTPBase chưa trả mốc SMS hợp lệ; chưa gửi SMS.')
                tap('SMSでの認証へ進む', vc)
                profile.pop('otp', None)  # A manual OTP supplied before this new SMS may be stale.
                after = wait_for({'SMS_CODE_SCREEN'})
                action = 'request_sms'
            elif stage == 'SMS_CODE_SCREEN':
                code = str(profile.get('otp') or '').strip()
                if not code:
                    if cursor is None:
                        raise FlowStop('otp_context_missing', 'Thiếu mốc trước khi gửi SMS. Dùng --resume-dump từ lần chạy trước hoặc cung cấp OTP thủ công.')
                    if client is None:
                        client = build_otp_client(settings)
                    if client is None:
                        raise FlowStop('otp_not_configured', 'Chưa cấu hình API key OTPBase để chờ OTP.')
                    code = client.wait_for_otp(profile['phone_number'], cursor)
                if not re.fullmatch('[0-9]{4,8}', code):
                    raise FlowStop('invalid_otp', 'OTP phải gồm 4–8 chữ số.')
                typed(0, code, vc)
                tap('次へ進む', vc)
                after = wait_for({'PASSWORD_SCREEN'})
                action = 'verify_sms'
                profile.pop('otp', None)
            elif stage == 'PASSWORD_SCREEN':
                typed(0, profile['account_password'], vc)
                tap('次へ進む', vc)
                after = wait_for({'KANA_NAME_SCREEN'})
                action = 'set_login_password'
            elif stage == 'KANA_NAME_SCREEN':
                typed(0, profile['kana_sei'], vc)
                typed(1, profile['kana_mei'], vc)
                tap('次へ進む', vc)
                after = wait_for({'BIRTHDAY_SCREEN'})
                action = 'set_kana_name'
            elif stage == 'BIRTHDAY_SCREEN':
                birthday = date.fromisoformat(profile['birthday'])
                if not api.set_birthday(birthday.year, birthday.month, birthday.day, vc):
                    raise FlowStop('birthday_mismatch', 'Bộ chọn ngày không khớp ngày sinh; dừng trước nút đồng ý.')
                tap('上記に同意して次へ進む', vc)
                after = wait_for({'PIN_SCREEN'})
                action = 'set_birthday_and_continue'
            elif stage in ('PIN_SCREEN', 'RE_PIN_SCREEN'):
                if no_submit and stage == 'RE_PIN_SCREEN':
                    return result('ready_to_submit', ready_to_submit=True)
                if not api.enter_pin(profile['pin'], PIN_TITLE if stage == 'PIN_SCREEN' else RE_PIN_TITLE):
                    raise FlowStop('pin_input_failed', 'Nhập PIN chưa hoàn tất.')
                after = wait_for({'RE_PIN_SCREEN'} if stage == 'PIN_SCREEN' else {'BIOMETRIC_CHOICE', 'CREATION_COMPLETE_SCREEN'})
                action = 'set_pin' if stage == 'PIN_SCREEN' else 'confirm_pin'
            elif stage == 'BIOMETRIC_CHOICE':
                if no_submit:
                    return result('ready_to_submit', ready_to_submit=True)
                api.choose_alert('利用しない', snapshot['alert']['message'])
                after = wait_for({'CREATION_COMPLETE_SCREEN'})
                action = 'decline_biometrics'
            elif stage == 'CREATION_COMPLETE_SCREEN':
                account_created = True
                tap('次へ進む', vc)
                after = wait_for({'INVITATION_SCREEN', 'ACCOUNT_SETUP_SCREEN', 'RECRUIT_ID', 'HOME_SCREEN'})
                action = 'registration_completed_next'
            elif stage == 'INVITATION_SCREEN':
                tap('次へ進む', vc)
                after = wait_for({'ACCOUNT_SETUP_SCREEN', 'RECRUIT_ID', 'HOME_SCREEN'})
                action = 'skip_invitation'
            elif stage == 'ACCOUNT_SETUP_SCREEN':
                tap('あとで設定する', vc)
                after = wait_for({'RECRUIT_ID', 'HOME_SCREEN'})
                action = 'defer_bank_kyc_setup'
            elif stage == 'RECRUIT_ID':
                tap('今は連携しない', vc)
                after = wait_for({'HOME_SCREEN'})
                action = 'skip_recruit_id'
            else:
                raise FlowStop('screen_not_mapped', 'Màn chưa có thao tác iOS đã kiểm chứng.')
            snapshot = after
            record(action, before, after)
            if single_step:
                return result('complete' if _stage(snapshot) == 'HOME_SCREEN' else 'step_complete')
        if _stage(snapshot) == 'HOME_SCREEN':
            return result('complete')
        raise FlowStop('step_limit', 'Đã chạm giới hạn bước; xem dump trước khi tiếp tục.')
    except FlowStop as error:
        return result(error.status, False, message=error.message)
    except ValueError as error:
        return result('invalid_profile', False, message=str(error))
    except OTPBaseTimeoutError as error:
        return result('otp_timeout', False, message='[OTP_TIMEOUT] Không nhận được mã OTP mới. ' + str(error))
    except OTPBaseError as error:
        return result('otp_error', False, message=str(error))
    except Exception as error:
        message = str(error)
        for key in ('account_password', 'password', 'pin', 'otp'):
            value = str(profile.get(key) or '')
            if value:
                message = message.replace(value, '[redacted]')
        return result('runtime_error', False, message=message)
