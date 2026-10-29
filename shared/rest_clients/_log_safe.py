# -*- coding: utf-8 -*-
"""
_log_safe.py — encoding-proof logging for the REST clients.

Why this exists
───────────────
Windows consoles default to cp1252. Printing an emoji such as '✅' (U+2705) raises
UnicodeEncodeError. Every client in this package logs progress with emoji, and several
of them do it *inside* the same try/except that guards the HTTP call, e.g.:

    try:
        resp = self._session.get(url)
        ...
        self._access_token = data['accessToken']      # success — state is set
        self.log('[NBOP-REST] ✅ Portal token obtained')   # <-- raises on cp1252
        return True
    except Exception as e:
        self.log(f'[NBOP-REST] createToken error: {e}')
        return False                                   # <-- reports a FALSE failure

Observed live: NBOP REST reported

    [NBOP-REST] createToken error: 'charmap' codec can't encode character '\u2705'

…even though the token had been obtained. The method returned False, so every NBOP
subscriber lookup silently fell back to a ~47 second browser scrape instead of a ~1
second REST call.

A log line must never be able to fail a request. Wrap loggers with safe_logger().
"""


def safe_logger(fn):
    """Return a logger that never raises.

    Tries the original logger, then an ASCII-folded version (emoji become '?'),
    then gives up silently. Never propagates an exception to the caller.

    Args:
        fn: the underlying log callable (e.g. print, or a dashboard LiveLog).

    Returns:
        A drop-in replacement callable taking a single message argument.
    """
    if fn is None:
        fn = print

    def _log(msg):
        try:
            fn(msg)
        except UnicodeEncodeError:
            try:
                fn(str(msg).encode('ascii', 'replace').decode('ascii'))
            except Exception:
                pass
        except Exception:
            # A logging sink that is closed/broken must not fail the API call either.
            pass

    return _log
