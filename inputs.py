"""Load registration data without conflating the two password fields."""
import json
from pathlib import Path

from flow import ROOT, normalize_profile


def select_device_id(requested=None, *, profile=None, settings=None):
    """Explicit CLI/env device wins; auto uses the row assignment before config."""
    for candidate in (requested, (profile or {}).get('frida_device_id'), (settings or {}).get('device_id')):
        value = str(candidate or '').strip()
        if value and value.lower() != 'auto':
            return value
    return 'auto'


def read_json_profile(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    profile = data.get('profile', data) if isinstance(data, dict) else None
    if not isinstance(profile, dict):
        raise ValueError('Profile JSON cần là object hoặc có object profile.')
    return profile


def load_signup_input(args, settings):
    profile = dict(settings.get('registration_defaults') or {})
    if getattr(args, 'profile_json', None):
        profile.update(read_json_profile(args.profile_json))
    else:
        from scripts.airwallet_profile_from_excel import load_row, profile_from_record
        raw = getattr(args, 'xlsx', None) or settings.get('xlsx_path')
        if not raw:
            raise ValueError('Cần --profile-json hoặc --xlsx/config xlsx_path.')
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        sheet = getattr(args, 'sheet', None) or settings.get('active_sheet', 'Inputs')
        record, row_number, actual_sheet = load_row(path, sheet, getattr(args, 'row', None))
        args.input_xlsx, args.input_sheet, args.input_row = path, actual_sheet, row_number
        profile.update(profile_from_record(record))
    if getattr(args, 'resume_dump', None):
        resume = json.loads(Path(args.resume_dump).read_text(encoding='utf-8'))
        context = resume.get('resume', {})
        for key in ('otp_after_seq', 'phone_fingerprint'):
            if key in context:
                profile[key] = context[key]
    if getattr(args, 'otp', None):
        profile['otp'] = args.otp
    return normalize_profile(profile)
