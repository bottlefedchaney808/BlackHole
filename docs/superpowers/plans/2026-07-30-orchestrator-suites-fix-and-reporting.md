# Orchestrator Suites Fix & PDF Report Generation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix sentiment and options suite failures in the orchestrator, and create a consolidated PDF report generator that consolidates scattered orchestrator outputs into a single readable document.

**Architecture:** 
- **Sentiment fix:** Add sys.path manipulation in orchestrator.py to ensure `shared` module is discoverable when spawning sentiment-scanner subprocess
- **Options fix:** Verify ThetaData credentials are available and add error handling for missing/invalid credentials
- **PDF report:** New module that reads all suite outputs (vol_result.json, options results, var_result.json, sentiment blocks) and generates a professional PDF with charts, tables, and summaries

**Tech Stack:** Python 3.8+, reportlab (PDF generation), orchestrator.py (existing), shared module (existing)

## Global Constraints

- All PDF generation must work cross-platform (Windows/Mac/Linux)
- Report must be self-contained (no external images or fonts beyond standard reportlab fonts)
- Shared module must remain at repository root (do not move)
- ThetaData credentials checked at suite startup, fail-fast with clear message if missing
- PDF report should be written to `orchestrator_output/<run_id>/analysis_report.pdf`

---

## Task 1: Fix Sentiment Suite — Add sys.path Setup in Orchestrator

**Files:**
- Modify: `orchestrator.py:94-96` (after ROOT is set, before any shared imports)
- Test: Run sentiment-scanner manually to verify shared imports work

**Interfaces:**
- Consumes: ROOT (repository root path, already set on line 89)
- Produces: sys.path includes ROOT, allowing all subprocesses to import shared

**Steps:**

- [ ] **Step 1: Read orchestrator.py to understand current sys.path setup**

Read lines 89-106 of `orchestrator.py`. Current code sets ROOT but only adds to sys.path after orchestrator.py imports. Suites spawned as subprocesses won't have this path.

- [ ] **Step 2: Add PYTHONPATH to env dict in run_suite()**

Find the line in `run_suite()` that reads `env = os.environ.copy()` (search for this string). Immediately after this line, add:

```python
env = os.environ.copy()
# NEW: Ensure shared module is in PYTHONPATH for all child suites
env['PYTHONPATH'] = f"{ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
```

This ensures PYTHONPATH is explicitly set in the environment dict passed to subprocess.run(), guaranteeing child processes (sentiment-scanner, Vol_Suite, etc.) inherit it.

- [ ] **Step 3: Test sentiment-scanner import path**

Run manually from the FinancialDevelopment root:

```bash
cd C:\Users\bottl\FinancialDevelopment
python sentiment-scanner/main.py --no-loop --export-context /tmp/test_sentiment.json
```

Expected: Should complete with exit code 0 and WITHOUT "ModuleNotFoundError: No module named 'shared'"

Verify exit code:

```bash
echo $LASTEXITCODE  # Windows PowerShell
# or
echo $?  # Windows cmd.exe
```

If exit code != 0 but "ModuleNotFoundError" is NOT in stderr, the import fix worked but sentiment-scanner failed for other reasons (data, network, etc.) — that's OK for this task.

If "ModuleNotFoundError" still appears, add diagnostic: insert before the subprocess call in orchestrator.py (search for `subprocess.run(`):

```python
print(f"[DEBUG] PYTHONPATH={env.get('PYTHONPATH')}")
print(f"[DEBUG] ROOT={ROOT}")
```

Then re-run and confirm ROOT appears in PYTHONPATH.

- [ ] **Step 4: Verify no regression on other suites**

Run the full orchestrator with NVDA:

```bash
python orchestrator.py --unified --ticker NVDA
```

All four suites should reach at least the validation stage (even if some fail for data reasons, they shouldn't fail on import).

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py
git commit -m "fix: add PYTHONPATH setup so shared module is discoverable in child suite processes"
```

---

## Task 2: Fix Options Suite — Verify & Diagnose ThetaData Credentials

**Files:**
- Modify: `Options_Suite/main.py:1-50` (add credential check at startup)
- Modify: `Options_Suite/thetadata_controller.py` (add error handling)
- Test: Manually test ThetaData authentication

**Interfaces:**
- Consumes: ThetaData credentials (environment variables or config)
- Produces: Clear error message if credentials missing/invalid, before attempting data fetch

**Steps:**

- [ ] **Step 1: Find where ThetaData credentials are loaded**

Read `Options_Suite/main.py` and search for ThetaDataController initialization. Check how credentials are passed (env var, config file, etc.).

Expected location: somewhere near line 50-100 in main.py (based on grep output showing ThetaDataController import).

- [ ] **Step 2: Add credential validation at startup**

In `Options_Suite/main.py`, before any ThetaData call, add:

```python
import os

# Check ThetaData credentials early, fail fast
# ThetaData uses CF Access (Cloudflare) authentication, not a simple API key
CLIENT_ID = os.environ.get('THETADATA_CF_ACCESS_CLIENT_ID')
CLIENT_SECRET = os.environ.get('THETADATA_CF_ACCESS_CLIENT_SECRET')

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: ThetaData CF Access credentials not set.")
    print("Required environment variables:")
    print("  - THETADATA_CF_ACCESS_CLIENT_ID")
    print("  - THETADATA_CF_ACCESS_CLIENT_SECRET")
    print("Set these variables and retry. Credentials can be in .env or environment.")
    sys.exit(1)
```

- [ ] **Step 3: Check if THETADATA_API_KEY is set**

Run from command line:

```bash
echo %THETADATA_API_KEY%
```

(Windows) or:

```bash
echo $THETADATA_API_KEY
```

(Mac/Linux)

If empty, you need to set it. Ask where credentials come from (user account, .env file, etc.).

**If credentials are in a .env file:**

- [ ] **Step 4a: Load .env if it exists**

Modify `Options_Suite/main.py` to load from .env:

```python
from dotenv import load_dotenv
load_dotenv()  # Load THETADATA_API_KEY from .env if present
```

- [ ] **Step 4b: Test Options_Suite directly**

```bash
cd Options_Suite
python main.py --context suite_context.json --context-out options_result.json
```

Expected: Should either succeed (if credentials valid) or show clear error about missing credentials.

- [ ] **Step 5: Update orchestrator.py to pass THETADATA_API_KEY to Options_Suite**

In orchestrator.py's `run_suite()` function (around line 610), ensure:

```python
env = os.environ.copy()
# ... existing env setup ...
# Ensure ThetaData credentials are available to child
if 'THETADATA_API_KEY' in os.environ:
    env['THETADATA_API_KEY'] = os.environ['THETADATA_API_KEY']
```

- [ ] **Step 6: Commit**

```bash
git add Options_Suite/main.py orchestrator.py
git commit -m "fix: add ThetaData credential validation and env variable pass-through in orchestrator"
```

---

## Task 3: Create PDF Report Generator Module

**Files:**
- Create: `report_generator.py` (new module, root level)
- Create: `tests/test_report_generator.py` (tests)
- Modify: `orchestrator.py:905-907` (call report generator after run_unified completes)

**Interfaces:**
- Consumes: 
  - `orchestrator_output/<run_id>/suite_context.json` (focus data: ticker, expiry, strike, etc.)
  - `orchestrator_output/<run_id>/vol_result.json` (vol surface, dealer positioning, gamma)
  - `orchestrator_output/<run_id>/options_result.json` (options greeks, pricing)
  - `orchestrator_output/<run_id>/var_result.json` (Value at Risk simulations)
  - `orchestrator_output/<run_id>/sentiment_*.json` (if sentiment ran successfully)
- Produces: `orchestrator_output/<run_id>/analysis_report.pdf` (consolidated PDF)

**Steps:**

- [ ] **Step 1: Verify reportlab compatibility**

Check if reportlab is already installed:

```bash
pip show reportlab
```

Expected: reportlab 5.0.0 (or later) is already installed. The API for SimpleDocTemplate, Platypus, and TableStyle is compatible with version 5.0.0+.

If reportlab is NOT installed, add to `requirements.txt`:

```
reportlab>=5.0.0
```

Then install:

```bash
pip install reportlab
```

Note: The code in Step 2 is written for reportlab 5.0.0+. If you have 4.x, upgrade with `pip install --upgrade reportlab`.

- [ ] **Step 2: Create report_generator.py skeleton**

Create file `report_generator.py`:

```python
"""
PDF Report Generator for Orchestrator Outputs

Consolidates vol surface, options analytics, VaR results, and sentiment data
into a single professional PDF report.
"""

from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.units import inch
import json
import sys


def generate_pdf_report(output_dir: str, run_id: str) -> str:
    """
    Generate a consolidated PDF report from orchestrator output files.
    
    Args:
        output_dir: Path to orchestrator_output/<run_id> directory
        run_id: Run ID (e.g., "20260730T085905Z")
    
    Returns:
        Path to generated PDF file
    
    Raises:
        FileNotFoundError: If required output files are missing
    """
    output_path = Path(output_dir)
    if not output_path.exists():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")
    
    pdf_path = output_path / "analysis_report.pdf"
    
    # Read input files
    context = _load_json(output_path / "suite_context.json")
    vol_result = _load_json(output_path / "vol_result.json", required=False)
    options_result = _load_json(output_path / "options_result.json", required=False)
    var_result = _load_json(output_path / "var_result.json", required=False)
    sentiment_result = _load_json(output_path / "sentiment_result.json", required=False)
    
    # Create PDF
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter)
    story = []
    styles = getSampleStyleSheet()
    
    # Title page
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=30,
        alignment=1  # Center
    )
    
    focus = context.get('focus', {})
    ticker = focus.get('ticker', 'UNKNOWN')
    expiry = focus.get('expiration_date', 'N/A')
    
    story.append(Paragraph(f"Options Analysis Report: {ticker}", title_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Run metadata
    meta_data = [
        ['Run ID:', run_id],
        ['Ticker:', ticker],
        ['Expiration:', expiry],
        ['Option Type:', focus.get('option_type', 'call').upper()],
        ['Strike:', focus.get('strike', 'ATM')],
    ]
    meta_table = Table(meta_data, colWidths=[1.5*inch, 3*inch])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.5*inch))
    
    # Vol Summary (if available)
    if vol_result:
        story.append(Paragraph("Volatility & Dealer Positioning", styles['Heading2']))
        vol_status = vol_result.get('status', 'unknown')
        story.append(Paragraph(f"<b>Status:</b> {vol_status}", styles['Normal']))
        story.append(Spacer(1, 0.2*inch))
    
    # Options Summary (if available)
    if options_result:
        story.append(PageBreak())
        story.append(Paragraph("Options Analysis", styles['Heading2']))
        opt_status = options_result.get('status', 'unknown')
        story.append(Paragraph(f"<b>Status:</b> {opt_status}", styles['Normal']))
        story.append(Spacer(1, 0.2*inch))
    
    # VaR Summary (if available)
    if var_result:
        story.append(PageBreak())
        story.append(Paragraph("Value at Risk Analysis", styles['Heading2']))
        var_status = var_result.get('status', 'unknown')
        story.append(Paragraph(f"<b>Status:</b> {var_status}", styles['Normal']))
        story.append(Spacer(1, 0.2*inch))
    
    # Log completeness warning if few result files found
    result_files_found = sum([bool(vol_result), bool(options_result), bool(var_result)])
    if result_files_found < 2:
        print(f"[report] WARNING: Only {result_files_found} suite result(s) loaded. Expected at least 2.")
        print(f"[report] Check orchestrator logs to see which suites failed.")
        story.append(Spacer(1, 0.3*inch))
        story.append(Paragraph(
            f"<b>Report Completeness:</b> {result_files_found} of 3 expected suite results loaded.",
            styles['Normal']
        ))
    
    # Build PDF
    doc.build(story)
    
    return str(pdf_path)


def _load_json(file_path: Path, required: bool = True) -> dict:
    """Load JSON file, distinguish between missing files and parse errors."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        if required:
            raise FileNotFoundError(f"Required file not found: {file_path}")
        else:
            print(f"Info: Optional file not found: {file_path}")
            return {}
    except json.JSONDecodeError as e:
        print(f"ERROR: Malformed JSON in {file_path}: {e}")
        print(f"       File exists but is not valid JSON. Check for syntax errors.")
        return {}
    except Exception as e:
        print(f"ERROR: Could not read {file_path}: {e}")
        return {}


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python report_generator.py <output_dir> <run_id>")
        sys.exit(1)
    
    output_dir = sys.argv[1]
    run_id = sys.argv[2]
    
    pdf_path = generate_pdf_report(output_dir, run_id)
    print(f"Report generated: {pdf_path}")
```

- [ ] **Step 3: Test report generator with existing output**

Run with the NVDA run you just completed:

```bash
python report_generator.py "orchestrator_output/20260730T085905Z" "20260730T085905Z"
```

Expected: Creates `orchestrator_output/20260730T085905Z/analysis_report.pdf`

Check that file exists:

```bash
ls -lh orchestrator_output/20260730T085905Z/analysis_report.pdf
```

- [ ] **Step 4: Expand report generator with actual data sections**

**Minimum viable report:** Title, metadata (ticker, run_id, expiry), and status of each suite (completed in Step 2).

**Enhanced report (this step):** Add one data section per suite, prioritized:

1. **Vol Surface** (required if vol_result.json exists):
   - Summary: ATM implied volatility, term structure (30d, 60d, 90d IV)
   - Dealer positioning delta/gamma exposure
   
2. **Options Greeks** (if options_result.json exists):
   - Table: strike, call delta, call gamma, put vega, theta
   
3. **VaR Analysis** (if var_result.json exists):
   - Summary: 1-day VaR at 99% confidence, 95% confidence
   - Brief interpretation
   
4. **Sentiment** (if sentiment_result.json exists):
   - Score and key narrative themes (if available)

Update `generate_pdf_report()` function:

```python
def _add_vol_section(story, vol_result, output_dir, styles):
    """Add volatility section if available."""
    if not vol_result or vol_result.get('status') != 'ok':
        story.append(Paragraph("Volatility Suite: <b>NOT COMPLETED</b>", styles['Heading3']))
        return
    
    story.append(Paragraph("Volatility Surface & Dealer Positioning", styles['Heading2']))
    
    # Extract vol surface data (if exists)
    vol_data = vol_result.get('vol_surface', {})
    if vol_data:
        atm_iv = vol_data.get('atm_iv', 'N/A')
        story.append(Paragraph(f"<b>ATM Implied Volatility:</b> {atm_iv}", styles['Normal']))
    
    # Dealer positioning (if exists)
    positioning = vol_result.get('dealer_positioning', {})
    if positioning:
        story.append(Paragraph(f"<b>Dealer Delta:</b> {positioning.get('delta', 'N/A')}", styles['Normal']))
    
    story.append(Spacer(1, 0.3*inch))
```

Focus on Steps 1-2 first; Step 4 enhancements can be added incrementally as suite outputs stabilize.

- [ ] **Step 5: Integrate report generator into orchestrator.py**

In the `run_unified()` function, find the line `return combined` (search for this text). BEFORE this return statement, add:

```python
    # Generate PDF report (before returning combined)
    try:
        from report_generator import generate_pdf_report
        pdf_path = generate_pdf_report(output_dir, combined['run_id'])
        print(f"\n[unified] PDF Report: {pdf_path}")
    except Exception as e:
        print(f"[unified] WARNING: Could not generate PDF report: {e}")
    
    return combined  # <- PDF generation happens BEFORE this return
```

**Critical:** The PDF generation call MUST come BEFORE `return combined`, not after. Code after `return` is unreachable.

- [ ] **Step 6: Test full orchestrator with report generation**

Run:

```bash
python orchestrator.py --unified --ticker TSLA
```

After completion, check for:
- `orchestrator_output/<run_id>/analysis_report.pdf` (should exist)
- Open PDF in a reader to verify it contains the run summary

- [ ] **Step 7: Write comprehensive tests for report generator**

Create `tests/test_report_generator.py`. Note: Requires `PyPDF2` for PDF validation. Add to requirements-dev.txt:

```
PyPDF2>=3.0.0
```

Then write tests:

```python
import json
import tempfile
from pathlib import Path
from report_generator import generate_pdf_report
from PyPDF2 import PdfReader


def test_generate_pdf_report_happy_path():
    """Test PDF report generation with minimal mock data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create minimal suite context
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
        
        # Generate report
        pdf_path = generate_pdf_report(str(tmppath), "TEST20260730T000000Z")
        
        # Verify PDF was created and is valid
        assert Path(pdf_path).exists(), f"PDF not created at {pdf_path}"
        assert Path(pdf_path).stat().st_size > 0, "PDF file is empty"
        
        # Verify PDF is valid and readable
        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 0, "PDF has no pages"


def test_generate_pdf_report_with_vol_data():
    """Test PDF report includes vol suite data when available."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create context
        context = {"focus": {"ticker": "TEST", "expiration_date": "2026-10-16"}}
        with open(tmppath / "suite_context.json", 'w') as f:
            json.dump(context, f)
        
        # Create vol result
        vol_result = {"status": "ok", "suite": "vol", "vol_surface": {"atm_iv": 0.25}}
        with open(tmppath / "vol_result.json", 'w') as f:
            json.dump(vol_result, f)
        
        # Generate report
        pdf_path = generate_pdf_report(str(tmppath), "TEST20260730T000000Z")
        
        # Verify PDF is valid
        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 0


def test_generate_pdf_report_missing_optional_files():
    """Test PDF generation with only required files (suite_context.json)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create ONLY suite_context.json (no suite results)
        context = {"focus": {"ticker": "TEST", "expiration_date": "2026-10-16"}}
        with open(tmppath / "suite_context.json", 'w') as f:
            json.dump(context, f)
        
        # Should still generate PDF (with warning)
        pdf_path = generate_pdf_report(str(tmppath), "TEST20260730T000000Z")
        assert Path(pdf_path).exists()
        
        # Verify PDF is valid
        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 0


def test_generate_pdf_report_malformed_json():
    """Test error handling for malformed JSON in result files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create valid context
        context = {"focus": {"ticker": "TEST", "expiration_date": "2026-10-16"}}
        with open(tmppath / "suite_context.json", 'w') as f:
            json.dump(context, f)
        
        # Create INVALID JSON in optional result file
        with open(tmppath / "vol_result.json", 'w') as f:
            f.write("{INVALID JSON}")
        
        # Should still generate PDF (malformed file is skipped)
        pdf_path = generate_pdf_report(str(tmppath), "TEST20260730T000000Z")
        assert Path(pdf_path).exists()
        reader = PdfReader(pdf_path)
        assert len(reader.pages) > 0
```

Run tests:

```bash
pip install PyPDF2
pytest tests/test_report_generator.py -v
```

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add report_generator.py tests/test_report_generator.py orchestrator.py requirements.txt
git commit -m "feat: add PDF report generator to consolidate orchestrator outputs"
```

---

## Summary

After all tasks complete:
1. ✅ Sentiment suite will find shared module (sys.path fix)
2. ✅ Options suite will fail fast on missing ThetaData credentials
3. ✅ Each orchestrator run produces a consolidated PDF report at `orchestrator_output/<run_id>/analysis_report.pdf`

**Execution: Subagent-Driven Development (recommended)**

Would you like me to use subagent-driven-development to execute these three tasks in parallel with reviews, or would you prefer inline execution with executing-plans?
