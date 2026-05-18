def export_postmortem_markdown(incident):
    analysis = incident.analysis

    content = f"""# Incident Postmortem

## Incident Title
{incident.title}

## Summary
{analysis.postmortem}

## Metadata
- Severity: {incident.severity}
- Status: {incident.status}
- Confidence: {analysis.confidence_score:.2f}
"""

    filename = f"postmortem_{incident.id}.md"
    return filename, content

def export_postmortem_pdf(incident, file_path):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    analysis = incident.analysis
    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(file_path)
    story = []

    story.append(Paragraph("<b>Incident Postmortem</b>", styles["Title"]))
    story.append(Paragraph(f"<b>Title:</b> {incident.title}", styles["Normal"]))
    story.append(Paragraph(f"<b>Severity:</b> {incident.severity}", styles["Normal"]))
    story.append(Paragraph(f"<b>Status:</b> {incident.status}", styles["Normal"]))
    story.append(Paragraph(
        f"<b>Confidence:</b> {analysis.confidence_score:.2f}",
        styles["Normal"]
    ))
    story.append(Paragraph("<br/>", styles["Normal"]))

    for line in analysis.postmortem.split("\n"):
        story.append(Paragraph(line, styles["Normal"]))

    doc.build(story)
