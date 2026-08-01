from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import inch
import json
import sys

def generate_pdf_report(output_dir: str, run_id: str) -> str:
    output_path = Path(output_dir)
    if not output_path.exists():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")

    pdf_path = output_path / "analysis_report.pdf"

    # Read input files
    context = _load_json(output_path / "suite_context.json")
    vol_result = _load_json(output_path / "vol_result.json", required=False)
    options_result = _load_json(output_path / "options_result.json", required=False)
    var_result = _load_json(output_path / "var_result.json", required=False)

    # Create PDF
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter)
    story = []
    styles = getSampleStyleSheet()

    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=30,
        alignment=1
    )

    focus = context.get('focus', {})
    ticker = focus.get('ticker', 'UNKNOWN')
    expiry = focus.get('expiration_date', 'N/A')

    story.append(Paragraph(f"Options Analysis Report: {ticker}", title_style))
    story.append(Spacer(1, 0.3*inch))

    # Metadata table
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

    # Completeness warning
    result_files_found = sum([bool(vol_result), bool(options_result), bool(var_result)])
    if result_files_found < 2:
        print(f"[report] WARNING: Only {result_files_found} suite result(s) loaded.")
        story.append(Paragraph(
            f"<b>Report Completeness:</b> {result_files_found} of 3 expected suite results loaded.",
            styles['Normal']
        ))

    # Build PDF
    doc.build(story)
    return str(pdf_path)

def _load_json(file_path: Path, required: bool = True) -> dict:
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
