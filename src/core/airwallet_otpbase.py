"""Read fresh COIN+ SMS for one phone through the authorized OTPBase API."""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

class OperationCancelled(RuntimeError):
    pass

DEFAULT_OTPBASE_KEY = ""
OTPBASE_OTP_URL = "https://otpbase.site/api/v1/otp"


class OTPBaseError(RuntimeError):
    """An actionable API error, without credentials or SMS contents."""


class OTPBaseTemporaryError(OTPBaseError):
    """A network or server error that polling may retry."""


class OTPBaseTimeoutError(OTPBaseError):
    """No fresh valid OTP arrived before the polling deadline."""


def normalize_phone(value: str) -> str:
    number = re.sub(r"\D", "", str(value or ""))
    if number.startswith("81") and len(number) == 12:
        number = "0" + number[2:]
    return number


def _seq(value: object) -> int:
    return int(str(value)) if str(value).isascii() and str(value).isdigit() else -1


def _message_phone(message: dict) -> str:
    for key in ("phone", "tel", "number", "to", "recipient"):
        value = str(message.get(key) or "").strip()
        if value:
            return value
    return ""


def _message_content(message: dict) -> str:
    for key in ("content", "message", "sms", "text", "body"):
        value = str(message.get(key) or "")
        if value:
            return unicodedata.normalize("NFKC", value)
    nested = message.get("data")
    if isinstance(nested, dict):
        return _message_content(nested)
    return ""


def _coin_otp_from_content(content: str) -> str | None:
    match = re.search(
        r"COIN\s*\+\s*認証コード\s*[:：]\s*([0-9]{4,8})(?![0-9])",
        unicodedata.normalize("NFKC", str(content or "")),
        re.I,
    )
    return match.group(1) if match else None


@dataclass(frozen=True)
class OTPBaseCursor:
    """OTPBase sequence captured before sending SMS."""

    seq: int = -1


class OTPBaseClient:
    def __init__(self, api_key: str, *, cancel: threading.Event | None = None,
                 timeout: float = 120.0, poll_interval: float = 2.0) -> None:
        self._api_key = api_key.strip()
        if not self._api_key:
            raise OTPBaseError("Chưa có API key OTPBase. Hãy nhập API key trước khi chạy.")
        self.cancel = cancel if cancel is not None else threading.Event()
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.last_otp_source = ""

    def _check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise OperationCancelled("Đã dừng chờ OTP theo yêu cầu người dùng.")

    def _request(self, url: str, phone: str, *, request_timeout: float = 15.0, **params: int | str) -> dict:
        self._check_cancelled()
        query = urlencode({"phone": normalize_phone(phone), **params})
        req = Request(f"{url}?{query}", headers={"x-api-key": self._api_key})
        try:
            with urlopen(req, timeout=request_timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status == 401:
                raise OTPBaseError("API key OTPBase không đúng hoặc đã bị thu hồi.") from None
            if status == 403:
                raise OTPBaseError("API key OTPBase chưa được cấp quyền cho số điện thoại này.") from None
            error = OTPBaseTemporaryError if status == 429 or status >= 500 else OTPBaseError
            raise error(f"OTPBase trả HTTP {status}.") from None
        except (URLError, TimeoutError, OSError):
            raise OTPBaseTemporaryError("Không kết nối được OTPBase. Kiểm tra mạng và thử lại.") from None
        except (ValueError, UnicodeError):
            raise OTPBaseError("OTPBase trả dữ liệu JSON không hợp lệ.") from None
        self._check_cancelled()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise OTPBaseError("OTPBase không chấp nhận yêu cầu. Kiểm tra API key và quyền của số điện thoại.")
        return data

    def _otp(self, phone: str, *, request_timeout: float = 15.0, **params: int | str) -> dict:
        data = self._request(OTPBASE_OTP_URL, phone, request_timeout=request_timeout, **params)
        if "found" not in data:
            raise OTPBaseError("OTPBase /otp không trả field `found` hợp lệ.")
        return data

    def capture_cursor(self, phone: str) -> OTPBaseCursor:
        data = self._otp(phone)
        cursor = max(_seq(data.get("seq")), _seq(data.get("last_seq")))
        return OTPBaseCursor(seq=cursor)

    def _code_from_otp_response(self, data: dict, phone: str, cursor: OTPBaseCursor) -> str | None:
        self.last_otp_source = ""
        if data.get("found") is not True:
            return None
        if cursor.seq >= 0:
            seq = max(_seq(data.get("seq")), _seq(data.get("last_seq")))
            if seq <= cursor.seq:
                return None
        else:
            return None
        if normalize_phone(_message_phone(data)) != normalize_phone(phone):
            return None

        otp = re.sub(r"\D", "", str(data.get("otp") or ""))
        if 4 <= len(otp) <= 8:
            self.last_otp_source = "otp"
            return otp

        # Fallback cho trường hợp API để trống field `otp` nhưng nội dung SMS
        # vẫn có mã, ví dụ: `COIN+認証コード:2056`.
        content = _message_content(data)
        code = _coin_otp_from_content(content)
        if code:
            self.last_otp_source = "message"
            return code
        return None

    def wait_for_otp(self, phone: str, after_seq: OTPBaseCursor | int, *, timeout: float | None = None) -> str:
        cursor = after_seq if isinstance(after_seq, OTPBaseCursor) else OTPBaseCursor(seq=int(after_seq))
        wait_timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + wait_timeout
        last_error = ""
        while (remaining := deadline - time.monotonic()) > 0:
            self._check_cancelled()
            try:
                params: dict[str, int] = {}
                if cursor.seq >= 0:
                    params["after_seq"] = cursor.seq
                data = self._otp(phone, request_timeout=min(15.0, remaining), **params)
                last_error = ""
                code = self._code_from_otp_response(data, phone, cursor)
                if code:
                    return code
            except OTPBaseTemporaryError as exc:
                last_error = str(exc)
            if self.cancel.wait(min(self.poll_interval, max(0, deadline - time.monotonic()))):
                self._check_cancelled()
        raise OTPBaseTimeoutError(
            f"Hết {wait_timeout:g}s chờ OTP COIN+ mới cho số điện thoại đã nhập."
            + (f" {last_error}" if last_error else "")
        )
