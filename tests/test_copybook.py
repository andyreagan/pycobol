"""Tests for the copybook parser and record encoder/decoder."""

import pytest
from pobol.copybook import parse_copybook, _expand_pic


# ---------------------------------------------------------------------------
# PIC parsing
# ---------------------------------------------------------------------------


class TestExpandPic:
    def test_alpha_parens(self):
        assert _expand_pic("X(20)") == ("alpha", 20, 0)

    def test_alpha_shorthand(self):
        assert _expand_pic("XXX") == ("alpha", 3, 0)

    def test_unsigned_parens(self):
        assert _expand_pic("9(5)") == ("unsigned", 5, 0)

    def test_unsigned_shorthand(self):
        assert _expand_pic("999") == ("unsigned", 3, 0)

    def test_signed(self):
        assert _expand_pic("S9(5)") == ("signed", 5, 0)

    def test_decimal(self):
        assert _expand_pic("9(7)V9(2)") == ("unsigned", 9, 2)

    def test_signed_decimal(self):
        assert _expand_pic("S9(5)V9(2)") == ("signed", 7, 2)

    def test_shorthand_decimal(self):
        assert _expand_pic("999V99") == ("unsigned", 5, 2)


# ---------------------------------------------------------------------------
# Copybook parsing from COBOL source
# ---------------------------------------------------------------------------

SAMPLE_FD = """\
       FD  INPUT-FILE.
       01  INPUT-RECORD.
           05  IN-CUST-ID     PIC 9(6).
           05  IN-CUST-NAME   PIC X(30).
           05  IN-BALANCE     PIC 9(7)V9(2).
"""


class TestParseCopybook:
    def test_parses_fields(self):
        cb = parse_copybook(SAMPLE_FD, name="INPUT")
        assert cb.name == "INPUT"
        assert len(cb.fields) == 3
        assert cb.fields[0].name == "IN-CUST-ID"
        assert cb.fields[0].kind == "unsigned"
        assert cb.fields[0].length == 6
        assert cb.fields[0].offset == 0

    def test_offsets(self):
        cb = parse_copybook(SAMPLE_FD)
        assert cb.fields[1].offset == 6  # after 6-digit ID
        assert cb.fields[2].offset == 36  # after 6 + 30

    def test_record_length(self):
        cb = parse_copybook(SAMPLE_FD)
        assert cb.record_length == 45  # 6 + 30 + 9

    def test_skips_group_items(self):
        """The 01-level group item should NOT appear as a field."""
        cb = parse_copybook(SAMPLE_FD)
        names = [f.name for f in cb.fields]
        assert "INPUT-RECORD" not in names


# ---------------------------------------------------------------------------
# Encode / Decode
# ---------------------------------------------------------------------------


class TestEncodeDecode:
    @pytest.fixture
    def cb(self):
        return parse_copybook(SAMPLE_FD, name="INPUT")

    def test_encode_alpha(self, cb):
        f = cb.fields[1]  # IN-CUST-NAME PIC X(30)
        assert f.encode("Alice") == b"Alice" + b" " * 25

    def test_encode_unsigned_int(self, cb):
        f = cb.fields[0]  # IN-CUST-ID PIC 9(6)
        assert f.encode(42) == b"000042"

    def test_encode_decimal(self, cb):
        f = cb.fields[2]  # IN-BALANCE PIC 9(7)V9(2)
        # 1500.00 * 10^2 = 150000 → zero-padded to 9 digits = "000150000"
        assert f.encode(1500.00) == b"000150000"

    def test_roundtrip(self, cb):
        record = {"IN-CUST-ID": 1, "IN-CUST-NAME": "Alice", "IN-BALANCE": 1500.0}
        encoded = cb.encode(record)
        assert len(encoded) == 45
        decoded = cb.decode(encoded)
        assert decoded["in_cust_id"] == 1
        assert decoded["in_cust_name"] == "Alice"
        assert decoded["in_balance"] == 1500.0

    def test_encode_many_decode_many(self, cb):
        records = [
            {"IN-CUST-ID": 1, "IN-CUST-NAME": "Alice", "IN-BALANCE": 1500.0},
            {"IN-CUST-ID": 2, "IN-CUST-NAME": "Bob", "IN-BALANCE": 50.0},
        ]
        data = cb.encode_many(records)
        decoded = cb.decode_many(data)
        assert len(decoded) == 2
        assert decoded[0]["in_cust_name"] == "Alice"
        assert decoded[1]["in_cust_id"] == 2

    def test_decode_short_record_pads(self, cb):
        """LINE SEQUENTIAL trims trailing spaces; decoder should handle it."""
        record = {"IN-CUST-ID": 1, "IN-CUST-NAME": "A", "IN-BALANCE": 0.0}
        encoded = cb.encode(record)
        # Simulate LINE SEQUENTIAL trimming
        trimmed = encoded.rstrip()
        decoded = cb.decode(trimmed)
        assert decoded["in_cust_id"] == 1
        assert decoded["in_cust_name"] == "A"


# ---------------------------------------------------------------------------
# Signed field encode/decode
# ---------------------------------------------------------------------------

SIGNED_FD = """\
       01  TRANSACTION-RECORD.
           05  TXN-ID         PIC 9(4).
           05  TXN-AMOUNT     PIC S9(5)V9(2).
"""


class TestSignedFields:
    @pytest.fixture
    def cb(self):
        return parse_copybook(SIGNED_FD)

    def test_positive_overpunch(self, cb):
        f = cb.fields[1]  # S9(5)V9(2) = 7 display digits
        encoded = f.encode(123.45)
        # 123.45 * 100 = 12345 → "0012345" with overpunch on last digit
        # 5 → positive overpunch 'E'
        assert encoded == b"001234E"

    def test_negative_overpunch(self, cb):
        f = cb.fields[1]
        encoded = f.encode(-123.45)
        # 5 → negative overpunch 'N'
        assert encoded == b"001234N"

    def test_roundtrip_positive(self, cb):
        record = {"TXN-ID": 1, "TXN-AMOUNT": 42.50}
        encoded = cb.encode(record)
        decoded = cb.decode(encoded)
        assert decoded["txn_amount"] == 42.50

    def test_roundtrip_negative(self, cb):
        record = {"TXN-ID": 1, "TXN-AMOUNT": -42.50}
        encoded = cb.encode(record)
        decoded = cb.decode(encoded)
        assert decoded["txn_amount"] == -42.50
