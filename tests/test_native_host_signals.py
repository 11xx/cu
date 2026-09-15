import os
import signal
import stat

from cu import browser_read as wire


def test_the_host_turns_signals_into_selector_events_and_restores_the_wakeup_fd(monkeypatch):
    calls = []
    real = signal.set_wakeup_fd

    def record(fd, **kwargs):
        previous = real(fd, **kwargs)
        calls.append((fd, previous))
        return previous

    monkeypatch.setattr(wire.signal, "set_wakeup_fd", record)
    stdin_read, stdin_write = os.pipe()
    os.close(stdin_write)
    stdout_read, stdout_write = os.pipe()
    wakeup_kinds = []
    real_register = wire.selectors.DefaultSelector.register

    def register(selector, fileobj, events, data=None):
        if data and data[0] == "signal":
            wakeup_kinds.append(stat.S_ISSOCK(os.fstat(fileobj.fileno()).st_mode))
        return real_register(selector, fileobj, events, data)

    monkeypatch.setattr(wire.selectors.DefaultSelector, "register", register)
    with (
        os.fdopen(stdin_read, "rb") as stdin,
        os.fdopen(stdout_write, "wb") as stdout,
        os.fdopen(stdout_read, "rb"),
    ):
        assert wire.NativeMessagingHost(stdin=stdin, stdout=stdout).run() == 0

    assert wakeup_kinds == [True]
    (installed, original), (restored, replaced) = calls
    assert installed >= 0 and restored == original
    assert replaced == installed
