# dpi_awareness.py
"""
Windows per-monitor DPI awareness, shared by every entry point that pins or measures
the Roblox window - not just gui.py, which is where this was first fixed.

Why this matters here specifically: this codebase drives two APIs that disagree under
display scaling. win32 (SetWindowPos/GetClientRect, which Windows feeds virtualised
coordinates to an unaware process) and mss (which always captures real physical
pixels). At 100% scaling they agree and nothing shows. At any other scaling an unaware
process's "1920x1080" and mss's "1920x1080" stop being the same number of physical
pixels, and every screenshot taken while pinning at a size decided by the unaware math
comes out some OTHER physical size - silently. That is indistinguishable, from every
other angle, from "the templates are broken" or "the crop tool is buggy".

gui.py sets this before any other import specifically because Windows locks a
process's DPI awareness the first time certain APIs are touched and refuses to change
it afterwards - importing customtkinter alone is enough to lock it in as unaware.
tools/template_capture.py and tools/retemplate.py hit the exact same win32 calls
(MoveWindow, GetClientRect) but never called this, which is one plausible way a
capture session ends up with crops sized for a client that was never actually
1920x1080.

Usage: call set_dpi_awareness() as the very first line of your script's own code,
before importing anything else that might itself query DPI.
"""
import ctypes


def set_dpi_awareness():
    """
    Requests per-monitor DPI awareness, falling back through older APIs on older
    Windows. Returns a short string describing what was actually achieved (or how it
    failed) - meant to be printed once at startup so a silent failure here still shows
    up in the log instead of just being a hard-to-explain scale error later.
    """
    # Newest and most reliable (Windows 10 1703+): survives longer into the process
    # lifecycle than the older APIs below.
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        result = ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_int(-4))
        if result:
            return "per-monitor v2 (SetProcessDpiAwarenessContext)"
    except Exception:
        pass

    # Windows 8.1+
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return "per-monitor (shcore)"
    except Exception as e:
        # Last resort: system-aware rather than per-monitor, but still aware -
        # Windows Vista+.
        try:
            if ctypes.windll.user32.SetProcessDPIAware():
                return "system-aware (user32)"
            return f"FAILED - all APIs refused ({e})"
        except Exception as e2:
            return f"FAILED - {e2}"
