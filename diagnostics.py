"""Read-only screen/error diagnosis using the supplied LINE_COIN reference."""
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / 'agents' / 'registration_reference.json'
_ERROR_CODE = re.compile(
    r'(?:エラーコード|エラー番号|error\s*code|mã\s*lỗi)\s*[:：=\[（(]?\s*([A-Z0-9][A-Z0-9_-]{2,31})', re.I
)


def visible_text(snapshot):
    alert = snapshot.get('alert') or {}
    values = list(snapshot.get('labels') or [])
    values.extend([alert.get('title'), alert.get('message')])
    values.extend(button.get('title') for button in snapshot.get('buttons', []))
    return '\n'.join(value for value in values if isinstance(value, str))


def detect_conditions(text):
    conditions = []
    phone_marker = 'この電話番号はすでに他のアカウントで'
    if phone_marker in text:
        conditions.append({'kind': 'phone_already_used', 'severity': 'error',
                           'message': 'Số điện thoại đã được tài khoản khác sử dụng.',
                           'source': 'LINE_COIN/flows/screens.py:PHONE_ALREADY_USED'})
    if '現在サービスをご利用いただけません' in text:
        conditions.append({'kind': 'service_unavailable', 'severity': 'error',
                           'message': 'Dịch vụ hiện không khả dụng. LINE_COIN gợi ý kiểm tra proxy Nhật; chưa xác định nguyên nhân.',
                           'source': 'LINE_COIN/flows/registration.py:_raise_if_proxy_unavailable'})
    if 'アカウントが作成されました' in text and any(marker in text for marker in
            ('メンテナンス中', '番号を発行できませんでした')):
        conditions.append({'kind': 'account_created_card_notice', 'severity': 'notice',
                           'message': 'Có thông báo đã tạo tài khoản kèm trạng thái thẻ Visa; kiểm tra riêng việc cấp thẻ.',
                           'source': 'LINE_COIN/flows/registration.py:_is_card_maintenance_completion'})
    return conditions


def screen_candidates(snapshot, spec, text):
    vc = snapshot.get('topVC', '')
    if vc == 'Sgc.TutorialViewController' and len(snapshot.get('pages', [])) == 1:
        page = snapshot['pages'][0]['current']
        name = f'INTRO_{page + 1}'
        if name in spec['entry_screens']:
            return [{'state': name, 'title': spec['entry_screens'][name]['title']}]
    known = {'Sgc.PushNotificationAppealViewController': 'NOTIFICATION_INFO',
             'Sgc.AccountCreateViewController': 'ACCOUNT',
             'Sgc.InputPhoneViewController': 'PHONE_INPUT_SCREEN',
             'Sgc.SMSValidationRegistrationViewController': 'SMS_CODE_SCREEN',
             'Sgc.InputPasswordViewController': 'PASSWORD_SCREEN',
             'Sgc.InputNameViewController': 'KANA_NAME_SCREEN',
             'Sgc.InputBirthdayViewController': 'BIRTHDAY_SCREEN',
             'Sgc.RegistrationCompleteViewController': 'CREATION_COMPLETE_SCREEN',
             'Sgc.RegistrationInputInvitationCodeRouter': 'INVITATION_SCREEN',
             'Sgc.BankAccountEKYCPromotionViewController': 'ACCOUNT_SETUP_SCREEN',
             'Sgc.MasterTopViewController': 'HOME_SCREEN'}
    if vc == 'Sgc.PasscodeSetupViewController':
        titles = {'暗証番号の入力': 'PIN_SCREEN', '暗証番号の再入力': 'RE_PIN_SCREEN'}
        state = titles.get(snapshot.get('title'))
        if state:
            return [{'state': state, 'title': snapshot['title']}]
    if vc in known:
        return [{'state': known[vc], 'title': vc}]
    if snapshot.get('alert'):
        return []
    candidates = []
    specs = [(name, value) for name, value in spec['entry_screens'].items()]
    specs += [(value['state'], value) for value in spec['registration_screens'] + spec.get('optional_screens', [])]
    for state, value in specs:
        if any(marker in text for marker in value.get('excluded_markers', [])):
            continue
        # A common Next button alone cannot establish screen identity.
        markers = [marker for marker in value['markers'] if marker not in value['primary_button']]
        if markers and any(marker in text for marker in markers):
            candidate = {'state': state, 'title': value['title']}
            if not any(c['state'] == state for c in candidates):
                candidates.append(candidate)
    return candidates


def diagnose(snapshot):
    spec = json.loads(SPEC_PATH.read_text(encoding='utf-8'))
    text = visible_text(snapshot)
    conditions = detect_conditions(text)
    if snapshot.get('alert') and not conditions:
        conditions.append({'kind': 'unmapped_alert', 'severity': 'review',
                           'message': 'Alert chưa có quy tắc trong nguồn tham chiếu; giữ nguyên để người dùng kiểm tra.'})
    codes = list(dict.fromkeys(_ERROR_CODE.findall(unicodedata.normalize('NFKC', text))))
    candidates = screen_candidates(snapshot, spec, text)
    return {'stage': candidates[0] if len(candidates) == 1 else None,
            'stage_candidates': candidates, 'conditions': conditions,
            'displayed_error_codes': codes,
            'needs_review': len(candidates) != 1 or bool(codes) or bool(conditions)}


def pretty_diagnosis(result):
    stage = result.get('stage')
    lines = ['stage: ' + (stage['state'] if stage else 'chưa xác định duy nhất')]
    for condition in result['conditions']:
        lines.append(condition['kind'] + ': ' + condition['message'])
    if result['displayed_error_codes']:
        lines.append('Mã lỗi hiển thị (chưa gán ý nghĩa): ' + ', '.join(result['displayed_error_codes']))
    return '\n'.join(lines)
