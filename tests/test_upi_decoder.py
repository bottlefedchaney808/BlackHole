"""Tests for upi_decoder.py."""
import pytest

from upi_decoder import decode_local, split_ric


class TestDecodeLocal:
    def test_extracts_first_segment_before_backslash(self):
        assert decode_local("LIVZON GROUP\\REGISTERED\\SHARES A\\000513") == "LIVZON GROUP"

    def test_extracts_name_with_punctuation(self):
        assert decode_local("CAPITAL SECURITIES CO.,LTD\\BEARER\\SHARES A\\601136") == "CAPITAL SECURITIES CO.,LTD"

    def test_single_segment_with_no_backslash_is_returned_as_is(self):
        assert decode_local("ACME CORP") == "ACME CORP"

    @pytest.mark.parametrize("placeholder", [
        "COM STK", "TWSE LISTED STOCKS", "No name obtainable",
        "N/A", "NA", "None", "com stk",
    ])
    def test_known_placeholders_return_none(self, placeholder):
        assert decode_local(placeholder) is None

    def test_blank_returns_none(self):
        assert decode_local("") is None

    def test_none_returns_none(self):
        assert decode_local(None) is None

    @pytest.mark.parametrize("descriptor", [
        "COMMON SHARES",
        "SHS",
        "EMERGING STOCKS",
        "TPEX LISTED STOCKS",
        "REGISTERED SHARES - CLASS C PREFERENCIALS",
        "PREFERRED SHARES CLASS A",
        "REGISTERED SHARES - ORDINARY - COMMON SHARES",
    ])
    def test_generic_share_class_descriptors_return_none(self, descriptor):
        # These are real values observed in the live feed's single-segment
        # (no-backslash) upi_underlier_name rows -- instrument-type
        # descriptors, not company names.
        assert decode_local(descriptor) is None

    @pytest.mark.parametrize("real_name", [
        "WOOSHIN SYSTEMS", "DHP KOREA.CO., Ltd", "BUMHAN FUEL CELL",
        "YG PLUS", "FADU", "Samryoong",
    ])
    def test_real_company_names_without_backslash_are_preserved(self, real_name):
        # None of these contain SHARES/STOCKS/LISTED, so the generic-descriptor
        # filter must not swallow them.
        assert decode_local(real_name) == real_name


class TestSplitRic:
    def test_splits_ticker_and_suffix(self):
        assert split_ric("002028.ZK") == ("002028", "ZK")

    def test_splits_ticker_with_dot_suffix_lowercased_input(self):
        assert split_ric("6902.t") == ("6902", "T")

    def test_no_dot_returns_none(self):
        assert split_ric("ACMECORP") is None

    def test_blank_returns_none(self):
        assert split_ric("") is None


from unittest.mock import MagicMock, patch

from upi_decoder import DecodeResult, OpenFigiClient, decode_one


class TestOpenFigiClient:
    def test_lookup_ticker_batch_parses_matched_response(self):
        client = OpenFigiClient(session=MagicMock())
        client.session.post.return_value.raise_for_status = lambda: None
        client.session.post.return_value.json.return_value = [
            {"data": [{"name": "SIEYUAN ELECTRIC CO LTD", "figi": "BBG000ABCXYZ"}]},
        ]
        names = client.lookup_ticker_batch([("002028", "SHE")])
        assert names == ["SIEYUAN ELECTRIC CO LTD"]

    def test_lookup_ticker_batch_handles_unmatched_entry(self):
        client = OpenFigiClient(session=MagicMock())
        client.session.post.return_value.raise_for_status = lambda: None
        client.session.post.return_value.json.return_value = [
            {"error": "No identifier found."},
        ]
        names = client.lookup_ticker_batch([("BADTICKER", "SHE")])
        assert names == [None]

    def test_lookup_ticker_batch_handles_request_exception(self):
        import requests
        client = OpenFigiClient(session=MagicMock())
        client.session.post.side_effect = requests.RequestException("boom")
        names = client.lookup_ticker_batch([("002028", "SHE")])
        assert names == [None]

    def test_lookup_ticker_batch_empty_input_returns_empty(self):
        client = OpenFigiClient(session=MagicMock())
        assert client.lookup_ticker_batch([]) == []

    def test_api_key_added_to_headers_when_set(self):
        client = OpenFigiClient(api_key="secret-key")
        assert client._headers()["X-OPENFIGI-APIKEY"] == "secret-key"

    def test_no_api_key_header_omitted(self):
        client = OpenFigiClient()
        assert "X-OPENFIGI-APIKEY" not in client._headers()


class TestDecodeOne:
    def test_prefers_tier1_local_name_without_calling_openfigi(self):
        figi_client = MagicMock()
        result = decode_one("LIVZON GROUP\\REGISTERED\\SHARES A\\000513", "000513.ZK", "RIC", figi_client=figi_client)
        assert result == DecodeResult("LIVZON GROUP", "local", "upi_underlier_name")
        figi_client.lookup_ticker_batch.assert_not_called()

    def test_falls_back_to_openfigi_when_local_is_placeholder(self):
        figi_client = MagicMock()
        figi_client.lookup_ticker_batch.return_value = ["SIEYUAN ELECTRIC CO LTD"]
        result = decode_one("COM STK", "002028.ZK", "RIC", figi_client=figi_client)
        assert result.company_name == "SIEYUAN ELECTRIC CO LTD"
        assert result.tier == "openfigi"
        figi_client.lookup_ticker_batch.assert_called_once_with([("002028", "XSHE")])

    def test_unresolved_when_ric_suffix_unmapped(self):
        figi_client = MagicMock()
        result = decode_one("COM STK", "1234.XX", "RIC", figi_client=figi_client)
        assert result == DecodeResult(None, "unresolved", "no local name, no OpenFIGI mapping")
        figi_client.lookup_ticker_batch.assert_not_called()

    def test_unresolved_when_source_is_not_ric(self):
        figi_client = MagicMock()
        result = decode_one("COM STK", "US0378331005", "ISIN", figi_client=figi_client)
        assert result.tier == "unresolved"
        figi_client.lookup_ticker_batch.assert_not_called()

    def test_unresolved_when_no_figi_client_given(self):
        result = decode_one("COM STK", "002028.ZK", "RIC", figi_client=None)
        assert result.tier == "unresolved"

    def test_unresolved_when_openfigi_returns_no_match(self):
        figi_client = MagicMock()
        figi_client.lookup_ticker_batch.return_value = [None]
        result = decode_one("COM STK", "002028.ZK", "RIC", figi_client=figi_client)
        assert result.tier == "unresolved"
