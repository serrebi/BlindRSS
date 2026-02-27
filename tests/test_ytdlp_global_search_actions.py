import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui.dialogs as dialogs


class _Host:
    _get_selected_action_availability = dialogs.YtdlpGlobalSearchDialog._get_selected_action_availability

    def __init__(self, item):
        self._item = item

    def _get_selected_result(self):
        return self._item


def test_selected_action_availability_when_result_has_play_and_subscribe_targets():
    host = _Host(
        {
            "url": "https://example.com/watch/1",
            "native_subscribe_url": "https://example.com/feed.xml",
            "source_subscribe_url": "",
        }
    )

    assert host._get_selected_action_availability() == (True, True, True)


def test_selected_action_availability_when_nothing_is_selected():
    host = _Host(None)
    assert host._get_selected_action_availability() == (False, False, False)
