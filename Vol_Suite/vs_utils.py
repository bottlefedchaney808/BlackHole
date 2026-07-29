import os
import glob
from datetime import datetime


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)
    return path


def timestamped_output_dir(base: str = "outputs") -> str:
    base = os.path.abspath(base)
    ensure_dir(base)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(base, ts)
    ensure_dir(out)
    return out


def collect_files(directory: str, patterns=None):
    if patterns is None:
        patterns = ["*.png", "*.pdf", "*.csv"]
    files = []
    for p in patterns:
        files.extend(sorted(glob.glob(os.path.join(directory, p))))
    return files


def images_to_pdf(image_paths, pdf_path):
    """Convert a list of image paths to a single PDF. Requires Pillow (PIL).
    """
    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError("Pillow is required to convert images to PDF. Install via `pip install pillow`.") from e
    if not image_paths:
        raise ValueError("No images to convert to PDF")
    imgs = []
    for p in image_paths:
        im = Image.open(p)
        if im.mode == 'RGBA':
            im = im.convert('RGB')
        imgs.append(im)
    first, rest = imgs[0], imgs[1:]
    first.save(pdf_path, save_all=True, append_images=rest)
    return pdf_path


def compose_pdf_report(output_pdf_path: str, sections: list, page_size=None):
    """Compose a multi-section PDF using ReportLab.

    sections: list of dicts {"title": str, "text": str, "images": [paths]}
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
    except Exception as e:
        raise RuntimeError("ReportLab is required to compose PDF reports. Install via `pip install reportlab`.") from e

    doc = SimpleDocTemplate(output_pdf_path, pagesize=page_size or letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    # Title page (centered)
    from reportlab.lib.enums import TA_CENTER
    title_style = styles['Title']
    title_style.alignment = TA_CENTER
    normal_center = styles['Normal'].clone('NormalCenter')
    normal_center.alignment = TA_CENTER
    # center content vertically
    story.append(Spacer(1, (doc.height * 0.25)))
    story.append(Paragraph("Volatility Suite Report", title_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_center))
    story.append(PageBreak())

    for sec in sections:
        title = Paragraph(sec.get('title', 'Section'), styles['Heading2'])
        story.append(title)
        story.append(Spacer(1, 6))
        text = sec.get('text', '')
        for para in text.split('\n'):
            story.append(Paragraph(para, styles['Normal']))
            story.append(Spacer(1, 6))
        imgs = sec.get('images', []) or []
        for img in imgs:
            try:
                im = RLImage(img)
                # scale to page width
                max_w = doc.width
                if im.drawWidth > max_w:
                    ratio = max_w / im.drawWidth
                    im.drawWidth = im.drawWidth * ratio
                    im.drawHeight = im.drawHeight * ratio
                story.append(im)
                story.append(Spacer(1, 12))
            except Exception:
                # skip images that cannot be loaded
                pass
        story.append(PageBreak())

    doc.build(story)
    return output_pdf_path
