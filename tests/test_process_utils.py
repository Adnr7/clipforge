from backend import process_utils


def test_native_processes_are_hidden_only_on_windows(monkeypatch):
    monkeypatch.setattr(process_utils.os, 'name', 'posix')
    assert process_utils.hidden_process_kwargs() == {}

    monkeypatch.setattr(process_utils.os, 'name', 'nt')
    options = process_utils.hidden_process_kwargs()
    assert options['creationflags'] & 0x08000000
