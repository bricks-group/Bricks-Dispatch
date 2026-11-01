"""Owner notification for an automatic applicant review: HTML + text + SMS.

House style: Bricks mark + wordmark on paper, ink text, brick #D2542B as the
only accent, status green/amber/red only for status, 2px corners, no pills.
Tables + inline CSS so it holds up in Gmail, Apple Mail/iCloud and Outlook,
and at 375px. Never names the model or any internal agent.
"""

from __future__ import annotations

import html as _html
import os
from typing import Optional

from .applicant_evidence import SOURCE_LABEL, COULDNT_CHECK

OS_BASE = (os.environ.get("BRICKS_OS_URL") or "https://app.brxs.ca").rstrip("/")
LOGO_URL = "https://www.brxs.ca/bricks-mark.png"

SANS = "'General Sans',Helvetica,Arial,sans-serif"
MONO = "'Ioskeley Mono','SFMono-Regular',Menlo,Consolas,'Courier New',monospace"
PAPER, PAPER_SOFT, PAPER_DEEP = "#F4F1EB", "#EDE8DF", "#E4DED2"
INK, INK_SOFT, INK_MUTE, INK_FAINT = "#14110E", "#3B332A", "#6B6054", "#A39684"
BRICK, BRICK_DEEP = "#D2542B", "#A8401F"
STATUS = {
    "green": {"fg": "#4A6B3C", "tint": "#E6ECDD", "word": "Good"},
    "yellow": {"fg": "#B8741F", "tint": "#F6E8D1", "word": "Check"},
    "red": {"fg": "#8C2F24", "tint": "#F1DAD4", "word": "Weak"},
    "grey": {"fg": "#6B6054", "tint": "#EDE8DF", "word": "Missing"},
}
VERDICT_BAND = {
    "green": "Recommend",
    "yellow": "Needs review",
    "red": "Not a fit",
    "grey": "Incomplete",
}
PART_ORDER = ("samples", "gear", "availability", "location", "portfolio", "drone", "resume")
SPECIALTY_LABEL = {
    "photo": "Photos", "video": "Video", "drone": "Drone", "virtual_tour": "Virtual tour",
    "virtual_visit": "Virtual visit", "floor_plan": "Floor plans", "twilight": "Twilight",
}


def e(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def os_link(member_id: str, action: str = "") -> str:
    url = f"{OS_BASE}/hiring/{member_id}"
    return url + (f"?action={action}" if action else "")


def band_title(review: dict) -> str:
    colour = review.get("colour") or "yellow"
    if colour == "grey":
        return f"Incomplete · {review.get('verdict_label') or 'Needs review'}"
    return review.get("verdict_label") or VERDICT_BAND.get(colour, "Needs review")


def subject(name: str, review: dict) -> str:
    who = (name or "").strip() or "Applicant"
    return f"{band_title(review)} · {review.get('score', 0)}/100 · {who} applied to Bricks"


def _specialties(specs) -> str:
    out = [SPECIALTY_LABEL.get(str(s).lower(), str(s).replace("_", " ").capitalize()) for s in (specs or []) if s]
    return ", ".join(out) or "—"


def _tag(source: str) -> str:
    label = SOURCE_LABEL.get(source, "Self-reported").upper()
    colour = {"verified": STATUS["green"]["fg"], "couldnt_check": INK_MUTE}.get(source, INK_SOFT)
    return (
        f'<span style="display:inline-block;font-family:{MONO};font-size:9px;line-height:12px;letter-spacing:1px;'
        f'color:{colour};border:1px solid {colour};border-radius:2px;padding:1px 5px;white-space:nowrap;">{e(label)}</span>'
    )


def _square(colour: str, size: int = 10) -> str:
    fg = STATUS.get(colour, STATUS["grey"])["fg"]
    return (
        f'<span style="display:inline-block;width:{size}px;height:{size}px;background:{fg};border-radius:2px;'
        f'vertical-align:middle;font-size:0;line-height:0;">&nbsp;</span>'
    )


def _section_label(text: str) -> str:
    return (
        f'<tr><td style="padding:22px 24px 8px 24px;font-family:{MONO};font-size:10px;letter-spacing:1.4px;'
        f'color:{BRICK_DEEP};text-transform:uppercase;">{e(text)}</td></tr>'
    )


def render_html(
    *,
    member_id: str,
    name: str,
    city: str,
    specialties,
    found_via: str,
    review: dict,
    thumb_urls: list,
    resume_url: str | None = None,
) -> str:
    colour = review.get("colour") or "yellow"
    st = STATUS.get(colour, STATUS["yellow"])
    score = int(review.get("score") or 0)
    cap_note = ""
    if review.get("incomplete"):
        cap_note = f"Capped at {review.get('score_cap') or 60} until samples are reviewed."
    parts = review.get("parts") or {}

    # per-part R/Y/G bar (labels + status word, never colour alone)
    cells = []
    for key in PART_ORDER:
        p = parts.get(key)
        if not p:
            continue
        pst = STATUS.get(p.get("status"), STATUS["grey"])
        cells.append(
            f'<div style="display:inline-block;vertical-align:top;width:31%;min-width:150px;margin:0 2% 8px 0;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">'
            f'<tr><td width="4" bgcolor="{pst["fg"]}" style="width:4px;background:{pst["fg"]};border-radius:2px 0 0 2px;font-size:0;line-height:0;">&nbsp;</td>'
            f'<td bgcolor="#FFFFFF" style="background:#FFFFFF;padding:6px 8px;border:1px solid {PAPER_DEEP};border-left:none;border-radius:0 2px 2px 0;">'
            f'<div style="font-family:{SANS};font-size:12px;line-height:16px;color:{INK};">{e(p.get("label") or key)}</div>'
            f'<div style="font-family:{MONO};font-size:10px;line-height:14px;letter-spacing:.6px;color:{pst["fg"]};">'
            f'{e(pst["word"].upper())} · {e(p.get("points", 0))}/{e(p.get("max_points", 0))}</div>'
            f'</td></tr></table></div>'
        )
    part_bar = "".join(cells)

    # thumbnails
    thumbs_html = ""
    if thumb_urls:
        tds = []
        for t in thumb_urls[:3]:
            tds.append(
                f'<td width="33%" valign="top" style="width:33%;padding:0 4px;">'
                f'<a href="{e(t["url"])}" target="_blank" style="text-decoration:none;">'
                f'<img src="{e(t["url"])}" width="176" alt="{e(t.get("label") or "Sample")} sample" '
                f'style="display:block;width:100%;max-width:176px;height:auto;border:1px solid {PAPER_DEEP};border-radius:2px;"></a>'
                f'<div style="font-family:{MONO};font-size:9px;letter-spacing:1px;color:{INK_MUTE};padding-top:5px;text-transform:uppercase;">{e(t.get("label") or "")}</div>'
                f'</td>'
            )
        while len(tds) < 3:
            tds.append('<td width="33%" style="width:33%;padding:0 4px;">&nbsp;</td>')
        thumbs_html = (
            _section_label("Sample photos")
            + f'<tr><td style="padding:0 20px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>{"".join(tds)}</tr></table></td></tr>'
        )
    elif review.get("needs_samples"):
        thumbs_html = (
            _section_label("Sample photos")
            + f'<tr><td style="padding:0 24px;font-family:{SANS};font-size:13px;line-height:19px;color:{INK_MUTE};">No sample photos to show. Needs samples.</td></tr>'
        )

    # rubric
    crit_rows = []
    vision_ran = review.get("vision_ran")
    for key, c in (review.get("criteria") or {}).items():
        cst = STATUS.get(c.get("status"), STATUS["grey"])
        crit_rows.append(
            f'<tr><td valign="top" width="16" style="padding:7px 0 7px 0;width:16px;">{_square(c.get("status"))}</td>'
            f'<td valign="top" style="padding:5px 8px 5px 6px;font-family:{SANS};font-size:13px;line-height:19px;color:{INK};">'
            f'{e(c.get("label"))} <span style="font-family:{MONO};font-size:10px;letter-spacing:.6px;color:{cst["fg"]};">{e(cst["word"].upper())}</span>'
            f'<div style="color:{INK_MUTE};font-size:12px;line-height:17px;">{e(c.get("words"))}</div></td></tr>'
        )
    rubric_html = ""
    if vision_ran and crit_rows:
        rubric_html = (
            _section_label("Rubric")
            + f'<tr><td style="padding:0 24px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{"".join(crit_rows)}</table></td></tr>'
        )
    else:
        rubric_html = (
            _section_label("Rubric")
            + f'<tr><td style="padding:0 24px;font-family:{SANS};font-size:13px;line-height:19px;color:{INK_MUTE};">Not run: no sample photos were reviewed.</td></tr>'
        )

    # evidence + flags
    ev_rows = []
    for b in list(review.get("flags") or []) + list(review.get("evidence") or []):
        ev_rows.append(
            f'<tr><td valign="top" width="112" style="width:112px;padding:6px 8px 6px 0;">{_tag(b.get("source"))}</td>'
            f'<td valign="top" style="padding:4px 0;font-family:{SANS};font-size:13px;line-height:19px;color:{INK};">{e(b.get("text"))}</td></tr>'
        )
    missing = review.get("missing") or []
    missing_html = ""
    if missing:
        items = "".join(
            f'<tr><td valign="top" width="14" style="width:14px;padding:4px 0;font-family:{MONO};font-size:12px;color:{BRICK_DEEP};">–</td>'
            f'<td style="padding:4px 0;font-family:{SANS};font-size:13px;line-height:19px;color:{INK};">{e(m)}</td></tr>'
            for m in missing
        )
        missing_html = _section_label("Still missing") + f'<tr><td style="padding:0 24px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{items}</table></td></tr>'

    reasons = review.get("verdict_reasons") or []
    reasons_html = ""
    if reasons:
        items = "".join(
            f'<tr><td valign="top" width="14" style="width:14px;padding:4px 0;font-family:{MONO};font-size:12px;color:{BRICK_DEEP};">–</td>'
            f'<td style="padding:4px 0;font-family:{SANS};font-size:13px;line-height:19px;color:{INK};">{e(r)}</td></tr>'
            for r in reasons
        )
        reasons_html = _section_label("Why") + f'<tr><td style="padding:0 24px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{items}</table></td></tr>'
    resume_html = _section_label("Resume") + (
        f'<tr><td style="padding:0 24px;font-family:{SANS};font-size:13px;line-height:19px;color:{INK};">'
        f'<a href="{e(resume_url)}" target="_blank" style="color:{BRICK_DEEP};">Download resume</a>'
        f' <span style="color:{INK_MUTE};">(link works for 7 days)</span></td></tr>'
        if resume_url else
        f'<tr><td style="padding:0 24px;font-family:{SANS};font-size:13px;line-height:19px;color:{INK_MUTE};">No resume on file.</td></tr>'
    )

    os_url, approve_url = os_link(member_id), os_link(member_id, "approve")
    avail = review.get("availability_summary") or "—"
    title = band_title(review)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">
<title>{e(subject(name, review))}</title>
<style>
:root{{color-scheme:light only;supported-color-schemes:light only;}}
@media (max-width:480px){{ .px{{padding-left:16px!important;padding-right:16px!important;}} .score{{font-size:30px!important;}} .btn td{{display:block!important;}} }}
</style></head>
<body style="margin:0;padding:0;background:{PAPER};" bgcolor="{PAPER}">
<div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;color:{PAPER};">{e(title)} · {score}/100 · {e(name)} · {e(city)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" style="background:{PAPER};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;border-collapse:separate;">
<tr><td style="padding:0 4px 14px 4px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
  <td width="18" style="width:18px;"><img src="{LOGO_URL}" width="18" height="18" alt="" style="display:block;width:18px;height:18px;border:0;"></td>
  <td style="padding-left:7px;font-family:{SANS};font-size:16px;font-weight:500;letter-spacing:-0.5px;color:{INK};">bricks</td>
  <td align="right" style="font-family:{MONO};font-size:10px;letter-spacing:1.4px;color:{INK_MUTE};">APPLICANT REVIEW</td>
  </tr></table>
</td></tr>
<tr><td bgcolor="#FFFFFF" style="background:#FFFFFF;border:1px solid {PAPER_DEEP};border-radius:2px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<!-- verdict band -->
<tr><td bgcolor="{st['tint']}" style="background:{st['tint']};border-left:4px solid {st['fg']};border-radius:2px 2px 0 0;padding:18px 24px 14px 20px;" class="px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
  <td valign="top">
    <div style="font-family:{MONO};font-size:10px;letter-spacing:1.4px;color:{st['fg']};">{_square(colour, 9)}&nbsp; VERDICT</div>
    <div style="font-family:{SANS};font-size:24px;line-height:30px;font-weight:600;letter-spacing:-0.4px;color:{INK};padding-top:4px;">{e(title)}</div>
  </td>
  <td valign="top" align="right" style="white-space:nowrap;">
    <div class="score" style="font-family:{MONO};font-size:36px;line-height:40px;color:{st['fg']};">{score}<span style="font-size:14px;color:{INK_MUTE};">/100</span></div>
  </td></tr></table>
  {f'<div style="font-family:{SANS};font-size:12px;line-height:17px;color:{INK_SOFT};padding-top:2px;">{e(cap_note)}</div>' if cap_note else ''}
  <div style="padding-top:12px;font-size:0;line-height:0;">{part_bar}</div>
</td></tr>
<!-- applicant -->
<tr><td style="padding:20px 24px 0 24px;" class="px">
  <div style="font-family:{SANS};font-size:22px;line-height:28px;font-weight:500;letter-spacing:-0.4px;color:{INK};">{e(name or 'Applicant')}</div>
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:8px;">
  {''.join(f'<tr><td width="104" valign="top" style="width:104px;padding:3px 0;font-family:{MONO};font-size:10px;letter-spacing:1px;color:{INK_MUTE};text-transform:uppercase;">{e(k)}</td><td style="padding:2px 0;font-family:{SANS};font-size:13px;line-height:19px;color:{INK};">{e(v)}</td></tr>' for k, v in (("City", city or "—"), ("Specialty", _specialties(specialties)), ("Found us via", found_via or "—"), ("Availability", avail)))}
  </table>
</td></tr>
{reasons_html}
{thumbs_html}
{resume_html}
{rubric_html}
{_section_label("Evidence")}
<tr><td style="padding:0 24px;" class="px"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{''.join(ev_rows)}</table></td></tr>
{missing_html}
<!-- actions -->
<tr><td style="padding:24px 24px 22px 24px;" class="px">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" class="btn"><tr>
  <td bgcolor="{BRICK}" style="background:{BRICK};border-radius:2px;"><a href="{e(os_url)}" target="_blank" style="display:inline-block;padding:13px 20px;font-family:{SANS};font-size:14px;font-weight:600;color:#FFFFFF;text-decoration:none;border-radius:2px;">Open in Bricks OS</a></td>
  <td width="10" style="width:10px;font-size:0;">&nbsp;</td>
  <td bgcolor="#FFFFFF" style="background:#FFFFFF;border:1px solid {INK};border-radius:2px;"><a href="{e(approve_url)}" target="_blank" style="display:inline-block;padding:12px 18px;font-family:{SANS};font-size:14px;font-weight:600;color:{INK};text-decoration:none;border-radius:2px;">Review &amp; approve</a></td>
  </tr></table>
  <div style="font-family:{SANS};font-size:12px;line-height:17px;color:{INK_MUTE};padding-top:12px;">Approve and reject stay manual in Bricks OS. Nothing here approved or rejected anyone.</div>
</td></tr>
</table></td></tr>
<tr><td style="padding:14px 4px 0 4px;font-family:{SANS};font-size:11px;line-height:16px;color:{INK_MUTE};">
Tags: <b style="font-weight:600;">Verified</b> = Bricks looked at it. <b style="font-weight:600;">Self-reported</b> = the applicant typed it. <b style="font-weight:600;">Couldn't check</b> = Bricks tried and couldn't look.<br>Bricks · brxs.ca
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def render_text(*, member_id: str, name: str, city: str, specialties, found_via: str, review: dict, thumb_urls: list,
                resume_url: str | None = None) -> str:
    lines = [
        f"{band_title(review)} · {review.get('score', 0)}/100",
    ]
    for r in review.get("verdict_reasons") or []:
        lines.append(f"Why: {r}")
    if review.get("incomplete"):
        lines.append(f"Score capped at {review.get('score_cap') or 60} until samples are reviewed.")
    parts = review.get("parts") or {}
    bar = []
    for key in PART_ORDER:
        p = parts.get(key)
        if p:
            bar.append(f"{p.get('label')}: {STATUS.get(p.get('status'), STATUS['grey'])['word']} ({p.get('points')}/{p.get('max_points')})")
    if bar:
        lines.append(" | ".join(bar))
    lines += [
        "",
        f"{name or 'Applicant'}",
        f"City: {city or '—'}",
        f"Specialty: {_specialties(specialties)}",
        f"Found us via: {found_via or '—'}",
        f"Availability: {review.get('availability_summary') or '—'}",
    ]
    if thumb_urls:
        lines += ["", "Sample photos:"] + [f"- {t.get('label')}: {t['url']}" for t in thumb_urls[:3]]
    lines += ["", f"Resume: {resume_url}" if resume_url else "Resume: none on file."]
    if review.get("vision_ran"):
        lines += ["", "Rubric:"] + [f"- {c['label']}: {c['words']}" for c in (review.get("criteria") or {}).values()]
    else:
        lines += ["", "Rubric: not run (no sample photos were reviewed)."]
    lines += ["", "Evidence:"]
    for b in list(review.get("flags") or []) + list(review.get("evidence") or []):
        lines.append(f"- [{SOURCE_LABEL.get(b.get('source'), 'Self-reported')}] {b.get('text')}")
    if review.get("missing"):
        lines += ["", "Still missing:"] + [f"- {m}" for m in review["missing"]]
    lines += ["", f"Open in Bricks OS: {os_link(member_id)}", "Approve and reject stay manual in Bricks OS."]
    return "\n".join(lines)


def render_sms(*, member_id: str, name: str, city: str, review: dict) -> str:
    """Short owner text. No model or agent name."""
    who = (name or "").strip() or "Someone"
    bits = [f"Bricks applicant: {who}, {city or 'no city'}. {band_title(review)} {review.get('score', 0)}/100."]
    top = None
    for b in list(review.get("flags") or []) + [x for x in review.get("evidence") or [] if x.get("ok") is False]:
        top = b.get("text")
        break
    if review.get("needs_samples"):
        top = "Needs samples."
    if top:
        bits.append(top if len(top) <= 90 else top[:89].rstrip() + "…")
    bits.append(os_link(member_id).replace("https://", ""))
    return " ".join(bits)
