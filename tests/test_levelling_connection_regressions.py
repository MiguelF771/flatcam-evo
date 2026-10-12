"""S3 connection regressions. RED tests and passing compatibility are separate."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_levelling_audit import FakeGrbl, make_audit_tool, run_worker, tl_module


class ConnectionPort(FakeGrbl):
    def __init__(self, replies=(), fail_dtr=False):
        super().__init__()
        self.handshake = list(replies)
        self.fail_dtr = fail_dtr
        self.read_hook = None
        self.read_count = 0
        self.reset_count = 0
        self.close_count = 0

    @property
    def dtr(self):
        return True

    @dtr.setter
    def dtr(self, value):
        if self.fail_dtr:
            raise OSError('DTR unavailable')

    def readlines(self):
        self.read_count += 1
        if self.read_hook is not None:
            self.read_hook()
        return list(self.handshake)

    def reset_input_buffer(self):
        self.reset_count += 1
        super().reset_input_buffer()

    def close(self):
        self.close_count += 1
        super().close()


def connect(tool, port, callbacks):
    tool.ui.com_list_combo.currentText.return_value = 'COM3'
    tool.ui.baudrates_list_combo.currentText.return_value = '115200'
    with patch.object(tl_module.serial, 'serial_for_url', return_value=port) as opened, \
            patch.object(tl_module.QtCore.QTimer, 'singleShot',
                         side_effect=lambda delay, callback: callbacks.append(callback)):
        tool.on_grbl_connect()
    return opened


class TestConnectionRed:
    def test_stale_callback_cannot_touch_replacement(self):
        tool = make_audit_tool()
        callbacks = []
        first = ConnectionPort([b'ok\r\n'])
        second = ConnectionPort([b'ok\r\n'])
        connect(tool, first, callbacks)
        tool._grbl_disconnect()
        connect(tool, second, callbacks)
        before = (second.read_count, second.reset_count, second.close_count,
                  list(second.written), tool.app.inform.emit.call_count,
                  list(tool.ui.mock_calls))
        callbacks[0]()
        assert (second.read_count, second.reset_count, second.close_count,
                list(second.written), tool.app.inform.emit.call_count,
                list(tool.ui.mock_calls)) == before
        assert tool.grbl_ser_port is second

    def test_connecting_refuses_probe_and_motion(self):
        tool = make_audit_tool()
        port = ConnectionPort()
        connect(tool, port, [])
        ready = tool._grbl_port_ready()
        probe = tool._grbl_reserve('probe', motion=True)
        move = tool._grbl_reserve('move', motion=True)
        assert (ready, probe, move) == (False, None, None)
        assert tool._grbl_op is None
        assert port.written == [b'\r\n\r\n']

    def test_failed_dtr_and_ok_do_not_prove_reset(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        callbacks = []
        connect(tool, ConnectionPort([b'ok\r\n'], fail_dtr=True), callbacks)
        callbacks[0]()
        assert tool._grbl_port_ready()
        assert tool._grbl_recovery == 'needs_reset'

    def test_successful_dtr_and_ok_do_not_prove_reset(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        callbacks = []
        connect(tool, ConnectionPort([b'ok\r\n']), callbacks)
        callbacks[0]()
        assert tool._grbl_recovery == 'needs_reset'

    def test_handshake_alarm_latches_recovery_even_after_ok(self):
        tool = make_audit_tool()
        callbacks = []
        connect(tool, ConnectionPort([b'ok\r\n', b'ALARM:4\r\n']), callbacks)
        callbacks[0]()
        assert tool._grbl_recovery is not None
        assert tool._grbl_reserve('probe', motion=True) is None

    def test_open_configures_finite_write_timeout(self):
        opened = connect(make_audit_tool(), ConnectionPort(), [])
        assert opened.call_args.kwargs['write_timeout'] == 1.0
        assert 0 < opened.call_args.kwargs['timeout'] <= 1.0

    def test_replacement_clears_connection_buffers(self):
        tool = make_audit_tool()
        old = ConnectionPort()
        tool._grbl_rx_port = old
        tool._grbl_rx_buffer = b'old partial reply'
        tool._grbl_reply_pending = True
        connect(tool, ConnectionPort(), [])
        assert tool._grbl_rx_port is not old
        assert tool._grbl_rx_buffer == b''
        assert not tool._grbl_reply_pending

    def test_attempt_identity_rechecked_after_read(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        callbacks = []
        first = ConnectionPort([b"Grbl 1.1h ['$' for help]\r\n"])
        second = ConnectionPort()
        connect(tool, first, callbacks)

        def replace():
            tool._grbl_disconnect()
            connect(tool, second, callbacks)
            tool.app.inform.emit.reset_mock()
            tool._grbl_set_ui_connected.reset_mock()

        tool._grbl_set_ui_connected = MagicMock()
        first.read_hook = replace
        callbacks[0]()
        assert tool.grbl_ser_port is second
        assert tool._grbl_recovery == 'needs_reset'
        tool._grbl_set_ui_connected.assert_not_called()
        tool.app.inform.emit.assert_not_called()
        assert second.read_count == 0
        assert second.close_count == 0

    def test_banner_cannot_override_alarm_in_same_chunk(self):
        tool = make_audit_tool()
        callbacks = []
        connect(tool, ConnectionPort([b"Grbl 1.1h ['$' for help]\r\nALARM:4\r\n"]), callbacks)
        callbacks[0]()
        assert tool._grbl_recovery == 'needs_reset'

    def test_handshake_does_not_flush_late_alarm(self):
        tool = make_audit_tool()
        port = ConnectionPort([b'ok\r\n'])
        callbacks = []
        connect(tool, port, callbacks)
        port.read_hook = lambda: port._send('ALARM:4')
        callbacks[0]()
        assert port.reset_count == 1
        op = tool._grbl_reserve('probe', motion=True)
        assert tool._grbl_transact(op, 'G0 X1')[1] != 'ok'
        assert tool._grbl_recovery == 'needs_reset'
        assert b'G0 X1\n' not in port.written

    def test_both_port_and_token_are_required(self):
        for changed in ('port', 'token'):
            tool = make_audit_tool()
            port = ConnectionPort([b'ok\r\n'])
            callbacks = []
            connect(tool, port, callbacks)
            if changed == 'port':
                tool.grbl_ser_port = ConnectionPort()
            else:
                tool._grbl_connect_attempt = object()
            tool.app.inform.emit.reset_mock()
            tool.ui.reset_mock()
            callbacks[0]()
            assert port.read_count == 0
            assert port.close_count == 0
            assert not tool._grbl_connected
            tool.app.inform.emit.assert_not_called()
            assert not tool.ui.mock_calls

    def test_close_invalidates_attempt_and_clears_receive_state(self):
        for close in ('_grbl_disconnect', '_grbl_force_close'):
            tool = make_audit_tool()
            port = ConnectionPort([b'ok\r\n'])
            callbacks = []
            connect(tool, port, callbacks)
            tool._grbl_rx_buffer = b'partial'
            tool._grbl_reply_pending = True
            getattr(tool, close)()
            assert not tool._grbl_connected
            assert tool._grbl_connect_attempt is None
            assert tool._grbl_rx_port is None
            assert tool._grbl_rx_buffer == b''
            assert not tool._grbl_reply_pending
            callbacks[0]()
            assert port.read_count == 0
            assert port.close_count == 1

    def test_handshake_preserves_receive_state_on_same_port(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([b'ok\r\n'])
        callbacks = []
        connect(tool, port, callbacks)
        tool._grbl_rx_buffer = b'[MSG:pending]\r\n'
        tool._grbl_reply_pending = True
        callbacks[0]()
        assert tool._grbl_port_ready()
        assert tool.grbl_ser_port is port
        assert tool._grbl_rx_port is port
        assert tool._grbl_rx_buffer == b'[MSG:pending]\r\n'
        assert tool._grbl_reply_pending
        assert tool._grbl_recovery == 'needs_reset'
        assert port.reset_count == 1
        assert port.close_count == 0

    def test_incomplete_prefix_plus_ok_is_not_handshake_ack(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([b'ok\r\n'])
        callbacks = []
        connect(tool, port, callbacks)
        tool._grbl_rx_buffer = b'partial'
        tool._grbl_reply_pending = True
        callbacks[0]()
        assert not tool._grbl_port_ready()
        assert tool.grbl_ser_port is None
        assert tool._grbl_recovery == 'needs_reset'
        assert port.close_count == 1


def run_home(tool):
    tool.on_grbl_homing()
    ticks = iter(range(10000))
    with patch('appPlugins.ToolLevelling.time.monotonic', side_effect=lambda: next(ticks)), \
            patch('appPlugins.ToolLevelling.time.sleep'):
        run_worker(tool)


class TestFragmentedHandshake:
    BANNER = b"Grbl 1.1h ['$' for help]\r\n"

    def test_fragmented_alarm_after_banner_blocks_home(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([self.BANNER, b'AL'])
        callbacks = []
        connect(tool, port, callbacks)
        callbacks[0]()
        assert tool._grbl_rx_buffer == b'AL'
        assert tool._grbl_rx_port is port
        port.handshake = []
        port.rx.append(b'ARM:9\r\n')
        run_home(tool)
        assert '$H' not in port.commands()
        assert tool._grbl_recovery == 'needs_reset'

    def test_complete_banner_without_suffix_still_homes(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([self.BANNER])
        callbacks = []
        connect(tool, port, callbacks)
        callbacks[0]()
        assert tool._grbl_recovery == 'needs_home'
        assert tool._grbl_rx_buffer == b''
        port.handshake = []
        run_home(tool)
        assert '$H' in port.commands()
        assert tool._grbl_recovery is None


class TestConnectionCompatibility:
    def test_second_connect_click_cancels_pending_attempt(self):
        tool = make_audit_tool()
        port = ConnectionPort([b'ok\r\n'])
        callbacks = []
        connect(tool, port, callbacks)
        with patch.object(tl_module.serial, 'serial_for_url') as opened:
            tool.on_grbl_connect()
        opened.assert_not_called()
        assert tool.grbl_ser_port is None
        assert port.close_count == 1
        tool.app.inform.emit.reset_mock()
        callbacks[0]()
        assert port.read_count == 0
        tool.app.inform.emit.assert_not_called()

    def test_plain_ok_connects_without_recovery(self):
        tool = make_audit_tool()
        callbacks = []
        connect(tool, ConnectionPort([b'ok\r\n']), callbacks)
        callbacks[0]()
        assert tool._grbl_port_ready()
        assert tool._grbl_recovery is None

    def test_startup_banner_promotes_reset_only_to_home(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        callbacks = []
        connect(tool, ConnectionPort([b"Grbl 1.1h ['$' for help]\r\n"], fail_dtr=True), callbacks)
        callbacks[0]()
        assert tool._grbl_port_ready()
        assert tool._grbl_recovery == 'needs_home'
        assert tool._grbl_reserve('probe', motion=True) is None

    def test_missing_handshake_closes_port(self):
        tool = make_audit_tool()
        port = ConnectionPort()
        callbacks = []
        connect(tool, port, callbacks)
        callbacks[0]()
        assert tool.grbl_ser_port is None
        assert port.close_count == 1
