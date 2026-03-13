#!/usr/bin/env python3
"""Demo: call COBOL programs from Python using pobol.

Run from the project root:
    uv run python examples/demo.py
"""

from pathlib import Path
from pobol import load

COBOL_DIR = Path(__file__).parent / "cobol"


def demo_hello():
    """Simplest case: no I/O, just stdout."""
    print("=" * 60)
    print("DEMO 1: hello.cob — pure stdout")
    print("=" * 60)
    hello = load(COBOL_DIR / "hello.cob")
    result = hello()
    print(f"  stdout: {result.stdout.strip()}")
    print()


def demo_add():
    """stdin → stdout."""
    print("=" * 60)
    print("DEMO 2: add_numbers.cob — stdin/stdout")
    print("=" * 60)
    add = load(COBOL_DIR / "add_numbers.cob")
    result = add(stdin="0010000200")  # 100 + 200
    print(f"  100 + 200 = {result.stdout.strip()}")
    print()


def demo_uppercase_auto():
    """File I/O with ZERO manual config — fully auto-discovered."""
    print("=" * 60)
    print("DEMO 3: uppercase.cob — auto-discovered file I/O")
    print("=" * 60)

    # Just point at the .cob file. pobol parses the source,
    # discovers SELECT/FD/OPEN, extracts PIC clauses, handles everything.
    upper = load(COBOL_DIR / "uppercase.cob")

    print(f"  {upper}")
    print(f"  Auto-discovered inputs:  {list(upper.inputs.keys())}")
    print(f"  Auto-discovered outputs: {list(upper.outputs.keys())}")
    print()

    result = upper(
        input_file=[
            {"IN-ID": 1, "IN-NAME": "alice smith"},
            {"IN-ID": 2, "IN-NAME": "bob jones"},
        ]
    )

    for rec in result.output_file:
        print(f"  ID={rec['out_id']}  NAME={rec['out_name']!r}")
    print()


def demo_customer_report_auto():
    """Complex file I/O with decimal arithmetic — zero config."""
    print("=" * 60)
    print("DEMO 4: customer_report.cob — auto-discovered business logic")
    print("=" * 60)

    report = load(COBOL_DIR / "customer_report.cob")

    # Show what pobol discovered from parsing the COBOL source
    print(report.file_info)
    print()

    customers = [
        {"IN-CUST-ID": 1, "IN-CUST-NAME": "Alice Johnson", "IN-BALANCE": 2500.00},
        {"IN-CUST-ID": 2, "IN-CUST-NAME": "Bob Smith", "IN-BALANCE": 800.00},
        {"IN-CUST-ID": 3, "IN-CUST-NAME": "Charlie Brown", "IN-BALANCE": 15000.50},
    ]

    result = report(input_file=customers)

    print(f"  {result.stdout.strip()}")
    print(f"  {'ID':<8} {'Name':<32} {'Balance':>12} {'Discount':>12}")
    print(f"  {'-' * 8} {'-' * 32} {'-' * 12} {'-' * 12}")
    for rec in result.output_file:
        print(
            f"  {rec['out_cust_id']:<8} {rec['out_cust_name']:<32} "
            f"{rec['out_balance']:>12.2f} {rec['out_discount']:>12.2f}"
        )
    print()


def demo_mainframe_parsing():
    """Show auto-parsing of a mainframe COBOL source (inline example)."""
    print("=" * 60)
    print("DEMO 5: Parse mainframe-format COBOL source")
    print("=" * 60)

    from pobol import parse_cobol_source

    # A small inline example showing mainframe format parsing.
    # In practice you'd point this at your real .cbl files.
    sample = """\
000100 IDENTIFICATION DIVISION.                                         00010001
000200 PROGRAM-ID. SAMPLE.                                              00020001
000300 ENVIRONMENT DIVISION.                                            00030001
000400 INPUT-OUTPUT SECTION.                                            00040001
000500 FILE-CONTROL.                                                    00050001
000600     SELECT INPUT-FILE ASSIGN TO DATAIN                           00060001
000700           ORGANIZATION IS SEQUENTIAL.                            00070001
000800     SELECT OUTPUT-FILE ASSIGN TO DATAOUT                         00080001
000900           ORGANIZATION IS SEQUENTIAL.                            00090001
001000 DATA DIVISION.                                                   00100001
001100 FILE SECTION.                                                    00110001
001200 FD INPUT-FILE                                                    00120001
001300     LABEL RECORDS ARE STANDARD.                                  00130001
001400 01 INPUT-RECORD.                                                 00140001
001500     05 IN-ID       PIC 9(6).                                     00150001
001600     05 IN-NAME     PIC X(30).                                    00160001
001700 FD OUTPUT-FILE                                                   00170001
001800     LABEL RECORDS ARE STANDARD.                                  00180001
001900 01 OUTPUT-RECORD.                                                00190001
002000     05 OUT-ID      PIC 9(6).                                     00200001
002100     05 OUT-NAME    PIC X(30).                                    00210001
002200 WORKING-STORAGE SECTION.                                         00220001
002300 PROCEDURE DIVISION.                                              00230001
002400 MAIN-PARA.                                                       00240001
002500     OPEN INPUT INPUT-FILE OUTPUT OUTPUT-FILE.                    00250001
002600     STOP RUN.                                                    00260001
"""
    parsed = parse_cobol_source(sample, rewrite_assigns=False)

    print(f"  Files discovered: {len(parsed.files)}")
    print()
    for name, fspec in parsed.files.items():
        layouts = list(fspec.record_layouts.keys())
        fields_total = sum(len(cb.fields) for cb in fspec.record_layouts.values())
        print(
            f"  {fspec.direction:>8} {name:<20} assign={fspec.assign_name:<10} "
            f"records={len(layouts)} fields={fields_total}"
        )
        for rec_name, cb in fspec.record_layouts.items():
            print(
                f"           └─ {rec_name} ({cb.record_length} bytes, {len(cb.fields)} fields)"
            )
    print()


if __name__ == "__main__":
    demo_hello()
    demo_add()
    demo_uppercase_auto()
    demo_customer_report_auto()
    demo_mainframe_parsing()
