"""WebDriverAgent backend (XCUITest event path).

Why this backend and not the Frida/UIKit agent: every mutating call here goes to
WebDriverAgent, which performs it with XCUITest -- ``XCUIElement.tap()`` and
``typeText()`` -- so the event travels the app's real touch/keyboard pipeline:
hit-testing, gesture recognisers, ``UIControl`` state changes, first-responder
and keyboard notifications, ``UITextFieldDelegate`` callbacks. A target/action
dispatch (``sendActionsForControlEvents:``) or a direct ``insertText:`` skips
most of that; see ``docs/ios-interaction-methods.md``.

Element handles: WDA's ``/source`` tree carries no element UUIDs, so
:class:`WebDriverAgentBackend` returns a *descriptor* as ``Element.ref`` and
resolves it to a live WDA element id at action time. That re-resolution is also a
staleness check -- if the element has gone, the action fails rather than hitting
whatever moved into its place.

Status: written against the WebDriverAgent HTTP routes as documented by the
project (``/session/{id}/source``, ``/elements``, ``/element/{id}/click``,
``/element/{id}/value``, ``/wda/keyboard/dismiss``). It has **not** been
exercised against a device in this repository -- see "Verification status" in
``docs/ui-automation-architecture.md`` before relying on it.
"""
from __future__ import annotations

import json
from typing import Any

from ..errors import ElementNotFoundError, InfrastructureError
from ..model import Alert, Element, Rect, Snapshot

# Attribute names in WDA's JSON source tree.
_TYPE = "type"
_CHILDREN = "children"

ALERT_TYPES = frozenset({"XCUIElementTypeAlert", "XCUIElementTypeSheet"})
SESSION_FAULTS = frozenset({"invalid session id", "unknown error", "no such driver",
                            "session not created"})
KEYBOARD_TYPE = "XCUIElementTypeKeyboard"


def _rect(node: dict[str, Any]) -> Rect:
    frame = node.get("rect") or {}
    return Rect(float(frame.get("x", 0)), float(frame.get("y", 0)),
                float(frame.get("width", 0)), float(frame.get("height", 0)))


def _descriptor(node: dict[str, Any], path: tuple[int, ...]) -> str:
    """Opaque ref: identifier when there is one, else a document path."""
    identifier = str(node.get("name") or "")
    return json.dumps({"id": identifier, "type": node.get(_TYPE, ""), "path": list(path)},
                      ensure_ascii=False, sort_keys=True)


class WebDriverAgentBackend:
    """Drive a licensed test build through WebDriverAgent over HTTP."""

    def __init__(self, session, base_url: str, session_id: str, bundle_id: str, *,
                 request_timeout: float = 15.0,
                 container_id_provider=None) -> None:
        """``session`` is anything with ``requests.Session``'s ``get``/``post``
        interface, injected so tests can supply a double.

        ``container_id_provider`` is an optional zero-argument callable that
        returns the active Crane container id. XCUITest knows nothing about
        Crane, so the id is supplied from outside and stamped onto every
        snapshot -- that is what lets a dump prove which container produced it.
        It is read once and cached: a container cannot change while a
        :class:`~uitest.crane.CraneContainerSession` holds the app, and that
        session fails the run if it does.
        """
        self._session = session
        self._base = base_url.rstrip("/")
        self._session_id = session_id
        self._bundle_id = bundle_id
        self._timeout = float(request_timeout)
        self._closed = False
        self._container_id_provider = container_id_provider
        self._container_id: str | None = None

    @property
    def name(self) -> str:
        return "webdriveragent"

    # --- transport ----------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base}/session/{self._session_id}{path}"

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        call = self._session.post if method == "POST" else self._session.get
        try:
            if method == "POST":
                response = call(self._url(path), json=payload or {}, timeout=self._timeout)
            else:
                response = call(self._url(path), timeout=self._timeout)
        except Exception as error:  # transport-level: connection, timeout, DNS
            raise InfrastructureError(f"WDA {method} {path} failed: {error}") from error
        status = getattr(response, "status_code", 200)
        try:
            body = response.json()
        except Exception as error:
            raise InfrastructureError(f"WDA {method} {path} returned non-JSON: {error}") from error
        if status >= 500:
            raise InfrastructureError(f"WDA {method} {path} -> HTTP {status}: {body!r}")
        if isinstance(body, dict) and isinstance(body.get("value"), dict):
            # A dead session is a transport fault (retryable); a missing element is not.
            if body["value"].get("error") in SESSION_FAULTS:
                raise InfrastructureError(f"WDA session fault on {path}: {body['value']!r}")
        return body.get("value") if isinstance(body, dict) else body

    # --- reading ------------------------------------------------------------

    def capture(self) -> Snapshot:
        source = self._request("GET", "/source?format=json")
        if not isinstance(source, dict):
            raise InfrastructureError(f"WDA /source returned {type(source).__name__}, expected an object.")
        active = self._request("GET", "/wda/activeAppInfo") or {}
        bundle_id = str(active.get("bundleId") or self._bundle_id)
        window = _rect(source) if source.get("rect") else Rect(0, 0, 0, 0)
        alert: Alert | None = None
        keyboard: Rect | None = None

        def build(node: dict[str, Any], path: tuple[int, ...]) -> Element:
            nonlocal alert, keyboard
            node_type = str(node.get(_TYPE) or "")
            if node_type in ALERT_TYPES and alert is None:
                alert = self._alert_from(node)
            if node_type == KEYBOARD_TYPE and node.get("visible", True):
                keyboard = _rect(node)
            children = tuple(
                build(child, path + (index,))
                for index, child in enumerate(node.get(_CHILDREN) or [])
                if isinstance(child, dict)
            )
            return Element(
                ref=_descriptor(node, path), type=node_type,
                identifier=str(node.get("name") or ""), label=str(node.get("label") or ""),
                value=None if node.get("value") is None else str(node.get("value")),
                placeholder=str(node.get("placeholderValue") or ""),
                enabled=bool(node.get("enabled", True)), visible=bool(node.get("visible", True)),
                hittable=bool(node.get("hittable", node.get("visible", True))),
                focused=bool(node.get("hasFocus", False)),
                protected=node_type == "XCUIElementTypeSecureTextField",
                frame=_rect(node), children=children,
            )

        root = build(source, ())
        screen_id = self._screen_id(root)
        return Snapshot(bundle_id=bundle_id, screen_id=screen_id, root=root,
                        window=window if not window.empty else root.frame,
                        alert=alert, keyboard=keyboard,
                        container_id=self._resolve_container_id())

    def _resolve_container_id(self) -> str:
        """The active container id, read once and cached.

        A provider failure must not fail a read: the snapshot simply carries no
        container id, and the caller can see that it is missing rather than get
        a wrong one.
        """
        if self._container_id is not None:
            return self._container_id
        if self._container_id_provider is None:
            self._container_id = ""
            return self._container_id
        try:
            self._container_id = str(self._container_id_provider() or "")
        except Exception:
            self._container_id = ""
        return self._container_id

    @staticmethod
    def _alert_from(node: dict[str, Any]) -> Alert:
        buttons: list[str] = []
        texts: list[str] = []

        def walk(current: dict[str, Any]) -> None:
            node_type = str(current.get(_TYPE) or "")
            if node_type == "XCUIElementTypeButton":
                label = str(current.get("label") or current.get("name") or "")
                if label:
                    buttons.append(label)
            elif node_type in {"XCUIElementTypeStaticText", "XCUIElementTypeTextView"}:
                text = str(current.get("label") or current.get("value") or "")
                if text:
                    texts.append(text)
            for child in current.get(_CHILDREN) or []:
                if isinstance(child, dict):
                    walk(child)

        walk(node)
        title = texts[0] if texts else str(node.get("label") or "")
        message = texts[1] if len(texts) > 1 else ""
        return Alert(identifier=str(node.get("name") or ""), title=title, message=message,
                     buttons=tuple(buttons), system=False)

    @staticmethod
    def _screen_id(root: Element) -> str:
        """Screen identity for XCUITest, which does not expose the view controller.

        Uses the first navigation-bar/other-element identifier found, falling
        back to the window's own identifier. Supply a project-specific override
        by subclassing when the app has a better signal (for example a hidden
        element carrying the screen name).
        """
        for element in root.walk():
            if element.type == "XCUIElementTypeNavigationBar" and element.identifier:
                return element.identifier
        for element in root.walk():
            if element.identifier.startswith("screen."):
                return element.identifier
        return root.identifier or "unknown"

    # --- element resolution -------------------------------------------------

    def _resolve_ref(self, ref: str) -> str:
        """Turn a descriptor into a live WDA element id."""
        descriptor = json.loads(ref)
        identifier = descriptor.get("id") or ""
        if not identifier:
            raise ElementNotFoundError(
                "This element has no accessibility identifier, so it cannot be addressed through "
                "WebDriverAgent. Add an identifier in the app under test."
            )
        found = self._request("POST", "/elements", {"using": "accessibility id", "value": identifier})
        if not isinstance(found, list) or not found:
            raise ElementNotFoundError(f"WDA no longer finds an element with identifier {identifier!r}.")
        if len(found) > 1:
            raise ElementNotFoundError(
                f"WDA finds {len(found)} elements with identifier {identifier!r}; identifiers must be unique."
            )
        entry = found[0]
        element_id = entry.get("ELEMENT") or entry.get("element-6066-11e4-a52e-4f735466cecf")
        if not element_id:
            raise InfrastructureError(f"WDA element payload has no id: {entry!r}")
        return str(element_id)

    # --- acting -------------------------------------------------------------

    def tap(self, ref: str) -> None:
        self._request("POST", f"/element/{self._resolve_ref(ref)}/click")

    def type_text(self, ref: str, text: str) -> None:
        element_id = self._resolve_ref(ref)
        # WDA focuses the element (a real tap) before typing through the keyboard.
        self._request("POST", f"/element/{element_id}/click")
        self._request("POST", f"/element/{element_id}/value", {"value": list(text)})

    def clear_text(self, ref: str) -> None:
        self._request("POST", f"/element/{self._resolve_ref(ref)}/clear")

    def scroll_into_view(self, ref: str) -> None:
        element_id = self._resolve_ref(ref)
        self._request("POST", f"/wda/element/{element_id}/scroll", {"toVisible": True})

    def dismiss_keyboard(self) -> None:
        self._request("POST", "/wda/keyboard/dismiss")

    def tap_alert_button(self, label: str) -> None:
        self._request("POST", "/alert/accept", {"name": label})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        delete = getattr(self._session, "delete", None)
        if delete is None:
            return
        try:
            delete(f"{self._base}/session/{self._session_id}", timeout=self._timeout)
        except Exception:
            pass  # the session is being torn down either way
