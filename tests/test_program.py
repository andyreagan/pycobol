"""Integration tests: load and call COBOL programs from Python."""

import pytest
from pathlib import Path

from pobol import CobolProgram, load, parse_copybook
from pobol.exceptions import CobolRuntimeError


# ---------------------------------------------------------------------------
# hello.cob — pure stdout, no file I/O
# ---------------------------------------------------------------------------


class TestHelloProgram:
    def test_hello_stdout(self, examples_dir):
        hello = load(examples_dir / "hello.cob")
        result = hello()
        assert "HELLO FROM COBOL" in result.stdout

    def test_repr(self, examples_dir):
        hello = load(examples_dir / "hello.cob")
        assert "hello.cob" in repr(hello)


# ---------------------------------------------------------------------------
# add_numbers.cob — stdin/stdout
# ---------------------------------------------------------------------------


class TestAddNumbers:
    def test_add(self, examples_dir):
        add = load(examples_dir / "add_numbers.cob")
        # WS-NUM-A PIC 9(5) = "00100", WS-NUM-B PIC 9(5) = "00200"
        result = add(stdin="0010000200")
        assert result.stdout.strip() == "000300"

    def test_add_larger(self, examples_dir):
        add = load(examples_dir / "add_numbers.cob")
        result = add(stdin="1234500001")
        assert result.stdout.strip() == "012346"


# ---------------------------------------------------------------------------
# uppercase.cob — file I/O with simple records
# ---------------------------------------------------------------------------


INPUT_FD_UPPER = """\
       01  INPUT-RECORD.
           05  IN-ID          PIC 9(4).
           05  IN-NAME        PIC X(20).
"""

OUTPUT_FD_UPPER = """\
       01  OUTPUT-RECORD.
           05  OUT-ID         PIC 9(4).
           05  OUT-NAME       PIC X(20).
"""


class TestUppercase:
    @pytest.fixture
    def upper_prog(self, examples_dir):
        input_cb = parse_copybook(INPUT_FD_UPPER, name="INPUT-RECORD")
        output_cb = parse_copybook(OUTPUT_FD_UPPER, name="OUTPUT-RECORD")
        return load(
            examples_dir / "uppercase.cob",
            inputs={"INPUT-FILE": input_cb},
            outputs={"OUTPUT-FILE": output_cb},
        )

    def test_uppercase_single(self, upper_prog):
        result = upper_prog(input_file=[{"IN-ID": 1, "IN-NAME": "alice"}])
        assert len(result.outputs["OUTPUT-FILE"]) == 1
        rec = result.outputs["OUTPUT-FILE"][0]
        assert rec["out_name"] == "ALICE"
        assert rec["out_id"] == 1

    def test_uppercase_multiple(self, upper_prog):
        result = upper_prog(
            input_file=[
                {"IN-ID": 1, "IN-NAME": "alice"},
                {"IN-ID": 2, "IN-NAME": "bob jones"},
                {"IN-ID": 3, "IN-NAME": "Charlie"},
            ]
        )
        out = result.outputs["OUTPUT-FILE"]
        assert len(out) == 3
        assert out[0]["out_name"] == "ALICE"
        assert out[1]["out_name"] == "BOB JONES"
        assert out[2]["out_name"] == "CHARLIE"

    def test_attribute_access(self, upper_prog):
        """result.output_file should work as shorthand."""
        result = upper_prog(input_file=[{"IN-ID": 1, "IN-NAME": "test"}])
        assert result.output_file[0]["out_name"] == "TEST"


# ---------------------------------------------------------------------------
# customer_report.cob — file I/O with decimal arithmetic
# ---------------------------------------------------------------------------


INPUT_FD_CUST = """\
       01  INPUT-RECORD.
           05  IN-CUST-ID     PIC 9(6).
           05  IN-CUST-NAME   PIC X(30).
           05  IN-BALANCE     PIC 9(7)V9(2).
"""

OUTPUT_FD_CUST = """\
       01  OUTPUT-RECORD.
           05  OUT-CUST-ID    PIC 9(6).
           05  OUT-CUST-NAME  PIC X(30).
           05  OUT-BALANCE    PIC 9(7)V9(2).
           05  OUT-DISCOUNT   PIC 9(7)V9(2).
"""


class TestCustomerReport:
    @pytest.fixture
    def report_prog(self, examples_dir):
        input_cb = parse_copybook(INPUT_FD_CUST, name="INPUT-RECORD")
        output_cb = parse_copybook(OUTPUT_FD_CUST, name="OUTPUT-RECORD")
        return load(
            examples_dir / "customer_report.cob",
            inputs={"INPUT-FILE": input_cb},
            outputs={"OUTPUT-FILE": output_cb},
        )

    def test_discount_high_balance(self, report_prog):
        result = report_prog(
            input_file=[
                {"IN-CUST-ID": 1, "IN-CUST-NAME": "Alice", "IN-BALANCE": 1500.00},
            ]
        )
        out = result.output_file
        assert len(out) == 1
        assert out[0]["out_cust_name"] == "Alice"
        assert out[0]["out_balance"] == 1500.00
        assert out[0]["out_discount"] == 150.00

    def test_no_discount_low_balance(self, report_prog):
        result = report_prog(
            input_file=[
                {"IN-CUST-ID": 2, "IN-CUST-NAME": "Bob", "IN-BALANCE": 500.00},
            ]
        )
        out = result.output_file
        assert out[0]["out_discount"] == 0.0

    def test_multiple_customers(self, report_prog):
        result = report_prog(
            input_file=[
                {"IN-CUST-ID": 1, "IN-CUST-NAME": "Alice", "IN-BALANCE": 2000.00},
                {"IN-CUST-ID": 2, "IN-CUST-NAME": "Bob", "IN-BALANCE": 500.00},
                {"IN-CUST-ID": 3, "IN-CUST-NAME": "Charlie", "IN-BALANCE": 10000.00},
            ]
        )
        out = result.output_file
        assert len(out) == 3
        assert out[0]["out_discount"] == 200.00
        assert out[1]["out_discount"] == 0.0
        assert out[2]["out_discount"] == 1000.00

    def test_stdout_shows_count(self, report_prog):
        result = report_prog(
            input_file=[
                {"IN-CUST-ID": 1, "IN-CUST-NAME": "Test", "IN-BALANCE": 100.00},
            ]
        )
        assert "RECORDS PROCESSED: 000001" in result.stdout


# ---------------------------------------------------------------------------
# Auto-discovery: zero-config loading
# ---------------------------------------------------------------------------


class TestAutoDiscovery:
    def test_auto_uppercase(self, examples_dir):
        """Load uppercase.cob with zero manual copybook definitions."""
        prog = load(examples_dir / "uppercase.cob")
        assert "INPUT-FILE" in prog.inputs
        assert "OUTPUT-FILE" in prog.outputs

        result = prog(
            input_file=[
                {"IN-ID": 1, "IN-NAME": "hello world"},
                {"IN-ID": 2, "IN-NAME": "testing auto"},
            ]
        )
        out = result.output_file
        assert len(out) == 2
        assert out[0]["out_name"] == "HELLO WORLD"
        assert out[1]["out_name"] == "TESTING AUTO"

    def test_auto_customer_report(self, examples_dir):
        """Load customer_report.cob with zero manual copybook definitions."""
        prog = load(examples_dir / "customer_report.cob")
        assert "INPUT-FILE" in prog.inputs
        assert "OUTPUT-FILE" in prog.outputs

        result = prog(
            input_file=[
                {"IN-CUST-ID": 1, "IN-CUST-NAME": "Alice", "IN-BALANCE": 5000.0},
            ]
        )
        out = result.output_file
        assert len(out) == 1
        assert out[0]["out_discount"] == 500.0

    def test_file_info_shows_fields(self, examples_dir):
        """The file_info property should describe the discovered I/O."""
        prog = load(examples_dir / "uppercase.cob")
        info = prog.file_info
        assert "INPUT-FILE" in info
        assert "OUTPUT-FILE" in info
        assert "IN-ID" in info
        assert "OUT-NAME" in info

    def test_record_layouts_property(self, examples_dir):
        """Access all record layouts for multi-record FDs."""
        prog = load(examples_dir / "customer_report.cob")
        layouts = prog.record_layouts
        assert "INPUT-FILE" in layouts
        assert "OUTPUT-FILE" in layouts
        # Each should have at least one record layout
        assert len(layouts["INPUT-FILE"]) >= 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unknown_input_file_raises(self, examples_dir):
        prog = load(examples_dir / "hello.cob")
        with pytest.raises(ValueError, match="Unknown input file"):
            prog(some_unknown_file=[{"a": 1}])
