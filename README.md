# pobol

[![CI](https://github.com/andyreagan/pobol/actions/workflows/ci.yml/badge.svg)](https://github.com/andyreagan/pobol/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pobol)](https://pypi.org/project/pobol/)

Call COBOL programs as Python functions. Like [maturin](https://github.com/PyO3/maturin) for Rust, but for GnuCOBOL.

**Motivation:** You're migrating COBOL off a mainframe. The programs do real work you want to keep, but they expect VSAM files, DD names, and JCL — none of which exist on Linux/macOS. pobol wraps `cobc` (GnuCOBOL) so you can:

1. Point it at a `.cbl` file — it parses the source and discovers everything
2. Pass Python dicts as input records
3. Get Python dicts back from the output files

No copybook transcription, no temp-file juggling, no batch scripts.

## Quick Start

```bash
uv add pobol          # or: pip install pobol
```

### Zero-config mode (auto-discovery)

```python
from pobol import load

# pobol parses the COBOL source, discovers SELECT/FD clauses,
# extracts record layouts, strips mainframe artifacts, compiles, and
# handles all file I/O automatically.
report = load("customer_report.cob")

print(report.file_info)
# COBOL Program: customer_report.cob
#
# INPUTS:
#   INPUT-FILE (45 bytes): IN-CUST-ID, IN-CUST-NAME, IN-BALANCE
# OUTPUTS:
#   OUTPUT-FILE (54 bytes): OUT-CUST-ID, OUT-CUST-NAME, OUT-BALANCE, OUT-DISCOUNT

result = report(input_file=[
    {"IN-CUST-ID": 1, "IN-CUST-NAME": "Alice", "IN-BALANCE": 2500.00},
    {"IN-CUST-ID": 2, "IN-CUST-NAME": "Bob",   "IN-BALANCE":  800.00},
])

for rec in result.output_file:
    print(rec)
# {'out_cust_id': 1, 'out_cust_name': 'Alice', 'out_balance': 2500.0, 'out_discount': 250.0}
# {'out_cust_id': 2, 'out_cust_name': 'Bob',   'out_balance':  800.0, 'out_discount':   0.0}
```

### Mainframe source — works directly

```python
# Point at raw mainframe COBOL with sequence numbers, LABEL RECORDS,
# IBM-370, DD-name assigns — pobol handles it all:
prog = load("check_disbursement.cbl")

print(prog.file_info)
# Discovers all 6 files, 4 record layouts under one FD, 60+ fields...
# Strips sequence numbers, rewrites ASSIGN TO for env-var mapping,
# removes LABEL RECORDS/RECORDING MODE/BLOCK CONTAINS.
```

### Explicit copybooks (optional override)

```python
from pobol import load, parse_copybook

input_cb = parse_copybook("""
    01  INPUT-RECORD.
        05  CUST-ID     PIC 9(6).
        05  CUST-NAME   PIC X(30).
        05  BALANCE     PIC 9(7)V9(2).
""")
output_cb = parse_copybook("""
    01  OUTPUT-RECORD.
        05  CUST-ID     PIC 9(6).
        05  CUST-NAME   PIC X(30).
        05  BALANCE     PIC 9(7)V9(2).
        05  DISCOUNT    PIC 9(7)V9(2).
""")

report = load(
    "customer_report.cob",
    inputs={"INPUT-FILE": input_cb},
    outputs={"OUTPUT-FILE": output_cb},
)
```

## How It Works

```
                                                        ┌──────────────────┐
  Python dict ──▶ Copybook.encode() ──▶ temp file ──▶  │                  │
                                                        │  cobc-compiled   │
  Python dict ◀── Copybook.decode() ◀── temp file ◀──  │  COBOL program   │
                                                        │                  │
                                                        └──────────────────┘
```

1. **Parse source** — discovers SELECT/FD/OPEN clauses, extracts PIC field layouts, detects input vs output files
2. **Strip mainframe** — removes sequence numbers (cols 1-6), identification (cols 73-80), LABEL RECORDS, RECORDING MODE, BLOCK CONTAINS, fixes SOURCE-COMPUTER
3. **Rewrite assigns** — converts `ASSIGN TO DATAIN` (DD names) to working-storage paths loaded from `DD_*` environment variables
4. **Compile** — `cobc -x` compiles to a native executable (cached by content hash)
5. **Write inputs** — your Python dicts are encoded as fixed-width records to temp files
6. **Run** — the executable runs with `DD_*` env vars pointing to temp files
7. **Read outputs** — output temp files are decoded back into Python dicts

## Handling Multiple Record Types

Real mainframe COBOL often has multiple 01-level records under one FD (header, detail, trailer). pobol discovers all of them:

```python
prog = load("check_disbursement.cbl")

# See all discovered layouts
for file_name, layouts in prog.record_layouts.items():
    for rec_name, copybook in layouts.items():
        print(f"{file_name}/{rec_name}: {len(copybook.fields)} fields")
# TXN-DATA-FILE/TXN-DATA-RECORD: 2 fields
# TXN-DATA-FILE/TXN-DATA-HDR-RECORD: 5 fields
# TXN-DATA-FILE/TXN-DATA-DETAIL-RECORD: 60 fields
# TXN-DATA-FILE/TXN-DATA-TRLR-RECORD: 5 fields

# For multi-record files, use raw_files with pre-encoded bytes:
header_bytes = header_copybook.encode(header_dict)
detail_bytes = detail_copybook.encode_many(detail_dicts)
trailer_bytes = trailer_copybook.encode(trailer_dict)

result = prog(raw_files={
    "TXN-DATA-FILE": header_bytes + detail_bytes + trailer_bytes,
})
```

## API Reference

### `load(source, **kwargs) → CobolProgram`

Compile a COBOL source file and return a callable.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | `str \| Path` | required | Path to .cob/.cbl file |
| `inputs` | `dict[str, Copybook]` | `None` | Override auto-discovered input layouts |
| `outputs` | `dict[str, Copybook]` | `None` | Override auto-discovered output layouts |
| `dialect` | `str` | `None` | GnuCOBOL `-std=` dialect |
| `extra_flags` | `list[str]` | `None` | Extra `cobc` flags |
| `strip_mainframe` | `bool` | `True` | Strip mainframe format artifacts |
| `rewrite_assigns` | `bool` | `True` | Rewrite ASSIGN TO for env-var mapping |

### `CobolProgram.__call__(**kwargs) → CobolResult`

| Parameter | Type | Description |
|-----------|------|-------------|
| `stdin` | `str` | Data for ACCEPT/stdin |
| `env` | `dict` | Extra environment variables |
| `timeout` | `float` | Execution timeout (default 30s) |
| `raw_files` | `dict[str, bytes]` | Pre-encoded file data by SELECT name |
| `**file_inputs` | `list[dict]` | Input data as list of dicts, keyed by SELECT name |

### `CobolResult`

| Attribute | Type | Description |
|-----------|------|-------------|
| `.stdout` | `str` | Program stdout (DISPLAY output) |
| `.stderr` | `str` | Program stderr |
| `.return_code` | `int` | Exit code |
| `.outputs` | `dict[str, list[dict]]` | Decoded output files |
| `.output_file` | `list[dict]` | Shorthand for `.outputs["OUTPUT-FILE"]` |

### `parse_cobol_source(source, **kwargs) → ParsedSource`

Parse without compiling. Returns discovered files, record layouts, and cleaned source.

### Supported PIC Clauses

| PIC | Python type | Notes |
|-----|-------------|-------|
| `X(n)`, `XX` | `str` | Left-justified, space-padded |
| `9(n)`, `999` | `int` | Zero-padded |
| `S9(n)` | `int` | Trailing sign overpunch |
| `9(n)V9(m)`, `9(5)V99` | `float` | Implied decimal (mixed forms supported) |
| `S9(n)V9(m)` | `float` | Signed implied decimal |

## Development

```bash
uv sync
uv run pytest -v
uv run python examples/demo.py
```

## Prerequisites

- **GnuCOBOL** (`cobc`) — `brew install gnucobol` / `apt install gnucobol`
- **Python 3.11+**

## License

MIT
