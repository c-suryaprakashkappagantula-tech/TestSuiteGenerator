# -*- coding: utf-8 -*-
"""
browser_focus.py — bring a freshly-opened login/reauth browser window to the FRONT.

Headed Playwright login windows (SSO / cookie-refresh) frequently open in the background
or minimized, so the user can't find them and the run appears to hang. This module raises
the window to the foreground, maximized and focused, beating the Windows foreground-lock
(SetForegroundWindow alone only flashes the taskbar when the caller isn't the active app).

Usage — right after navigating a headed login page:

    from shared.browser_focus import pop_to_front
    page.goto(login_url, ...)
    pop_to_front(page)          # Playwright bring_to_front + OS-level raise (Windows)

Everything is best-effort and fully fail-safe: any error (non-Windows, missing ctypes,
API failure) is swallowed so it can NEVER break a login flow. No-op on non-Windows.
"""

import sys
import time

# Substrings we match against visible window titles to find the login/browser window.
_TITLE_HINTS = (
    'sign in', 'login', 'log in', 'edge', 'chrome',
    'genesis', 't-mobile', 'tmobile', 'spectrum', 'century', 'nbop', 'apollo',
    'sm360', 'chalk', 'jira', 'okta', 'ping', 'microsoft', 'authenticat',
)


def _raise_windows(title_hints=_TITLE_HINTS):
    """Windows-only: find matching visible top-level windows and force them to the front.
    Returns the count of windows it acted on."""
    if sys.platform != 'win32':
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_MINIMIZE = 6
    SW_RESTORE = 9
    SW_SHOWMAXIMIZED = 3
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001

    matches = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = (buf.value or '').lower()
            if any(h in title for h in title_hints):
                matches.append(hwnd)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_enum, 0)
    except Exception:
        return 0

    def _force(hwnd):
        try:
            fg = user32.GetForegroundWindow()
            cur_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg, None)
            tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
            # Attach input queues so Windows lets us take the foreground.
            user32.AttachThreadInput(cur_tid, fg_tid, True)
            user32.AttachThreadInput(tgt_tid, fg_tid, True)
            # Toggle to knock it loose from a minimized/background state, then raise.
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.AttachThreadInput(cur_tid, fg_tid, False)
            user32.AttachThreadInput(tgt_tid, fg_tid, False)
        except Exception:
            try:
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    for hwnd in matches:
        _force(hwnd)
    return len(matches)


def pop_to_front(page=None, retries: int = 4, delay: float = 0.4, title_hints=None):
    """Bring the login window to the foreground, maximized.

    Args:
        page: optional Playwright Page — if given, page.bring_to_front() is called first
              (moves the tab to the front within the browser).
        retries: how many times to attempt the OS-level raise (the window title settles a
                 beat after navigation, so a couple of tries catches it reliably).
        delay: seconds between retries.
        title_hints: optional custom iterable of lowercase title substrings to match.

    Best-effort and fail-safe: never raises. No-op on non-Windows (aside from the
    Playwright bring_to_front, which is cross-platform)."""
    # 1) Playwright-level: move the tab to the front (cross-platform, harmless).
    if page is not None:
        try:
            page.bring_to_front()
        except Exception:
            pass

    # 2) OS-level: force the window up (Windows only). Retry while the title settles.
    hints = tuple(title_hints) if title_hints else _TITLE_HINTS
    for _ in range(max(1, int(retries))):
        try:
            _raise_windows(hints)
        except Exception:
            pass
        time.sleep(max(0.0, float(delay)))
