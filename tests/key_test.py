"""
Test pentru crearea unui userDataStream (listenKey) la Binance.
Rulează cu mock (fără rețea/chei) → determinist. Pentru un smoke REAL pe API,
rulează direct: `python tests/key_test.py` (vezi blocul __main__).
"""
import unittest
from unittest.mock import patch, MagicMock

import requests


# ─── funcție testabilă ────────────────────────────────────────────────────────
def create_user_data_stream(api_key):
    """POST /api/v3/userDataStream cu header-ul X-MBX-APIKEY → întoarce JSON-ul."""
    response = requests.post(
        "https://api.binance.com/api/v3/userDataStream",
        headers={"X-MBX-APIKEY": api_key},
    )
    return response.json()


class TestUserDataStream(unittest.TestCase):
    @patch("requests.post")
    def test_returns_listen_key(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {"listenKey": "abc123"})
        out = create_user_data_stream("MY_KEY")
        self.assertEqual(out["listenKey"], "abc123")

    @patch("requests.post")
    def test_sends_api_key_header(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {})
        create_user_data_stream("MY_KEY")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["X-MBX-APIKEY"], "MY_KEY")
        self.assertIn("userDataStream", mock_post.call_args[0][0])


if __name__ == "__main__":
    # smoke REAL pe API (necesită cheie/rețea)
    from keys.apikeys import api_key
    print(create_user_data_stream(api_key))
