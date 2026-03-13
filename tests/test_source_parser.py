"""Tests for automatic COBOL source parsing and file discovery."""

from textwrap import dedent

from pobol.source_parser import (
    strip_mainframe_format,
    parse_cobol_source,
    _extract_selects,
    _detect_direction,
    _extract_fd_records,
    _rewrite_assigns_for_env,
)


# ---------------------------------------------------------------------------
# Mainframe format stripping
# ---------------------------------------------------------------------------


class TestStripMainframeFormat:
    """Tests for mainframe source stripping.  We use multi-line samples
    because detection requires a statistically significant fraction of
    lines to have sequence numbers and wide columns."""

    MAINFRAME_SAMPLE = (
        "000100 IDENTIFICATION DIVISION.                                         00010001\n"
        "000200 PROGRAM-ID. TEST.                                                00020001\n"
        "000300 ENVIRONMENT DIVISION.                                            00030001\n"
        "000400 DATA DIVISION.                                                   00040001\n"
        "000500 PROCEDURE DIVISION.                                              00050001\n"
        "000600     STOP RUN.                                                    00060001\n"
    )

    def test_strips_sequence_numbers(self):
        result = strip_mainframe_format(self.MAINFRAME_SAMPLE)
        assert "IDENTIFICATION DIVISION." in result
        assert "000100" not in result
        assert "00010001" not in result

    def test_preserves_comment_indicator(self):
        src = self.MAINFRAME_SAMPLE + (
            "000610*****************************************************************00061001\n"
        )
        result = strip_mainframe_format(src)
        assert any(line.strip().startswith("*") for line in result.splitlines())

    def test_removes_label_records(self):
        src = "     LABEL RECORDS ARE STANDARD."
        result = strip_mainframe_format(src)
        assert "LABEL RECORDS" not in result

    def test_removes_recording_mode(self):
        src = "     RECORDING MODE IS F."
        result = strip_mainframe_format(src)
        assert "RECORDING MODE" not in result

    def test_removes_block_contains(self):
        src = "     BLOCK CONTAINS 0 RECORDS."
        result = strip_mainframe_format(src)
        assert "BLOCK CONTAINS" not in result

    def test_fixes_ibm_computer(self):
        src = "       SOURCE-COMPUTER. IBM-370."
        result = strip_mainframe_format(src)
        assert "X86-64" in result
        assert "IBM-370" not in result


# ---------------------------------------------------------------------------
# SELECT extraction
# ---------------------------------------------------------------------------


class TestExtractSelects:
    def test_basic(self):
        src = """
           SELECT PRINT-SETUP ASSIGN TO SETUP
                 ORGANIZATION IS SEQUENTIAL.
           SELECT VAR-DATA-FILE ASSIGN TO DATAIN
                 ORGANIZATION IS SEQUENTIAL.
        """
        selects = _extract_selects(src)
        assert "PRINT-SETUP" in selects
        assert selects["PRINT-SETUP"] == "SETUP"
        assert "VAR-DATA-FILE" in selects
        assert selects["VAR-DATA-FILE"] == "DATAIN"

    def test_quoted_assign(self):
        src = '    SELECT MY-FILE ASSIGN TO "myfile.dat"'
        selects = _extract_selects(src)
        assert selects["MY-FILE"] == "MYFILE.DAT"

    def test_external_assign(self):
        src = "    SELECT MY-FILE ASSIGN TO EXTERNAL WS-PATH"
        selects = _extract_selects(src)
        assert selects["MY-FILE"] == "WS-PATH"


# ---------------------------------------------------------------------------
# Direction detection
# ---------------------------------------------------------------------------


class TestDetectDirection:
    def test_input(self):
        src = """
       PROCEDURE DIVISION.
           OPEN INPUT MY-FILE.
           READ MY-FILE.
           CLOSE MY-FILE.
        """
        assert _detect_direction(src, "MY-FILE") == "input"

    def test_output(self):
        src = """
       PROCEDURE DIVISION.
           OPEN OUTPUT MY-FILE.
           WRITE MY-RECORD.
           CLOSE MY-FILE.
        """
        assert _detect_direction(src, "MY-FILE") == "output"

    def test_multi_file_open(self):
        src = """
       PROCEDURE DIVISION.
           OPEN INPUT PRINT-SETUP
                INPUT VAR-DATA-FILE
                OUTPUT CHECK-OUT-FILE
                OUTPUT ARP-FILE.
        """
        assert _detect_direction(src, "PRINT-SETUP") == "input"
        assert _detect_direction(src, "VAR-DATA-FILE") == "input"
        assert _detect_direction(src, "CHECK-OUT-FILE") == "output"
        assert _detect_direction(src, "ARP-FILE") == "output"


# ---------------------------------------------------------------------------
# FD record extraction
# ---------------------------------------------------------------------------


SAMPLE_FILE_SECTION = """
       FILE SECTION.

       FD INPUT-FILE

       01 INPUT-RECORD.
           05 IN-ID       PIC 9(6).
           05 IN-NAME     PIC X(30).
           05 IN-BALANCE  PIC 9(7)V9(2).

       FD OUTPUT-FILE

       01 OUTPUT-RECORD.
           05 OUT-ID      PIC 9(6).
           05 OUT-NAME    PIC X(30).
           05 OUT-TOTAL   PIC 9(9)V9(2).

       WORKING-STORAGE SECTION.
       01 WS-TEMP PIC X(10).
"""


class TestExtractFdRecords:
    def test_extracts_records(self):
        records = _extract_fd_records(
            SAMPLE_FILE_SECTION, {"INPUT-FILE", "OUTPUT-FILE"}
        )
        assert len(records["INPUT-FILE"]) == 1
        assert records["INPUT-FILE"][0][0] == "INPUT-RECORD"
        assert len(records["OUTPUT-FILE"]) == 1
        assert records["OUTPUT-FILE"][0][0] == "OUTPUT-RECORD"

    def test_record_is_parseable(self):
        records = _extract_fd_records(
            SAMPLE_FILE_SECTION, {"INPUT-FILE", "OUTPUT-FILE"}
        )
        from pobol.copybook import parse_copybook

        rec_name, rec_src = records["INPUT-FILE"][0]
        cb = parse_copybook(rec_src, name=rec_name)
        assert len(cb.fields) == 3
        assert cb.record_length == 45

    def test_multi_record_fd(self):
        """One FD with multiple 01-levels (header, detail, trailer)."""
        src = """
       FILE SECTION.

       FD VAR-DATA-FILE

       01 VAR-DATA-RECORD.
           05 VAR-DATA-REC-TYPE   PIC XX.
           05 FILLER              PIC X(3070).
       01 VAR-DATA-HDR-RECORD.
           05 FILLER              PIC XX.
           05 VAR-DATA-FORM-NO    PIC 9(4).
           05 VAR-DATA-TRANS-DATE PIC 9(8).
           05 FILLER              PIC X(3058).
       01 VAR-DATA-DETAIL-RECORD.
           05 VAR-DATA-DATEIN     PIC 9(8).
           05 VAR-DATA-CHECK-AMT  PIC 9(9)V9(2).
           05 VAR-DATA-CHECK-NUM  PIC 9(8).

       WORKING-STORAGE SECTION.
        """
        records = _extract_fd_records(src, {"VAR-DATA-FILE"})
        assert len(records["VAR-DATA-FILE"]) == 3
        names = [r[0] for r in records["VAR-DATA-FILE"]]
        assert "VAR-DATA-RECORD" in names
        assert "VAR-DATA-HDR-RECORD" in names
        assert "VAR-DATA-DETAIL-RECORD" in names


# ---------------------------------------------------------------------------
# ASSIGN rewriting
# ---------------------------------------------------------------------------


class TestRewriteAssigns:
    def test_rewrites_bare_dd_name(self):
        """Bare DD-name assigns (mainframe original) are rewritten."""
        src = dedent("""\
           SELECT MY-FILE ASSIGN TO MYDD
                 ORGANIZATION IS SEQUENTIAL.

           WORKING-STORAGE SECTION.
           01 WS-TEMP PIC X.

           PROCEDURE DIVISION.
           MAIN-PARA.
               OPEN INPUT MY-FILE.
        """)
        rewritten, env_map = _rewrite_assigns_for_env(src)
        assert "MY-FILE" in env_map
        assert env_map["MY-FILE"] == "DD_MY_FILE"
        assert "WS-PATH-MY-FILE" in rewritten
        assert "ACCEPT WS-PATH-MY-FILE" in rewritten

    def test_rewrites_quoted_literal(self):
        """Quoted-literal assigns (GnuCOBOL-ported) are also rewritten."""
        src = dedent("""\
           SELECT MY-FILE ASSIGN TO "myfile.dat"
                 ORGANIZATION IS SEQUENTIAL.

           WORKING-STORAGE SECTION.
           01 WS-TEMP PIC X.

           PROCEDURE DIVISION.
           MAIN-PARA.
               OPEN INPUT MY-FILE.
        """)
        rewritten, env_map = _rewrite_assigns_for_env(src)
        assert "MY-FILE" in env_map
        assert env_map["MY-FILE"] == "DD_MY_FILE"
        assert "WS-PATH-MY-FILE" in rewritten
        assert "ACCEPT WS-PATH-MY-FILE" in rewritten
        assert '"myfile.dat"' not in rewritten

    def test_skips_ws_path_variable(self):
        """WS-* path variables (pobol-native) are left alone."""
        src = dedent("""\
           SELECT MY-FILE ASSIGN TO WS-MY-PATH
                 ORGANIZATION IS SEQUENTIAL.

           WORKING-STORAGE SECTION.
           01 WS-MY-PATH PIC X(256).

           PROCEDURE DIVISION.
           MAIN-PARA.
               ACCEPT WS-MY-PATH FROM ENVIRONMENT "DD_MY_FILE"
               OPEN INPUT MY-FILE.
        """)
        rewritten, env_map = _rewrite_assigns_for_env(src)
        assert "WS-PATH-MY-FILE" not in rewritten
        assert "WS-MY-PATH" in rewritten

    def test_skips_existing_accept_from_environment(self):
        """Files that already have ACCEPT FROM ENVIRONMENT are left alone."""
        src = dedent("""\
           SELECT MY-FILE ASSIGN TO MYDD
                 ORGANIZATION IS SEQUENTIAL.

           WORKING-STORAGE SECTION.
           01 WS-TEMP PIC X(256).

           PROCEDURE DIVISION.
           MAIN-PARA.
               ACCEPT WS-TEMP FROM ENVIRONMENT "DD_MY_FILE"
               OPEN INPUT MY-FILE.
        """)
        rewritten, env_map = _rewrite_assigns_for_env(src)
        # The assign is left unchanged because there's already an ACCEPT
        assert "WS-PATH-MY-FILE" not in rewritten

    def test_rewrites_multiple_mixed_assigns(self):
        """Mix of bare DD, quoted literal, and WS-* in one source."""
        src = dedent("""\
           SELECT FILE-A ASSIGN TO DDNAMEA
                 ORGANIZATION IS SEQUENTIAL.
           SELECT FILE-B ASSIGN TO "fileb.dat"
                 ORGANIZATION IS SEQUENTIAL.
           SELECT FILE-C ASSIGN TO WS-PATH-C
                 ORGANIZATION IS SEQUENTIAL.

           WORKING-STORAGE SECTION.
           01 WS-PATH-C PIC X(256).
           01 WS-TEMP PIC X.

           PROCEDURE DIVISION.
           MAIN-PARA.
               OPEN INPUT FILE-A FILE-B FILE-C.
        """)
        rewritten, env_map = _rewrite_assigns_for_env(src)
        # FILE-A (bare DD) and FILE-B (quoted) are rewritten
        assert "WS-PATH-FILE-A" in rewritten
        assert "WS-PATH-FILE-B" in rewritten
        # FILE-C (WS-*) is left alone
        assert "WS-PATH-FILE-C" not in rewritten
        assert "WS-PATH-C" in rewritten


# ---------------------------------------------------------------------------
# Full parse integration
# ---------------------------------------------------------------------------


FULL_PROGRAM = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TEST-PROG.

       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT INPUT-FILE ASSIGN TO WS-INPUT-PATH
               ORGANIZATION IS LINE SEQUENTIAL.
           SELECT OUTPUT-FILE ASSIGN TO WS-OUTPUT-PATH
               ORGANIZATION IS LINE SEQUENTIAL.

       DATA DIVISION.
       FILE SECTION.

       FD INPUT-FILE.
       01 INPUT-RECORD.
           05 IN-ID       PIC 9(4).
           05 IN-NAME     PIC X(20).

       FD OUTPUT-FILE.
       01 OUTPUT-RECORD.
           05 OUT-ID      PIC 9(4).
           05 OUT-NAME    PIC X(20).

       WORKING-STORAGE SECTION.
       01 WS-INPUT-PATH   PIC X(256).
       01 WS-OUTPUT-PATH  PIC X(256).
       01 WS-EOF          PIC 9 VALUE 0.

       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-INPUT-PATH  FROM ENVIRONMENT "DD_INPUT_FILE"
           ACCEPT WS-OUTPUT-PATH FROM ENVIRONMENT "DD_OUTPUT_FILE"
           OPEN INPUT INPUT-FILE
           OPEN OUTPUT OUTPUT-FILE
           PERFORM READ-LOOP UNTIL WS-EOF = 1
           CLOSE INPUT-FILE
           CLOSE OUTPUT-FILE
           STOP RUN.

       READ-LOOP.
           READ INPUT-FILE
               AT END MOVE 1 TO WS-EOF
               NOT AT END
                   MOVE IN-ID TO OUT-ID
                   MOVE FUNCTION UPPER-CASE(IN-NAME) TO OUT-NAME
                   WRITE OUTPUT-RECORD
           END-READ.
"""


class TestFullParse:
    def test_discovers_files(self):
        parsed = parse_cobol_source(
            FULL_PROGRAM, strip_mainframe=False, rewrite_assigns=False
        )
        assert "INPUT-FILE" in parsed.files
        assert "OUTPUT-FILE" in parsed.files

    def test_discovers_directions(self):
        parsed = parse_cobol_source(
            FULL_PROGRAM, strip_mainframe=False, rewrite_assigns=False
        )
        assert parsed.files["INPUT-FILE"].direction == "input"
        assert parsed.files["OUTPUT-FILE"].direction == "output"

    def test_discovers_record_layouts(self):
        parsed = parse_cobol_source(
            FULL_PROGRAM, strip_mainframe=False, rewrite_assigns=False
        )
        in_layouts = parsed.files["INPUT-FILE"].record_layouts
        assert "INPUT-RECORD" in in_layouts
        cb = in_layouts["INPUT-RECORD"]
        assert len(cb.fields) == 2
        assert cb.fields[0].name == "IN-ID"
        assert cb.record_length == 24

    def test_discovers_output_layouts(self):
        parsed = parse_cobol_source(
            FULL_PROGRAM, strip_mainframe=False, rewrite_assigns=False
        )
        out_layouts = parsed.files["OUTPUT-FILE"].record_layouts
        assert "OUTPUT-RECORD" in out_layouts
        cb = out_layouts["OUTPUT-RECORD"]
        assert len(cb.fields) == 2
        assert cb.fields[1].name == "OUT-NAME"


# ---------------------------------------------------------------------------
# Synthetic mainframe-style source
# ---------------------------------------------------------------------------
# This exercises the same complexities found in real enterprise COBOL
# (e.g. insurance check-printing programs) without including any
# proprietary code:
#   - Mainframe fixed format (cols 1-6 sequence, cols 73-80 ident)
#   - 6 SELECT/FD pairs with bare DD-name assigns
#   - Multi-record FDs (header, detail, trailer under one FD)
#   - ~50 fields on the detail record
#   - LABEL RECORDS, RECORDING MODE, BLOCK CONTAINS, RECORD CONTAINS
#   - IBM-370 SOURCE/OBJECT-COMPUTER
#   - 88-level condition names
#   - FILLER fields mixed with named fields
#   - COMP-3 packed fields
#   - FILE STATUS clauses
#   - Multi-file OPEN statements
#   - Deeply nested WORKING-STORAGE with report headers
# ---------------------------------------------------------------------------

MAINFRAME_SYNTHETIC = """\
000100 IDENTIFICATION DIVISION.                                         00010001
000200 PROGRAM-ID. SYNTH-CHK.                                           00020001
000300 AUTHOR.          TEST AUTHOR.                                    00030001
000400 DATE-WRITTEN.    JANUARY 2026.                                   00040001
000500 DATE-COMPILED.   JANUARY 2026.                                   00050001
000600 REMARKS.                                                         00060001
000610*****************************************************************
000620** Synthetic check-printing program for pobol test coverage. **
000700** Exercises mainframe complexities without proprietary code.   ** 00070001
000800*****************************************************************
001000 ENVIRONMENT DIVISION.                                            00100001
001100 CONFIGURATION SECTION.                                           00110001
001200 SOURCE-COMPUTER. IBM-370.                                        00120001
001300 OBJECT-COMPUTER. IBM-370.                                        00130001
001400                                                                  00140001
001500 INPUT-OUTPUT SECTION.                                            00150001
001600 FILE-CONTROL.                                                    00160001
001700                                                                  00170001
001800     SELECT PRINT-SETUP ASSIGN TO  SETUP                          00180001
001900           ORGANIZATION IS SEQUENTIAL                             00190001
002000           ACCESS       IS SEQUENTIAL                             00200001
002100           FILE STATUS  IS WS00-SETUP-STATUS.                     00210001
002200                                                                  00220001
002300     SELECT TXN-DATA-FILE ASSIGN TO DATAIN                        00230001
002400           ORGANIZATION IS SEQUENTIAL                             00240001
002500           ACCESS       IS SEQUENTIAL                             00250001
002600           FILE STATUS  IS WS00-TXN-DATA-STATUS.                  00260001
002700                                                                  00270001
002800     SELECT CHECK-OUT-FILE ASSIGN TO CHECK                        00280001
002900           ORGANIZATION IS SEQUENTIAL                             00290001
003000           ACCESS       IS SEQUENTIAL                             00300001
003100           FILE STATUS  IS WS00-CHECK-STATUS.                     00310001
003200                                                                  00320001
003300     SELECT RECON-FILE   ASSIGN TO  RECON                         00330001
003400           ORGANIZATION IS SEQUENTIAL                             00340001
003500           ACCESS       IS SEQUENTIAL                             00350001
003600           FILE STATUS  IS WS00-RECON-STATUS.                     00360001
003700                                                                  00370001
003800     SELECT CHECK-REG-FILE ASSIGN TO CHECKREG                     00380001
003900           ORGANIZATION IS SEQUENTIAL                             00390001
004000           ACCESS       IS SEQUENTIAL                             00400001
004100           FILE STATUS  IS WS00-CHECK-REG-STATUS.                 00410001
004200                                                                  00420001
004300     SELECT EXTRACT-FILE ASSIGN TO EXTRACT                        00430001
004400           ORGANIZATION IS SEQUENTIAL                             00440001
004500           ACCESS       IS SEQUENTIAL                             00450001
004600           FILE STATUS  IS WS00-EXTRACT-STATUS.                   00460001
004700                                                                  00470001
004800 DATA DIVISION.                                                   00480001
004900 FILE SECTION.                                                    00490001
005000                                                                  00500001
005100 FD PRINT-SETUP                                                   00510001
005200     LABEL RECORDS ARE STANDARD                                   00520001
005300     RECORDING MODE IS F                                          00530001
005400     BLOCK CONTAINS 0 RECORDS.                                    00540001
005500                                                                  00550001
005600 01 SETUP-RECORD               PIC X(80).                         00560001
005700                                                                  00570001
005800 FD TXN-DATA-FILE                                                 00580001
005900     LABEL RECORDS ARE STANDARD                                   00590001
006000     RECORDING MODE IS F                                          00600001
006100     BLOCK CONTAINS 0 RECORDS.                                    00610001
006200                                                                  00620001
006300 01 TXN-DATA-RECORD.                                              00630001
006400     05 TXN-DATA-REC-TYPE         PIC XX.                         00640001
006500        88 TXN-DATA-HDR-REC                 VALUE LOW-VALUES.     00650001
006600        88 TXN-DATA-TRLR1-REC               VALUE X'0707'.        00660001
006700        88 TXN-DATA-TRLR2-REC               VALUE X'FFFF'.        00670001
006800     05 FILLER                    PIC X(3070).                    00680001
006900 01 TXN-DATA-HDR-RECORD.                                          00690001
007000     05 FILLER                    PIC XX.                         00700001
007100     05 TXN-DATA-FORM-NO          PIC 9(4).                       00710001
007200     05 TXN-DATA-TRANS-DATE       PIC 9(8).                       00720001
007300     05 TXN-DATA-TRANS-TIME       PIC 9(6).                       00730001
007400     05 FILLER                    PIC X(3052).                    00740001
007500 01 TXN-DATA-DETAIL-RECORD.                                       00750001
007600     05 TXN-DATA-DATEIN           PIC 9(8).                       00760001
007700     05 TXN-DATA-CHECK-AMT        PIC 9(9)V99.                    00770001
007800     05 TXN-DATA-CHECK-NUM        PIC 9(8).                       00780001
007900     05 TXN-DATA-ID-NUMBER        PIC X(14).                      00790001
008000     05 TXN-DATA-PAYEE-1          PIC X(35).                      00800001
008100     05 TXN-DATA-PAYEE-2          PIC X(35).                      00810001
008200     05 TXN-DATA-ADDR-1           PIC X(30).                      00820001
008300     05 TXN-DATA-ADDR-2           PIC X(30).                      00830001
008400     05 TXN-DATA-ADDR-3           PIC X(30).                      00840001
008500     05 TXN-DATA-ADDR-4           PIC X(30).                      00850001
008600     05 TXN-DATA-ADDR-5           PIC X(30).                      00860001
008700     05 TXN-DATA-ADDR-6           PIC X(30).                      00870001
008800     05 TXN-DATA-ADDR-7           PIC X(30).                      00880001
008900     05 TXN-DATA-CO-CODE          PIC X(3).                       00890001
009000     05 TXN-DATA-CC-TYPE          PIC XX.                         00900001
009100     05 TXN-DATA-CITY             PIC X(20).                      00910001
009200     05 TXN-DATA-STATE-CODE       PIC XX.                         00920001
009300     05 TXN-DATA-ZIP-CODE         PIC X(9).                       00930001
009400     05 TXN-DATA-SSN              PIC 9(9).                       00940001
009500     05 TXN-DATA-CHECK-TYPE       PIC X.                          00950001
009600     05 TXN-DATA-SEQUENCE         PIC 9(6).                       00960001
009700     05 TXN-DATA-PAGE-NO          PIC 9(5).                       00970001
009800     05 TXN-DATA-POLICY-NO        PIC X(11).                      00980001
009900     05 TXN-DATA-FACE-VALUE       PIC X(14).                      00990001
010000     05 TXN-DATA-FACE-VAL-TEXT    PIC X(18).                      01000001
010100     05 TXN-DATA-LOAN-OUTSTD      PIC X(14).                      01010001
010200     05 TXN-DATA-LOAN-INT-DUE     PIC X(14).                      01020001
010300     05 TXN-DATA-SUR-CHARGE       PIC X(14).                      01030001
010400     05 TXN-DATA-SUR-CHRG-TEXT    PIC X(18).                      01040001
010500     05 TXN-DATA-ADJ-FEE          PIC X(14).                      01050001
010600     05 TXN-DATA-ADJ-FEE-TEXT     PIC X(21).                      01060001
010700     05 TXN-DATA-UNPROCESSED      PIC X(14).                      01070001
010800     05 TXN-DATA-NET-AMOUNT       PIC X(14).                      01080001
010900     05 TXN-DATA-TOT-WITHHELD     PIC X(14).                      01090001
011000     05 TXN-DATA-DISBURSEMENT     PIC X(14).                      01100001
011100     05 TXN-DATA-TAXABLE-GAIN     PIC X(14).                      01110001
011200     05 TXN-DATA-FED-WITHHOLDING  PIC X(14).                      01120001
011300     05 TXN-DATA-ST-WITHHOLDING   PIC X(14).                      01130001
011400     05 TXN-DATA-INSURED-NAME     PIC X(40).                      01140001
011500     05 TXN-DATA-PYMT-WITHHELD    PIC X(14).                      01150001
011600     05 TXN-DATA-PYMT-WTHLD-TXT1  PIC X(17).                      01160001
011700     05 TXN-DATA-PYMT-WTHLD-TXT2  PIC X.                          01170001
011800     05 TXN-DATA-TERM-DATE        PIC X(10).                      01180001
011900     05 TXN-DATA-TAX-TEXT         PIC X(51).                      01190001
012000     05 TXN-DATA-NOTE-1           PIC X(50).                      01200001
012100     05 TXN-DATA-NOTE-2           PIC X(50).                      01210001
012200     05 TXN-DATA-NOTE-3           PIC X(50).                      01220001
012300     05 TXN-DATA-NOTE-4           PIC X(50).                      01230001
012400     05 TXN-DATA-AGENT-CODE       PIC X(5).                       01240001
012500     05 TXN-DATA-AGENCY-NO        PIC X(3).                       01250001
012600     05 TXN-DATA-MAIL-CODE        PIC X(4).                       01260001
012700     05 TXN-DATA-USER-ID          PIC X(7).                       01270001
012800     05 TXN-DATA-ADJ-FEE-SIGN     PIC X.                          01280001
012900     05 TXN-DATA-SOURCE-SYSTEM    PIC X.                          01290001
013000     05 TXN-DATA-JOINT-IND        PIC X.                          01300001
013100     05 TXN-DATA-ISSUE-STATE      PIC X(2).                       01310001
013200     05 TXN-DATA-PRODUCT-CODE     PIC X(2).                       01320001
013300     05 TXN-DATA-JOINT-NAME       PIC X(40).                      01330001
013400     05 TXN-DATA-COST-BASIS       PIC X(14).                      01340001
013500     05 FILLER                    PIC X(2045).                    01350001
013600                                                                  01360001
013700 01 TXN-DATA-TRLR-RECORD.                                         01370001
013800     05 FILLER                    PIC XX.                         01380001
013900     05 TXN-DATA-TOT-PAGES        PIC 9(5).                       01390001
014000     05 TXN-DATA-TOT-CHECKS       PIC 9(5).                       01400001
014100     05 TXN-DATA-TOT-CHK-AMT      PIC 9(11)V99.                   01410001
014200     05 FILLER                    PIC X(3047).                    01420001
014300                                                                  01430001
014400 FD CHECK-OUT-FILE                                                01440001
014500     LABEL RECORDS ARE STANDARD                                   01450001
014600     BLOCK CONTAINS 0 RECORDS.                                    01460001
014700                                                                  01470001
014800 01  CHECK-OUT-DATA.                                              01480001
014900     05 CHECK-PRINT-DATA            PIC X(120).                   01490001
015000                                                                  01500001
015100 FD RECON-FILE                                                    01510001
015200     LABEL RECORDS ARE STANDARD                                   01520001
015300     BLOCK CONTAINS 0 RECORDS.                                    01530001
015400                                                                  01540001
015500 01 RECON-RECORD.                                                  01550001
015600     05 RECON-BANK-NO              PIC X(3).                       01560001
015700     05 RECON-ACCT-NO              PIC X(10).                      01570001
015800     05 RECON-CHECK-NO             PIC 9(10).                      01580001
015900     05 RECON-AMOUNT               PIC 9(13)V99 COMP-3.           01590001
016000     05 RECON-ISSUE-DT             PIC X(6).                       01600001
016100     05 RECON-ADDL-DATA            PIC X(15).                      01610001
016200     05 RECON-PAYEE                PIC X(35).                      01620001
016300     05 RECON-ADDR1                PIC X(35).                      01630001
016400     05 RECON-ADDR2                PIC X(35).                      01640001
016500     05 RECON-ADDR3                PIC X(35).                      01650001
016600     05 RECON-CITY                 PIC X(34).                      01660001
016700     05 RECON-SOURCE-SYSTEM        PIC X.                          01670001
016800     05 RECON-STATE                PIC XX.                         01680001
016900     05 RECON-ZIP                  PIC X(9).                       01690001
017000     05 RECON-TAX-ID               PIC X(9).                       01700001
017100     05 RECON-VOID-IND             PIC X(1).                       01710001
017200                                                                  01720001
017300 FD CHECK-REG-FILE                                                01730001
017400     LABEL RECORDS ARE STANDARD                                   01740001
017500     BLOCK CONTAINS 0 RECORDS.                                    01750001
017600                                                                  01760001
017700 01 CHECK-REG-RECORD.                                              01770001
017800    05 CHECK-REG-REC              PIC X(133).                     01780001
017900                                                                  01790001
018000 FD  EXTRACT-FILE                                                 01800001
018100     LABEL RECORDS ARE STANDARD                                   01810001
018200     RECORDING MODE IS F                                          01820001
018300     BLOCK CONTAINS 0 RECORDS                                     01830001
018400     RECORD CONTAINS 3072 CHARACTERS.                             01840001
018500                                                                  01850001
018600 01  EXTRACT-DATA                 PIC X(3072).                    01860001
018700                                                                  01870001
018800 WORKING-STORAGE SECTION.                                         01880001
018900                                                                  01890001
019000 01 FILLER                        PIC X(30)                       01900001
019100        VALUE  'WORKING STORAGE BEGINS HERE'.                     01910001
019200                                                                  01920001
019300 01 WS00-FILE-STATUS.                                             01930001
019400     05 WS00-SETUP-STATUS       PIC X(2).                         01940001
019500        88 SETUP-ACCESS-OK                   VALUE '00'.          01950001
019600        88 SETUP-EOF                         VALUE '10'.          01960001
019700                                                                  01970001
019800     05 WS00-TXN-DATA-STATUS    PIC X(2).                         01980001
019900        88 INPUT-ACCESS-OK                     VALUE '00'.        01990001
020000        88 INPUT-EOF                           VALUE '10'.        02000001
020100     05 WS00-CHECK-STATUS         PIC X(2).                       02010001
020200        88 CHECK-ACCESS-OK                     VALUE '00'.        02020001
020300     05 WS00-RECON-STATUS         PIC X(2).                       02030001
020400        88 RECON-ACCESS-OK                     VALUE '00'.        02040001
020500     05 WS00-CHECK-REG-STATUS     PIC X(2).                       02050001
020600        88 CHECK-REG-ACCESS-OK                 VALUE '00'.        02060001
020700     05 WS00-EXTRACT-STATUS       PIC X(2).                       02070001
020800        88 EXTRACT-ACCESS-OK                   VALUE '00'.        02080001
020900        88 EXTRACT-EOF                         VALUE '10'.        02090001
021000                                                                  02100001
021100 01 WS03-CHK-REG-HDRS.                                            02110001
021200     05 WS03-HDR01.                                               02120001
021300        10  FILLER                PIC X        VALUE '1'.         02130001
021400        10  FILLER                PIC X(40)    VALUE SPACES.      02140001
021500        10  FILLER                PIC X(50)    VALUE              02150001
021600              'CHECK DISBURSEMENT - CHECK REGISTER'.              02160001
021700        10  FILLER                PIC X(18)    VALUE SPACES.      02170001
021800        10  FILLER                PIC X(17)    VALUE              02180001
021900              'PROGRAM: SYNTHCHK'.                                02190001
022000     05 WS03-HDR02.                                               02200001
022100        10  FILLER                PIC X(1)     VALUE SPACE.       02210001
022200        10  WS03-REPORT-DATE      PIC X(10)    VALUE SPACES.      02220001
022300        10  FILLER                PIC X(43)    VALUE SPACES.      02230001
022400        10  FILLER                PIC X(4)     VALUE 'FOR '.      02240001
022500        10  WS03-TITLE            PIC X(20)    VALUE              02250001
022600              'CHECK PROCESSING'.                                 02260001
022700        10  FILLER                PIC X(31)    VALUE SPACES.      02270001
022800        10  FILLER                PIC X(6)     VALUE 'PAGE: '.    02280001
022900        10  WS03-PAGE-NUM         PIC ZZ9.                        02290001
023000                                                                  02300001
023100 PROCEDURE DIVISION.                                              02310001
023200 MAIN-PARA.                                                       02320001
023300     OPEN INPUT PRINT-SETUP                                       02330001
023400                TXN-DATA-FILE                                     02340001
023500          OUTPUT CHECK-OUT-FILE                                   02350001
023600                 RECON-FILE                                       02360001
023700                 CHECK-REG-FILE                                   02370001
023800                 EXTRACT-FILE.                                    02380001
023900     STOP RUN.                                                    02390001
"""


class TestMainframeSynthetic:
    """Tests exercising enterprise mainframe COBOL complexities using a
    synthetic source.  Covers the same parsing paths as real check-printing
    programs (6 files, multi-record FDs, ~50 detail fields, obsolete clauses,
    IBM-370, sequence numbers, FILLER, 88-levels, COMP-3, etc.)."""

    def test_parses_all_selects(self):
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        select_names = set(parsed.files.keys())
        assert "PRINT-SETUP" in select_names
        assert "TXN-DATA-FILE" in select_names
        assert "CHECK-OUT-FILE" in select_names
        assert "RECON-FILE" in select_names
        assert "CHECK-REG-FILE" in select_names
        assert "EXTRACT-FILE" in select_names

    def test_detects_input_files(self):
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        assert parsed.files["PRINT-SETUP"].direction == "input"
        assert parsed.files["TXN-DATA-FILE"].direction == "input"

    def test_detects_output_files(self):
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        assert parsed.files["CHECK-OUT-FILE"].direction == "output"
        assert parsed.files["RECON-FILE"].direction == "output"
        assert parsed.files["CHECK-REG-FILE"].direction == "output"
        assert parsed.files["EXTRACT-FILE"].direction == "output"

    def test_multi_file_open_parsed_correctly(self):
        """The OPEN statement lists 2 inputs and 4 outputs in one block."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        inputs = [n for n, f in parsed.files.items() if f.direction == "input"]
        outputs = [n for n, f in parsed.files.items() if f.direction == "output"]
        assert len(inputs) == 2
        assert len(outputs) == 4

    def test_extracts_multi_record_fd(self):
        """TXN-DATA-FILE has 4 different 01-levels (record, header, detail, trailer)."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        layouts = parsed.files["TXN-DATA-FILE"].record_layouts
        assert len(layouts) >= 3  # at least record, header, detail
        layout_names = set(layouts.keys())
        assert "TXN-DATA-RECORD" in layout_names
        assert "TXN-DATA-HDR-RECORD" in layout_names
        assert "TXN-DATA-DETAIL-RECORD" in layout_names
        assert "TXN-DATA-TRLR-RECORD" in layout_names

    def test_detail_record_field_count(self):
        """The detail record should have ~50 fields (mirrors real enterprise complexity)."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        layouts = parsed.files["TXN-DATA-FILE"].record_layouts
        cb = layouts["TXN-DATA-DETAIL-RECORD"]
        assert len(cb.fields) >= 40

    def test_detail_record_specific_fields(self):
        """Key fields on the detail record are discoverable."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        cb = parsed.files["TXN-DATA-FILE"].record_layouts["TXN-DATA-DETAIL-RECORD"]
        field_names = [f.name for f in cb.fields]
        assert "TXN-DATA-CHECK-AMT" in field_names
        assert "TXN-DATA-PAYEE-1" in field_names
        assert "TXN-DATA-SSN" in field_names
        assert "TXN-DATA-COST-BASIS" in field_names
        assert "TXN-DATA-JOINT-NAME" in field_names

    def test_detail_record_field_types(self):
        """Fields have the correct PIC types parsed."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        cb = parsed.files["TXN-DATA-FILE"].record_layouts["TXN-DATA-DETAIL-RECORD"]
        fields_by_name = {f.name: f for f in cb.fields}

        # PIC 9(8) → unsigned, 8 digits
        assert fields_by_name["TXN-DATA-DATEIN"].kind == "unsigned"
        assert fields_by_name["TXN-DATA-DATEIN"].length == 8

        # PIC 9(9)V99 → unsigned, 11 display digits, 2 decimal
        assert fields_by_name["TXN-DATA-CHECK-AMT"].kind == "unsigned"
        assert fields_by_name["TXN-DATA-CHECK-AMT"].decimals == 2

        # PIC X(35) → alpha, 35 bytes
        assert fields_by_name["TXN-DATA-PAYEE-1"].kind == "alpha"
        assert fields_by_name["TXN-DATA-PAYEE-1"].length == 35

        # PIC 9(9) → unsigned, 9 digits
        assert fields_by_name["TXN-DATA-SSN"].kind == "unsigned"
        assert fields_by_name["TXN-DATA-SSN"].length == 9

    def test_header_record_fields(self):
        """Header record has form number and date fields."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        cb = parsed.files["TXN-DATA-FILE"].record_layouts["TXN-DATA-HDR-RECORD"]
        field_names = [f.name for f in cb.fields]
        assert "TXN-DATA-FORM-NO" in field_names
        assert "TXN-DATA-TRANS-DATE" in field_names
        assert "TXN-DATA-TRANS-TIME" in field_names

    def test_trailer_record_fields(self):
        """Trailer record has totals."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        cb = parsed.files["TXN-DATA-FILE"].record_layouts["TXN-DATA-TRLR-RECORD"]
        field_names = [f.name for f in cb.fields]
        assert "TXN-DATA-TOT-PAGES" in field_names
        assert "TXN-DATA-TOT-CHECKS" in field_names
        assert "TXN-DATA-TOT-CHK-AMT" in field_names

    def test_strips_sequence_numbers(self):
        """Mainframe cols 1-6 sequence numbers are removed."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        assert "000100" not in parsed.cleaned_source
        assert "00010001" not in parsed.cleaned_source

    def test_strips_ibm370(self):
        """IBM-370 is rewritten to X86-64."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        assert "IBM-370" not in parsed.cleaned_source
        assert "X86-64" in parsed.cleaned_source

    def test_strips_label_records(self):
        """LABEL RECORDS ARE STANDARD is removed."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        assert "LABEL RECORDS" not in parsed.cleaned_source.upper()

    def test_strips_recording_mode(self):
        """RECORDING MODE IS F is removed."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        assert "RECORDING MODE" not in parsed.cleaned_source.upper()

    def test_strips_block_contains(self):
        """BLOCK CONTAINS 0 RECORDS is removed."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        assert "BLOCK CONTAINS" not in parsed.cleaned_source.upper()

    def test_strips_record_contains(self):
        """RECORD CONTAINS n CHARACTERS is removed."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        assert "RECORD CONTAINS" not in parsed.cleaned_source.upper()

    def test_preserves_identification_division(self):
        """Core program structure survives cleaning."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        assert "IDENTIFICATION DIVISION" in parsed.cleaned_source

    def test_preserves_comment_lines(self):
        """Comment indicators (* in col 7) survive stripping."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC)
        # After stripping cols 1-6, col 7 becomes col 1
        assert any(
            line.strip().startswith("*")
            for line in parsed.cleaned_source.splitlines()
            if line.strip()
        )

    def test_single_record_fds(self):
        """FDs with a single 01-level are correctly extracted."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)

        # CHECK-OUT-FILE: one record, one field
        check_layouts = parsed.files["CHECK-OUT-FILE"].record_layouts
        assert "CHECK-OUT-DATA" in check_layouts
        cb = check_layouts["CHECK-OUT-DATA"]
        assert len(cb.fields) == 1
        assert cb.fields[0].name == "CHECK-PRINT-DATA"
        assert cb.record_length == 120

        # CHECK-REG-FILE: one record, one field
        reg_layouts = parsed.files["CHECK-REG-FILE"].record_layouts
        assert "CHECK-REG-RECORD" in reg_layouts
        cb = reg_layouts["CHECK-REG-RECORD"]
        assert cb.record_length == 133

    def test_filler_fields_in_record(self):
        """FILLER fields appear in the parsed layout (they carry size info)."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        cb = parsed.files["TXN-DATA-FILE"].record_layouts["TXN-DATA-RECORD"]
        field_names = [f.name for f in cb.fields]
        assert "FILLER" in field_names
        assert "TXN-DATA-REC-TYPE" in field_names

    def test_assign_names_are_dd_names(self):
        """SELECT ASSIGN TO values are bare DD names (not paths)."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        assert parsed.files["PRINT-SETUP"].assign_name == "SETUP"
        assert parsed.files["TXN-DATA-FILE"].assign_name == "DATAIN"
        assert parsed.files["CHECK-OUT-FILE"].assign_name == "CHECK"
        assert parsed.files["RECON-FILE"].assign_name == "RECON"
        assert parsed.files["CHECK-REG-FILE"].assign_name == "CHECKREG"
        assert parsed.files["EXTRACT-FILE"].assign_name == "EXTRACT"

    def test_assign_rewriting_generates_env_vars(self):
        """When rewrite_assigns is enabled, bare DD names get env-var mapping."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=True)
        # The rewritten source should contain WS-PATH variables
        assert "WS-PATH-PRINT-SETUP" in parsed.cleaned_source
        assert "WS-PATH-TXN-DATA-FILE" in parsed.cleaned_source

    def test_detail_record_encode_decode_roundtrip(self):
        """The discovered detail record layout can encode and decode data."""
        parsed = parse_cobol_source(MAINFRAME_SYNTHETIC, rewrite_assigns=False)
        cb = parsed.files["TXN-DATA-FILE"].record_layouts["TXN-DATA-DETAIL-RECORD"]

        # Build a record using a few key fields
        record = {
            "TXN-DATA-DATEIN": 20260101,
            "TXN-DATA-CHECK-AMT": 1500.50,
            "TXN-DATA-CHECK-NUM": 12345678,
            "TXN-DATA-PAYEE-1": "JOHN DOE",
            "TXN-DATA-SSN": 123456789,
        }
        encoded = cb.encode(record)
        assert len(encoded) == cb.record_length

        decoded = cb.decode(encoded)
        assert decoded["txn_data_datein"] == 20260101
        assert decoded["txn_data_check_amt"] == 1500.50
        assert decoded["txn_data_payee_1"] == "JOHN DOE"
        assert decoded["txn_data_ssn"] == 123456789


# ---------------------------------------------------------------------------
# Quoted-literal variant (simulates a hand-ported GnuCOBOL source)
# ---------------------------------------------------------------------------
# When someone has already ported a mainframe program to GnuCOBOL by
# hardcoding file paths as quoted literals, pobol should still be able
# to discover all file I/O and rewrite the assigns for env-var control.
# This covers the workflow: mainframe original → manual GnuCOBOL port → pobol.

GNUCOBOL_PORTED_SYNTHETIC = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SYNTH-CHK.
       ENVIRONMENT DIVISION.
       CONFIGURATION SECTION.
       SOURCE-COMPUTER. X86-64.
       OBJECT-COMPUTER. X86-64.

       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT PRINT-SETUP ASSIGN TO "setup.dat"
                 ORGANIZATION IS SEQUENTIAL
                 ACCESS       IS SEQUENTIAL
                 FILE STATUS  IS WS00-SETUP-STATUS.
           SELECT TXN-DATA-FILE ASSIGN TO "datain.dat"
                 ORGANIZATION IS SEQUENTIAL
                 ACCESS       IS SEQUENTIAL
                 FILE STATUS  IS WS00-TXN-DATA-STATUS.
           SELECT CHECK-OUT-FILE ASSIGN TO "check.out"
                 ORGANIZATION IS SEQUENTIAL
                 ACCESS       IS SEQUENTIAL
                 FILE STATUS  IS WS00-CHECK-STATUS.
           SELECT RECON-FILE ASSIGN TO "recon.out"
                 ORGANIZATION IS SEQUENTIAL
                 ACCESS       IS SEQUENTIAL
                 FILE STATUS  IS WS00-RECON-STATUS.

       DATA DIVISION.
       FILE SECTION.

       FD PRINT-SETUP.
       01 SETUP-RECORD               PIC X(80).

       FD TXN-DATA-FILE.
       01 TXN-DATA-RECORD.
           05 TXN-DATA-REC-TYPE      PIC XX.
           05 FILLER                  PIC X(3070).
       01 TXN-DATA-DETAIL-RECORD.
           05 TXN-DATA-DATEIN        PIC 9(8).
           05 TXN-DATA-CHECK-AMT     PIC 9(9)V99.
           05 TXN-DATA-PAYEE-1       PIC X(35).

       FD CHECK-OUT-FILE.
       01 CHECK-OUT-DATA.
           05 CHECK-PRINT-DATA       PIC X(120).

       FD RECON-FILE.
       01 RECON-RECORD.
           05 RECON-BANK-NO          PIC X(3).
           05 RECON-ACCT-NO          PIC X(10).

       WORKING-STORAGE SECTION.
       01 WS00-SETUP-STATUS          PIC X(2).
       01 WS00-TXN-DATA-STATUS       PIC X(2).
       01 WS00-CHECK-STATUS           PIC X(2).
       01 WS00-RECON-STATUS           PIC X(2).

       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT PRINT-SETUP
                      TXN-DATA-FILE
                OUTPUT CHECK-OUT-FILE
                       RECON-FILE.
           STOP RUN.
"""


class TestGnucobolPortedSource:
    """Tests for sources that were already hand-ported to GnuCOBOL
    (quoted-literal assigns, no mainframe artifacts).  pobol should
    still discover all I/O and be able to rewrite assigns for env-var
    control — so you don't have to re-port the COBOL to use pobol."""

    def test_discovers_all_files(self):
        parsed = parse_cobol_source(
            GNUCOBOL_PORTED_SYNTHETIC, strip_mainframe=False, rewrite_assigns=False
        )
        assert set(parsed.files.keys()) == {
            "PRINT-SETUP",
            "TXN-DATA-FILE",
            "CHECK-OUT-FILE",
            "RECON-FILE",
        }

    def test_discovers_directions(self):
        parsed = parse_cobol_source(
            GNUCOBOL_PORTED_SYNTHETIC, strip_mainframe=False, rewrite_assigns=False
        )
        assert parsed.files["PRINT-SETUP"].direction == "input"
        assert parsed.files["TXN-DATA-FILE"].direction == "input"
        assert parsed.files["CHECK-OUT-FILE"].direction == "output"
        assert parsed.files["RECON-FILE"].direction == "output"

    def test_discovers_record_layouts(self):
        parsed = parse_cobol_source(
            GNUCOBOL_PORTED_SYNTHETIC, strip_mainframe=False, rewrite_assigns=False
        )
        layouts = parsed.files["TXN-DATA-FILE"].record_layouts
        assert "TXN-DATA-RECORD" in layouts
        assert "TXN-DATA-DETAIL-RECORD" in layouts

    def test_quoted_assigns_are_rewritten(self):
        """Quoted literals like ASSIGN TO \"setup.dat\" are rewritten
        to WS-PATH env-var mapping so pobol controls file paths."""
        parsed = parse_cobol_source(
            GNUCOBOL_PORTED_SYNTHETIC, strip_mainframe=False, rewrite_assigns=True
        )
        src = parsed.cleaned_source
        # Original quoted literals should be gone
        assert '"setup.dat"' not in src
        assert '"datain.dat"' not in src
        assert '"check.out"' not in src
        assert '"recon.out"' not in src
        # Replaced with WS-PATH variables
        assert "WS-PATH-PRINT-SETUP" in src
        assert "WS-PATH-TXN-DATA-FILE" in src
        assert "WS-PATH-CHECK-OUT-FILE" in src
        assert "WS-PATH-RECON-FILE" in src
        # ACCEPT statements are injected
        assert 'ACCEPT WS-PATH-PRINT-SETUP FROM ENVIRONMENT "DD_PRINT_SETUP"' in src
        assert 'ACCEPT WS-PATH-TXN-DATA-FILE FROM ENVIRONMENT "DD_TXN_DATA_FILE"' in src

    def test_mainframe_and_ported_produce_same_file_specs(self):
        """Parsing the mainframe original and the GnuCOBOL port should
        discover the same file structure (SELECT names, directions,
        record layouts).  Only the cleaned_source differs."""
        mainframe = parse_cobol_source(
            MAINFRAME_SYNTHETIC, strip_mainframe=True, rewrite_assigns=False
        )
        ported = parse_cobol_source(
            GNUCOBOL_PORTED_SYNTHETIC, strip_mainframe=False, rewrite_assigns=False
        )
        # Both should discover the same files (intersection of common ones)
        common_files = set(mainframe.files.keys()) & set(ported.files.keys())
        assert "PRINT-SETUP" in common_files
        assert "TXN-DATA-FILE" in common_files
        assert "CHECK-OUT-FILE" in common_files
        assert "RECON-FILE" in common_files

        # Directions should match for common files
        for name in common_files:
            assert mainframe.files[name].direction == ported.files[name].direction, (
                f"Direction mismatch for {name}: "
                f"{mainframe.files[name].direction} vs {ported.files[name].direction}"
            )
