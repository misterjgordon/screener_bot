"""Launch and wait for an IB live session when unavailable.

This module is intended as the entry point when IB is not yet logged in.
It tries to connect first; if unavailable, it launches Trader Workstation,
optionally submits username/password from .env, then polls while you approve 2FA.

Autofill uses AppleScript against the System Events **process** name (often ``tws``),
not only the app bundle name. Override with ``IB_UI_PROCESS_NAME`` from Activity Monitor
if detection fails. If autofill never works (common with embedded web UIs), leave
``IB_AUTOFILL_CREDENTIALS`` off and log in by hand; the script still waits for the API.
"""

import os
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from trading.config import IB_CLIENT_ID
from trading.config import IB_HOST
from trading.config import IB_PORT
from trading.market_data import connect

if TYPE_CHECKING:
    from ib_async import IB

DEFAULT_WAIT_SECONDS = 10
DEFAULT_MAX_WAIT_SECONDS = 240
DEFAULT_APP_START_WAIT_SECONDS = 6
DEFAULT_AUTOFILL_RETRIES = 3
DEFAULT_AUTOFILL_RETRY_WAIT_SECONDS = 2
DEFAULT_APP_READY_TIMEOUT_SECONDS = 11
DEFAULT_APP_READY_POLL_SECONDS = 0.5
DEFAULT_LOGIN_WINDOW_TIMEOUT_SECONDS = 45
DEFAULT_LOGIN_WINDOW_POLL_SECONDS = 0.5
# Splash / updater windows can exist before the real two-field Login form. Require this
# many text-like fields (text fields + secure text fields) on a window named "Login".
MIN_LOGIN_TEXT_FIELDS_FOR_PRIMARY_SCREEN = 2
DEFAULT_USERNAME_TO_PASSWORD_WAIT_SECONDS = 1.5
DEFAULT_IB_APP_NAME = 'Trader Workstation'
DEFAULT_LOGIN_FLOW = 'tab_then_submit'
# Keystrokes before the username field is focused can hit tabs/links and push TWS into a
# different flow (single-field + Try Demo). Prefer clicking the username field first.
ENV_IB_LOGIN_FOCUS_FIRST_FIELD = 'IB_LOGIN_FOCUS_FIRST_FIELD'
ENV_IB_USERNAME = 'IB_USERNAME'
ENV_IB_PASSWORD = 'IB_PASSWORD'
ENV_IB_APP_NAME = 'IB_APP_NAME'
ENV_IB_AUTOFILL_CREDENTIALS = 'IB_AUTOFILL_CREDENTIALS'
ENV_IB_LOGIN_FLOW = 'IB_LOGIN_FLOW'
# System Events ``tell process`` name often differs from the app bundle name (e.g. ``tws``).
ENV_IB_UI_PROCESS_NAME = 'IB_UI_PROCESS_NAME'
ENV_IB_LOGIN_WINDOW_TITLE = 'IB_LOGIN_WINDOW_TITLE'
# If true, return early when autofill/readiness fails. Default: keep polling so manual login works.
ENV_IB_LOGIN_FAIL_ON_AUTOFILL_ERROR = 'IB_LOGIN_FAIL_ON_AUTOFILL_ERROR'

DEFAULT_API_SOCKET_PROBE_TIMEOUT_SECONDS = 0.35
DEFAULT_LOGIN_WINDOW_TITLE = 'Login'


@dataclass(frozen=True)
class LaunchStatus:
    """Result metadata for IB login attempts."""

    launched_app: bool
    attempted_credential_fill: bool
    connected: bool


def _safe_bool_from_env(var_name: str, default_value: bool) -> bool:
    """Parse booleans from environment using common truthy values."""
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return default_value
    normalized = raw_value.strip().lower()
    return normalized in {'1', 'true', 'yes', 'y', 'on'}


def _is_ib_process_running() -> bool:
    """Return True when TWS/IB Gateway process appears to be running."""
    result = subprocess.run(
        ['pgrep', '-f', 'Trader Workstation|tws|IB Gateway|ibgateway'],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _launch_ib_application(app_name: str) -> bool:
    """Launch IB GUI app by name with macOS open command."""
    result = subprocess.run(
        ['open', '-a', app_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr_msg = result.stderr.strip() if result.stderr else 'unknown error'
        print(f'Unable to launch IB app "{app_name}": {stderr_msg}')
        return False
    return True


def _is_target_app_process_running(app_name: str) -> bool:
    """Return True when the specific app process appears to be running.

    TWS often spawns a Java child whose argv may not contain the full app name; use the
    same broad pattern as ``_is_ib_process_running`` when the bundle name is the default.
    """
    if app_name.strip() == DEFAULT_IB_APP_NAME:
        pattern = 'Trader Workstation|tws|IB Gateway|ibgateway'
    else:
        pattern = app_name
    result = subprocess.run(
        ['pgrep', '-f', pattern],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _wait_for_app_ready(
    app_name: str,
    timeout_seconds: float = DEFAULT_APP_READY_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_APP_READY_POLL_SECONDS,
) -> bool:
    """Wait until the target app process exists or timeout expires."""
    elapsed_seconds = 0.0
    last_progress_at = -1.0
    while elapsed_seconds <= timeout_seconds:
        if _is_target_app_process_running(app_name):
            return True
        time.sleep(poll_seconds)
        elapsed_seconds += poll_seconds
        if elapsed_seconds - last_progress_at >= 2.0:
            last_progress_at = elapsed_seconds
            print(
                f'Waiting for {app_name} process... '
                f'{elapsed_seconds:.0f}s / {timeout_seconds:.0f}s'
            )
    return False


def _escape_applescript_text(raw_text: str) -> str:
    """Escape a string so it can be safely wrapped in AppleScript quotes."""
    return raw_text.replace('\\', '\\\\').replace('"', '\\"')


def _ui_process_names(app_bundle_name: str) -> list[str]:
    """Names to try with ``tell process`` in System Events (often not the bundle name)."""
    override = os.getenv(ENV_IB_UI_PROCESS_NAME, '').strip()
    if override:
        return [override]
    names = [app_bundle_name.strip()]
    if app_bundle_name.strip() == DEFAULT_IB_APP_NAME:
        names.extend(['tws', 'TWS'])
    return list(dict.fromkeys(names))


def _is_primary_login_form_ready(
    ui_process_name: str,
    window_title: str,
) -> bool:
    """Return True when the real Login form is up (not splash / single-field branch).

    TWS often shows a brief window before the two-field screen. Any-window checks and
    ``text field 1 of window 1`` then target the wrong UI, so credentials go to the
    alternate single-field flow.
    """
    proc_escaped = _escape_applescript_text(ui_process_name)
    title_escaped = _escape_applescript_text(window_title)
    min_fields = MIN_LOGIN_TEXT_FIELDS_FOR_PRIMARY_SCREEN
    script = f"""
tell application "System Events"
    if not (exists process "{proc_escaped}") then
        return "false"
    end if
    tell process "{proc_escaped}"
        repeat with w in windows
            try
                if (name of w) is "{title_escaped}" then
                    set nt to count of (every text field of w)
                    set ns to count of (every secure text field of w)
                    if (nt + ns) >= {min_fields} then
                        return "true"
                    end if
                end if
            end try
        end repeat
    end tell
end tell
return "false"
"""
    result = subprocess.run(
        ['osascript', '-e', script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip().lower() == 'true'


def _probe_primary_login_ui_process(
    app_bundle_name: str,
    window_title: str,
) -> str | None:
    """Return the System Events process name that owns the primary login form, if any."""
    for proc in _ui_process_names(app_bundle_name):
        if _is_primary_login_form_ready(proc, window_title):
            return proc
    return None


def _wait_for_login_window(
    app_bundle_name: str,
    window_title: str,
    timeout_seconds: float = DEFAULT_LOGIN_WINDOW_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_LOGIN_WINDOW_POLL_SECONDS,
) -> str | None:
    """Wait until the two-field Login window is ready; return UI process name or None."""
    elapsed_seconds = 0.0
    last_progress_at = -1.0
    while elapsed_seconds <= timeout_seconds:
        found = _probe_primary_login_ui_process(app_bundle_name, window_title)
        if found is not None:
            print(f'Login form detected (UI process name: {found}).')
            return found
        time.sleep(poll_seconds)
        elapsed_seconds += poll_seconds
        if elapsed_seconds - last_progress_at >= 2.0:
            last_progress_at = elapsed_seconds
            tried = ', '.join(_ui_process_names(app_bundle_name))
            print(
                f'Waiting for two-field Login window... '
                f'{elapsed_seconds:.0f}s / {timeout_seconds:.0f}s '
                f'(trying UI process names: {tried})'
            )
    return None


def _applescript_activate_focus_first_text_field_clear(
    app_bundle_name_escaped: str,
    ui_process_name_escaped: str,
    window_title_escaped: str,
) -> str:
    """Activate bundle, click username field on primary Login window only, clear, then type."""
    min_fields = MIN_LOGIN_TEXT_FIELDS_FOR_PRIMARY_SCREEN
    return f"""
tell application "{app_bundle_name_escaped}"
    activate
end tell
delay 0.5
tell application "System Events"
    tell process "{ui_process_name_escaped}"
        set frontmost to true
        delay 0.3
        set targetWin to missing value
        repeat with w in windows
            try
                if (name of w) is "{window_title_escaped}" then
                    set nt to count of (every text field of w)
                    set ns to count of (every secure text field of w)
                    if (nt + ns) >= {min_fields} then
                        set targetWin to w
                        exit repeat
                    end if
                end if
            end try
        end repeat
        if targetWin is missing value then
            error "primary_login_form_not_found"
        end if
        click text field 1 of targetWin
    end tell
    delay 0.25
    keystroke "a" using command down
    key code 51
    delay 0.1
end tell
"""


def _fill_login_form_with_env_credentials(
    app_bundle_name: str,
    username: str,
    password: str,
    login_flow: str = DEFAULT_LOGIN_FLOW,
    focus_first_text_field: bool = True,
    ui_process_name: str | None = None,
    window_title: str = DEFAULT_LOGIN_WINDOW_TITLE,
) -> bool:
    """Best-effort UI automation for TWS login form.

    This activates the app bundle, then uses System Events with ``ui_process_name`` (often
    ``tws``) to click the username field. It does not bypass 2FA.

    For the standard two-field Login window, we click the first text field (when enabled),
    clear it, then type username, Tab, password, Return. That avoids stray keystrokes
    hitting Live/Paper tabs or links, which can navigate to IB's alternate single-field
    screen (Try Demo).

    ``two_step_submit`` sends Return after username only; IB may treat that as advancing
    a different login path. Prefer ``tab_then_submit`` unless you know you need two-step.
    """
    bundle_escaped = _escape_applescript_text(app_bundle_name)
    username_escaped = _escape_applescript_text(username)
    password_escaped = _escape_applescript_text(password)
    title_escaped = _escape_applescript_text(window_title)
    login_flow_normalized = login_flow.strip().lower()
    if login_flow_normalized not in {'tab_then_submit', 'two_step_submit'}:
        login_flow_normalized = DEFAULT_LOGIN_FLOW

    if focus_first_text_field:
        proc = ui_process_name if ui_process_name is not None else app_bundle_name
        proc_escaped = _escape_applescript_text(proc)
        prefix = _applescript_activate_focus_first_text_field_clear(
            bundle_escaped,
            proc_escaped,
            title_escaped,
        )
    else:
        prefix = f"""
tell application "{bundle_escaped}"
    activate
end tell
delay 0.3
"""

    if login_flow_normalized == 'two_step_submit':
        script = f"""
{prefix}
tell application "System Events"
    keystroke "{username_escaped}"
    key code 36
end tell
delay {DEFAULT_USERNAME_TO_PASSWORD_WAIT_SECONDS}
tell application "System Events"
    keystroke "{password_escaped}"
    key code 36
end tell
"""
    else:
        script = f"""
{prefix}
tell application "System Events"
    keystroke "{username_escaped}"
    key code 48
    keystroke "{password_escaped}"
    key code 36
end tell
"""
    result = subprocess.run(
        ['osascript', '-e', script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr_msg = result.stderr.strip() if result.stderr else 'unknown error'
        print(f'Credential autofill failed: {stderr_msg}')
        return False
    return True


def _live_api_port_accepts_tcp(
    host: str = IB_HOST,
    port: int = IB_PORT,
    timeout_seconds: float = DEFAULT_API_SOCKET_PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Return True if something is listening on the IB API port (silent probe).

    Until TWS is logged in and API sockets are enabled, the port is closed.
    Skipping ``ib_async`` connect in that case avoids noisy 'API connection failed'
    logs on every poll; those messages are expected, not errors, during startup.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _try_connect_live(client_id: int) -> 'IB | None':
    """Try a single live IB API connection attempt."""
    if not _live_api_port_accepts_tcp():
        return None
    return connect(
        host=IB_HOST,
        port=IB_PORT,
        client_id=client_id,
        readonly=False,
    )


def ensure_live_ib_connection(
    wait_seconds: int = DEFAULT_WAIT_SECONDS,
    max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
    client_id: int = IB_CLIENT_ID,
    app_start_wait_seconds: int = DEFAULT_APP_START_WAIT_SECONDS,
) -> tuple['IB | None', LaunchStatus]:
    """Ensure IB live session is available, launching login flow when needed.

    Login behavior notes:
    - IB API itself cannot perform authentication directly; TWS/Gateway must be running.
    - 2FA approval is still required on your mobile device for live sessions.
    - Polling defaults to 10 seconds between attempts to allow manual 2FA completion.
    - Until the API port accepts connections, probes stay quiet; refusal is normal pre-login.
    """
    load_dotenv()

    ib_direct = _try_connect_live(client_id=client_id)
    if ib_direct is not None and ib_direct.isConnected():
        return ib_direct, LaunchStatus(False, False, True)

    app_name = os.getenv(ENV_IB_APP_NAME, DEFAULT_IB_APP_NAME)
    login_window_title = os.getenv(ENV_IB_LOGIN_WINDOW_TITLE, DEFAULT_LOGIN_WINDOW_TITLE).strip()
    if not login_window_title:
        login_window_title = DEFAULT_LOGIN_WINDOW_TITLE
    should_autofill = _safe_bool_from_env(ENV_IB_AUTOFILL_CREDENTIALS, False)
    login_flow = os.getenv(ENV_IB_LOGIN_FLOW, DEFAULT_LOGIN_FLOW)
    focus_login_username_field = _safe_bool_from_env(ENV_IB_LOGIN_FOCUS_FIRST_FIELD, True)
    fail_on_autofill_error = _safe_bool_from_env(ENV_IB_LOGIN_FAIL_ON_AUTOFILL_ERROR, False)

    launched_app = False
    if not _is_ib_process_running():
        launched_app = _launch_ib_application(app_name=app_name)
        if launched_app:
            print(f'Launched {app_name}; waiting for login screen...')
            app_ready = _wait_for_app_ready(app_name=app_name)
            if not app_ready:
                print(f'{app_name} process did not become ready in time.')
            print(f'Pausing {app_start_wait_seconds}s for login UI to load...')
            time.sleep(app_start_wait_seconds)

    attempted_fill = False
    successful_fill = False
    username = os.getenv(ENV_IB_USERNAME, '').strip()
    password = os.getenv(ENV_IB_PASSWORD, '').strip()
    if should_autofill and username and password:
        ui_process = _wait_for_login_window(
            app_bundle_name=app_name,
            window_title=login_window_title,
        )
        if ui_process is None:
            print(
                f'Two-field Login form not detected (window "{login_window_title}", '
                f'{MIN_LOGIN_TEXT_FIELDS_FOR_PRIMARY_SCREEN}+ text/secure fields). '
                f'Set {ENV_IB_UI_PROCESS_NAME} to the exact name from Activity Monitor '
                f'if autofill should target a different process than "{app_name}". '
                'You can still log in manually; this script will wait for the API port.'
            )
            if fail_on_autofill_error:
                return None, LaunchStatus(launched_app, attempted_fill, False)
        else:
            for autofill_attempt in range(1, DEFAULT_AUTOFILL_RETRIES + 1):
                attempted_fill = True
                did_fill = _fill_login_form_with_env_credentials(
                    app_bundle_name=app_name,
                    username=username,
                    password=password,
                    login_flow=login_flow,
                    focus_first_text_field=focus_login_username_field,
                    ui_process_name=ui_process,
                    window_title=login_window_title,
                )
                if did_fill:
                    successful_fill = True
                    print('Submitted IB credentials from .env (2FA still required).')
                    break
                if autofill_attempt < DEFAULT_AUTOFILL_RETRIES:
                    print(
                        'Credential autofill attempt '
                        f'{autofill_attempt}/{DEFAULT_AUTOFILL_RETRIES} failed; retrying in '
                        f'{DEFAULT_AUTOFILL_RETRY_WAIT_SECONDS}s.'
                    )
                    time.sleep(DEFAULT_AUTOFILL_RETRY_WAIT_SECONDS)
    elif should_autofill:
        print('IB credentials missing in environment; skipping credential autofill.')
    else:
        print('Credential autofill disabled (set IB_AUTOFILL_CREDENTIALS=true to enable).')

    if should_autofill and attempted_fill and not successful_fill:
        print(
            'Credential autofill did not complete. '
            'Log in manually in TWS if needed; waiting for API connection.'
        )
        if fail_on_autofill_error:
            return None, LaunchStatus(launched_app, attempted_fill, False)

    elapsed_seconds = 0
    while elapsed_seconds <= max_wait_seconds:
        ib_polled = _try_connect_live(client_id=client_id)
        if ib_polled is not None and ib_polled.isConnected():
            return ib_polled, LaunchStatus(launched_app, attempted_fill, True)

        print(
            'Waiting for IB login/2FA completion... '
            f'retrying in {wait_seconds}s ({elapsed_seconds}/{max_wait_seconds}s elapsed).'
        )
        time.sleep(wait_seconds)
        elapsed_seconds += wait_seconds

    return None, LaunchStatus(launched_app, attempted_fill, False)


def _main() -> int:
    """CLI entry point for manual IB login bootstrap."""
    ib, status = ensure_live_ib_connection()
    if ib is None or not status.connected:
        print('Failed to establish live IB connection within timeout.')
        return 1

    print('Live IB connection established and ready.')
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
