"""Unit tests for IB login launcher bootstrap flow."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from trading.ib_login_launcher import _main
from trading.ib_login_launcher import ensure_live_ib_connection


class TestIbLoginLauncher(unittest.TestCase):
    """Verify launch + polling behavior without real IB/TWS."""

    def test_returns_connected_immediately_when_live_session_already_available(self) -> None:
        """Skip launch path when initial connection succeeds."""
        ib_connected = MagicMock()
        ib_connected.isConnected.return_value = True

        with patch(
            'trading.ib_login_launcher._try_connect_live',
            return_value=ib_connected,
        ) as mock_try_connect_live, patch.dict(
            'os.environ',
            {'IB_AUTOFILL_CREDENTIALS': 'false'},
            clear=False,
        ):
            ib, status = ensure_live_ib_connection()

        self.assertIs(ib, ib_connected)
        self.assertTrue(status.connected)
        self.assertFalse(status.launched_app)
        self.assertFalse(status.attempted_credential_fill)
        self.assertEqual(mock_try_connect_live.call_count, 1)

    def test_launches_and_polls_until_connection_is_ready(self) -> None:
        """Launch app, submit credentials, then return once poll connects."""
        ib_connected = MagicMock()
        ib_connected.isConnected.return_value = True

        with patch(
            'trading.ib_login_launcher._try_connect_live',
            side_effect=[None, None, ib_connected],
        ) as mock_try_connect_live, patch(
            'trading.ib_login_launcher._is_ib_process_running',
            return_value=False,
        ), patch(
            'trading.ib_login_launcher._launch_ib_application',
            return_value=True,
        ) as mock_launch_ib_application, patch(
            'trading.ib_login_launcher._wait_for_app_ready',
            return_value=True,
        ), patch(
            'trading.ib_login_launcher._wait_for_login_window',
            return_value='tws',
        ), patch(
            'trading.ib_login_launcher._fill_login_form_with_env_credentials',
            return_value=True,
        ) as mock_fill_login, patch(
            'trading.ib_login_launcher.time.sleep',
        ) as mock_sleep, patch.dict(
            'os.environ',
            {
                'IB_USERNAME': 'user',
                'IB_PASSWORD': 'pass',
                'IB_AUTOFILL_CREDENTIALS': 'true',
            },
            clear=False,
        ):
            ib, status = ensure_live_ib_connection(wait_seconds=10, max_wait_seconds=20, client_id=123)

        self.assertIs(ib, ib_connected)
        self.assertTrue(status.connected)
        self.assertTrue(status.launched_app)
        self.assertTrue(status.attempted_credential_fill)
        mock_launch_ib_application.assert_called_once()
        mock_fill_login.assert_called_once_with(
            app_bundle_name='Trader Workstation',
            username='user',
            password='pass',
            login_flow='tab_then_submit',
            focus_first_text_field=True,
            ui_process_name='tws',
            window_title='Login',
        )
        self.assertEqual(mock_try_connect_live.call_count, 3)
        self.assertGreaterEqual(mock_sleep.call_count, 2)

    def test_polls_after_autofill_failure_by_default(self) -> None:
        """When autofill attempts fail, still poll for API (manual login possible)."""
        with patch(
            'trading.ib_login_launcher._try_connect_live',
            return_value=None,
        ) as mock_try_connect_live, patch(
            'trading.ib_login_launcher._is_ib_process_running',
            return_value=False,
        ), patch(
            'trading.ib_login_launcher._launch_ib_application',
            return_value=True,
        ), patch(
            'trading.ib_login_launcher._wait_for_app_ready',
            return_value=True,
        ), patch(
            'trading.ib_login_launcher._wait_for_login_window',
            return_value='tws',
        ), patch(
            'trading.ib_login_launcher._fill_login_form_with_env_credentials',
            return_value=False,
        ) as mock_fill_login, patch(
            'trading.ib_login_launcher.time.sleep',
        ), patch.dict(
            'os.environ',
            {
                'IB_USERNAME': 'user',
                'IB_PASSWORD': 'pass',
                'IB_AUTOFILL_CREDENTIALS': 'true',
            },
            clear=False,
        ):
            ib, status = ensure_live_ib_connection(wait_seconds=10, max_wait_seconds=20)

        self.assertIsNone(ib)
        self.assertFalse(status.connected)
        self.assertTrue(status.launched_app)
        self.assertTrue(status.attempted_credential_fill)
        self.assertEqual(mock_fill_login.call_count, 3)
        self.assertGreater(mock_try_connect_live.call_count, 1)

    def test_aborts_when_fail_on_autofill_error_and_form_missing(self) -> None:
        """Optional strict mode exits when login form is never detected."""
        with patch(
            'trading.ib_login_launcher._try_connect_live',
            return_value=None,
        ) as mock_try_connect_live, patch(
            'trading.ib_login_launcher._is_ib_process_running',
            return_value=True,
        ), patch(
            'trading.ib_login_launcher._wait_for_login_window',
            return_value=None,
        ), patch(
            'trading.ib_login_launcher.time.sleep',
        ), patch.dict(
            'os.environ',
            {
                'IB_USERNAME': 'user',
                'IB_PASSWORD': 'pass',
                'IB_AUTOFILL_CREDENTIALS': 'true',
                'IB_LOGIN_FAIL_ON_AUTOFILL_ERROR': 'true',
            },
            clear=False,
        ):
            ib, status = ensure_live_ib_connection(wait_seconds=10, max_wait_seconds=20)

        self.assertIsNone(ib)
        self.assertFalse(status.connected)
        self.assertEqual(mock_try_connect_live.call_count, 1)

    def test_times_out_when_connection_never_becomes_available(self) -> None:
        """Return disconnected status after max wait budget is exhausted."""
        with patch(
            'trading.ib_login_launcher._try_connect_live',
            return_value=None,
        ) as mock_try_connect_live, patch(
            'trading.ib_login_launcher._is_ib_process_running',
            return_value=True,
        ), patch(
            'trading.ib_login_launcher.time.sleep',
        ), patch.dict(
            'os.environ',
            {'IB_AUTOFILL_CREDENTIALS': 'false'},
            clear=False,
        ):
            ib, status = ensure_live_ib_connection(wait_seconds=10, max_wait_seconds=20)

        self.assertIsNone(ib)
        self.assertFalse(status.connected)
        self.assertFalse(status.launched_app)
        self.assertFalse(status.attempted_credential_fill)
        self.assertEqual(mock_try_connect_live.call_count, 4)

    def test_skips_autofill_when_env_flag_disabled(self) -> None:
        """Do not send credentials when autofill env toggle is false."""
        with patch(
            'trading.ib_login_launcher._try_connect_live',
            return_value=None,
        ), patch(
            'trading.ib_login_launcher._is_ib_process_running',
            return_value=True,
        ), patch(
            'trading.ib_login_launcher._fill_login_form_with_env_credentials',
            return_value=True,
        ) as mock_fill_login, patch(
            'trading.ib_login_launcher.time.sleep',
        ), patch.dict(
            'os.environ',
            {
                'IB_USERNAME': 'user',
                'IB_PASSWORD': 'pass',
                'IB_AUTOFILL_CREDENTIALS': 'false',
            },
            clear=False,
        ):
            ib, status = ensure_live_ib_connection(wait_seconds=1, max_wait_seconds=0)

        self.assertIsNone(ib)
        self.assertFalse(status.connected)
        self.assertFalse(status.attempted_credential_fill)
        mock_fill_login.assert_not_called()

    def test_main_returns_non_zero_when_not_connected(self) -> None:
        """CLI main reports failure when ensure_live_ib_connection fails."""
        disconnected_status = SimpleNamespace(connected=False)
        with patch(
            'trading.ib_login_launcher.ensure_live_ib_connection',
            return_value=(None, disconnected_status),
        ):
            exit_code = _main()

        self.assertEqual(exit_code, 1)


if __name__ == '__main__':
    unittest.main(buffer=False)
