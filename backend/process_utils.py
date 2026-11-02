"""Small helpers for running native tools without flashing console windows."""

import os
import subprocess


def hidden_process_kwargs() -> dict:
    """Return subprocess options that keep console tools invisible on Windows."""
    if os.name != 'nt':
        return {}

    # CREATE_NO_WINDOW prevents console applications such as FFmpeg and
    # FFprobe from opening a transient terminal when ClipForge is launched as
    # a windowed executable. The fallback keeps the helper usable on hosts
    # where the Windows-only constant is not exposed.
    create_no_window = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    detached_process = getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
    options = {
        # The detached flag covers older/native console builds; CREATE_NO_WINDOW
        # covers normal console tools. Together with STARTF_USESHOWWINDOW this
        # prevents both a visible terminal and an inherited console attachment.
        'creationflags': create_no_window | detached_process,
    }
    startupinfo_type = getattr(subprocess, 'STARTUPINFO', None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, 'STARTF_USESHOWWINDOW', 1)
        startupinfo.wShowWindow = getattr(subprocess, 'SW_HIDE', 0)
        options['startupinfo'] = startupinfo
    return options
