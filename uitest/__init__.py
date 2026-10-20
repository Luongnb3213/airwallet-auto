"""UI testing adapter for licensed iOS test builds.

Scope: this package drives an app under test through a UI *testing* framework
(XCUITest via WebDriverAgent), against targets that have been explicitly
declared as licensed test/demo builds. It deliberately offers no coordinate
taps, no target/action dispatch, no direct text-storage writes, and no
device-identity or Keychain manipulation.

Entry points::

    from uitest import UITestAdapter, Selector, Budget
    from uitest.expectations import ScreenIs, ElementEnabled
    from uitest.policy import AuthorizedTarget, AlertPolicy, AlertRule

See ``docs/ui-automation-architecture.md`` for the design and
``docs/ios-interaction-methods.md`` for how the interaction layers differ.
"""
from .adapter import ActionRecord, UITestAdapter
from .crane import CraneContainerSession, ContainerState, prepare_container
from .policy import AlertPolicy, AlertRule, AuthorizedTarget, TargetRegistry
from .selectors import Selector
from .waits import Budget, Deadline, Waiter

__all__ = [
    "ActionRecord",
    "AlertPolicy",
    "AlertRule",
    "AuthorizedTarget",
    "Budget",
    "ContainerState",
    "CraneContainerSession",
    "Deadline",
    "Selector",
    "TargetRegistry",
    "UITestAdapter",
    "Waiter",
    "prepare_container",
]
