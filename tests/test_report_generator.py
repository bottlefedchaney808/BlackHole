import json
import tempfile
from pathlib import Path
from report_generator import generate_pdf_report
from PyPDF2 import PdfReader

def test_generate_pdf_report_happy_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        context = {
            "focus": {
                "ticker": "TEST",
                "expiration_date": "2026-10-16",
                "option_type": "call",
                "strike": 100.0
            }
        }
        with open(tmppath / "suite_context.json", 'w') as f:
            json.dump(context, f)

        pdf_path = generate_pdf_report(str(tmppath), "TEST20260730T000000Z")
        assert Path(pdf_path).exists()
        assert Path(pdf_path).stat().st_size > 0

        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 0

def test_missing_optional_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        context = {"focus": {"ticker": "TEST", "expiration_date": "2026-10-16"}}
        with open(tmppath / "suite_context.json", 'w') as f:
            json.dump(context, f)

        pdf_path = generate_pdf_report(str(tmppath), "TEST20260730T000000Z")
        assert Path(pdf_path).exists()
        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 0

def test_malformed_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        context = {"focus": {"ticker": "TEST", "expiration_date": "2026-10-16"}}
        with open(tmppath / "suite_context.json", 'w') as f:
            json.dump(context, f)

        with open(tmppath / "vol_result.json", 'w') as f:
            f.write("{INVALID JSON}")

        pdf_path = generate_pdf_report(str(tmppath), "TEST20260730T000000Z")
        assert Path(pdf_path).exists()
        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 0
