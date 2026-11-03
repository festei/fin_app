from __future__ import annotations

from fin_application.errors import ChannelError
from fin_application.ports import AlertMessage
from fin_domain.signals import AlertPolicy


class FakeChannel:
    """Records what it was asked to send. ``fail`` makes it raise a ``ChannelError``; ``crash``
    makes it raise something unexpected."""

    def __init__(self, name: str = "fake", *, fail: bool = False, crash: Exception | None = None):
        self.name, self.fail, self.crash = name, fail, crash
        self.sent: list[AlertMessage] = []
        self.on_send = None  # optional hook, called before the outcome is decided

    def send(self, message: AlertMessage) -> None:
        if self.on_send:
            self.on_send(message)
        if self.crash:
            raise self.crash
        if self.fail:
            raise ChannelError("simulated outage")
        self.sent.append(message)


class FixedPolicy:
    """An ``AlertPolicySource`` that always answers with the policy it was given."""

    def __init__(self, policy: AlertPolicy | None = None):
        self.policy = policy or AlertPolicy()

    def load(self) -> AlertPolicy:
        return self.policy
