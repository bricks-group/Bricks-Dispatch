"""Client delivery email — one template for every "your media is ready" send.

Used by:
  * client_comms.notify_photos_delivered  (post_production finalize: stills/video)
  * client_comms.notify_capture_delivered (capture compile + Virtual Visit deliver)
  * Companion /api/internal/comms/job-delivered fallback (Dispatch pre-renders
    subject/html/text and Companion sends them verbatim).

Rendering rules (email clients, Nov 2026):
  * Table layout + inline CSS, 600px max, fluid below that. No web-only layout.
  * Light + dark: color-scheme / supported-color-schemes meta, a
    prefers-color-scheme block (Apple Mail, iOS Mail, Outlook macOS, Samsung,
    Thunderbird) and [data-ogsc]/[data-ogsb] twins for Outlook.com. Gmail and
    Outlook Windows ignore all of that and force-invert; the light palette is
    chosen so the inversion lands on readable colours, and the logo is an
    orange mark on transparent + live text, so it reads on either background.
  * Motion is progressive: every animated element's resting style is fully
    visible, keyframes only *start* from hidden. Clients that drop @keyframes
    (Gmail, Outlook, Yahoo) render the static, complete layout. All motion sits
    inside prefers-reduced-motion: no-preference.
  * Hero: one plain <img> per state with an absolute public HTTPS URL to a
    static JPEG/GIF (no cid:, data:, SVG, CSS background or expiring signature)
    and explicit width/height. No tiles: the only photos shown are this job's
    own QC-passed stills (see delivery_email_media.gallery_preview).
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

try:  # Toronto is the business timezone for the "Delivered" date.
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("America/Toronto")
except Exception:  # noqa: BLE001 - tzdata missing: fall back to UTC
    _TZ = timezone.utc

LOGO_URL = "https://www.brxs.ca/bricks-mark.png"
FONT_BASE = "https://www.brxs.ca/fonts"
SUPPORT_EMAIL = "help@brxs.ca"
BRICK = "#D2542B"

FONT_STACK = (
    "'General Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif"
)
MONO_STACK = "'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

# www tokens (hello-www globals.css). Light is inline; dark lives in <style>.
L = {
    "page": "#F4F1EB", "card": "#FFFFFF", "ink": "#14110E", "soft": "#3B332A",
    "mute": "#6B6054", "hair": "#E4DED2", "chip": "#F4F1EB", "tile": "#EDE8DF",
}
D = {
    "page": "#141210", "card": "#1C1A17", "ink": "#F4F1EB", "soft": "#D6CFC2",
    "mute": "#A39684", "hair": "#2E2A25", "chip": "#23201C", "tile": "#23201C",
}

HERO_W, HERO_H = 600, 375          # 16:10 edge-to-edge hero


@dataclass
class PreviewStill:
    """One QC-passed still from this job, published at an absolute HTTPS URL."""

    tile_url: str = ""
    hero_url: str = ""
    alt: str = ""


@dataclass
class DeliveryEmailContext:
    recipient_name: str = ""
    address: str = ""
    primary_url: str = ""            # never-expiring share page (or desk fallback)
    desk_url: str = ""
    share_link: bool = False         # primary_url is the share page
    client: bool = False             # PM / commercial / investor account wording
    photo_count: int = 0
    services: Sequence[str] = ()     # ordered labels, e.g. ["24 photos", "Video"]
    stills: Sequence[PreviewStill] = ()
    hero_gif_url: str = ""           # optional crossfade; first frame == stills[0]
    headline_what: str = "photos"    # "photos" | "media" | "floor plan" | ...
    headline_verb: str = "are"
    button_label: str = "View your gallery"
    capture_note: str = ""           # extra sentence (capture deliveries)
    delivered_at: Optional[datetime] = None


@dataclass
class RenderedEmail:
    subject: str
    html: str
    text: str
    preheader: str = ""
    meta: dict = field(default_factory=dict)


def _e(v: object) -> str:
    return _html.escape(str(v or ""), quote=True)


def _date_label(dt: Optional[datetime]) -> str:
    d = (dt or datetime.now(timezone.utc)).astimezone(_TZ)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def subject_for(what: str, address: str, verb: str = "are") -> str:
    addr = (address or "").strip() or "your property"
    return f"Your {what} {verb} ready · {addr}"


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------

def _style_block() -> str:
    """Head CSS: fonts, mobile, dark mode, motion. Inline styles stay the source
    of truth for every client that drops this block."""
    dark_rules = f"""
  .bx-page{{background:{D['page']}!important}}
  .bx-card{{background:{D['card']}!important;border-color:{D['hair']}!important}}
  .bx-ink{{color:{D['ink']}!important}}
  .bx-soft{{color:{D['soft']}!important}}
  .bx-mute{{color:{D['mute']}!important}}
  .bx-hair{{border-color:{D['hair']}!important}}
  .bx-chip{{background:{D['chip']}!important;border-color:{D['hair']}!important;color:{D['soft']}!important}}
  .bx-hero-img{{background:{D['tile']}!important}}
  .bx-link{{color:#E8754F!important}}
  .bx-btn{{background:{BRICK}!important}}
  .bx-btn a{{color:#FFFFFF!important}}"""
    # Outlook.com dark mode tags restyled nodes with data-ogsc / data-ogsb.
    rules = [ln.strip() for ln in dark_rules.strip().splitlines() if ln.strip()]
    ogsc = "\n".join(f"[data-ogsc] {r}" for r in rules)
    ogsb = "\n".join(f"[data-ogsb] {r}" for r in rules)
    # Separate <style> blocks on purpose: Gmail discards a whole block when it
    # meets a rule it can't parse (nested @keyframes, @supports, attribute
    # selectors). Keeping base + mobile in their own block means Gmail always
    # keeps the responsive layout even though it drops the rest.
    base = f"""<style>
:root{{color-scheme:light dark;supported-color-schemes:light dark}}
body{{margin:0!important;padding:0!important;width:100%!important;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}}
img{{border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic}}
a{{text-decoration:none}}
.bx-hero-img{{display:block;width:100%!important;max-width:600px!important;height:auto!important}}
@media only screen and (max-width:620px){{
  .bx-outer{{padding:0!important}}
  .bx-card{{border-radius:0!important;border-left:0!important;border-right:0!important}}
  .bx-pad{{padding-left:24px!important;padding-right:24px!important}}
  .bx-h1{{font-size:26px!important;line-height:32px!important}}
  .bx-gap{{width:8px!important}}
  .bx-btn-wrap{{width:100%!important}}
  .bx-btn a{{display:block!important;text-align:center!important}}
}}
</style>"""
    fonts = f"""<style>
@font-face{{font-family:'General Sans';src:url('{FONT_BASE}/GeneralSans-Regular.woff2') format('woff2');font-weight:400;font-style:normal}}
@font-face{{font-family:'General Sans';src:url('{FONT_BASE}/GeneralSans-Medium.woff2') format('woff2');font-weight:500;font-style:normal}}
@font-face{{font-family:'JetBrains Mono';src:url('{FONT_BASE}/JetBrainsMono-Regular.woff2') format('woff2');font-weight:400;font-style:normal}}
</style>"""
    dark = f"""<style>
@media (prefers-color-scheme:dark){{{dark_rules}
}}
</style>
<style>
{ogsc}
{ogsb}
</style>"""
    motion = """<style>
@media (prefers-reduced-motion:no-preference){
  @keyframes bx-rise{from{opacity:0;transform:translate3d(0,14px,0)}to{opacity:1;transform:none}}
  @keyframes bx-fade{from{opacity:0}to{opacity:1}}
  @keyframes bx-brick{0%{opacity:0;transform:translate3d(0,-7px,0)}70%{opacity:1}100%{opacity:1;transform:none}}
  .bx-in{animation:bx-fade .9s cubic-bezier(.16,1,.3,1) .15s both}
  .bx-b1{animation:bx-brick .7s cubic-bezier(.16,1,.3,1) .1s both}
  .bx-b2{animation:bx-brick .7s cubic-bezier(.16,1,.3,1) .28s both}
  .bx-b3{animation:bx-brick .7s cubic-bezier(.16,1,.3,1) .46s both}
}
</style>
<style>
@media (prefers-reduced-motion:reduce){
  .bx-hero-gif{display:none!important}
  .bx-hero-still{display:block!important;max-height:none!important;overflow:visible!important}
}
</style>
<style>
/* Apple Mail / iOS Mail / Samsung / Thunderbird: swap the PNG mark for the
   same three bricks built from divs so they can settle in one by one.
   Gmail + Outlook drop @supports, so they always keep the PNG. */
@supports (-webkit-backdrop-filter:none) or (backdrop-filter:none){
  .bx-mark-live{display:block!important;max-height:none!important;overflow:visible!important}
  .bx-mark-png{display:none!important}
}
</style>"""
    return "\n".join((base, fonts, dark, motion))


def _mark(size: int = 24) -> str:
    """Logo mark: PNG for every client, live-bricks twin for @supports clients.

    The live twin is the exact geometry of bricks-mark.svg (viewBox 24):
      rect(3,4,11,6.4) op .82 · rect(3,11.6,6,8.4) · rect(10.2,11.6,10.8,8.4), rx .8
    """
    s = size / 24.0

    def brick(cls: str, x: float, y: float, w: float, h: float, op: str = "1") -> str:
        return (
            f'<div class="{cls}" style="position:absolute;left:{x * s:.2f}px;top:{y * s:.2f}px;'
            f"width:{w * s:.2f}px;height:{h * s:.2f}px;border-radius:{0.8 * s:.2f}px;"
            f'background:{BRICK};opacity:{op}"></div>'
        )

    live = (
        f'<!--[if !mso]><!--><div class="bx-mark-live" style="display:none;max-height:0;overflow:hidden;'
        f'mso-hide:all;position:relative;width:{size}px;height:{size}px">'
        + brick("bx-b1", 3, 4, 11, 6.4, "0.82")
        + brick("bx-b2", 3, 11.6, 6, 8.4)
        + brick("bx-b3", 10.2, 11.6, 10.8, 8.4)
        + "</div><!--<![endif]-->"
    )
    png = (
        f'<img class="bx-mark-png" src="{LOGO_URL}" width="{size}" height="{size}" alt="" '
        f'style="display:block;width:{size}px;height:{size}px;border:0">'
    )
    return png + live


def _header(date_label: str) -> str:
    return f"""<tr><td class="bx-pad" style="padding:28px 40px 0 40px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td valign="middle" style="width:24px">{_mark(24)}</td>
<td valign="middle" class="bx-ink" style="padding-left:8px;font-family:{FONT_STACK};font-size:18px;line-height:24px;font-weight:500;letter-spacing:-0.5px;color:{L['ink']}">bricks</td>
<td valign="middle" align="right" class="bx-mute" style="font-family:{MONO_STACK};font-size:11px;line-height:16px;letter-spacing:0.4px;color:{L['mute']}">Delivered · {_e(date_label)}</td>
</tr></table>
</td></tr>"""


def _hero_img(src: str, alt: str, cls: str, extra: str = "") -> str:
    # Plain absolute-URL <img> with explicit width AND height so every client
    # reserves the box before it loads; CSS keeps it fluid on phones.
    return (
        f'<img class="bx-hero-img {cls}" src="{_e(src)}" width="{HERO_W}" height="{HERO_H}" alt="{_e(alt)}" '
        f'style="display:block;width:100%;max-width:{HERO_W}px;height:auto;border:0;outline:none;{extra}'
        f'background:{L["tile"]};color:{L["mute"]};font-family:{FONT_STACK};font-size:13px">'
    )


def _hero(ctx: DeliveryEmailContext) -> str:
    """One image from this job. GIF crossfade when available (its first frame is
    the still, so Outlook for Windows shows the still); reduced-motion readers in
    Apple Mail get the still JPEG instead."""
    if not ctx.stills:
        return ""
    first = ctx.stills[0]
    still = first.hero_url or first.tile_url
    if not still:
        return ""
    alt = first.alt or f"Photo from {ctx.address}"
    if ctx.hero_gif_url:
        img = _hero_img(ctx.hero_gif_url, alt, "bx-hero-gif") + (
            "<!--[if !mso]><!-->"
            + _hero_img(still, alt, "bx-hero-still", "display:none;max-height:0;overflow:hidden;mso-hide:all;")
            .replace("display:block;width:100%", "width:100%", 1)
            + "<!--<![endif]-->"
        )
    else:
        img = _hero_img(still, alt, "bx-hero-jpg")
    return f"""<tr><td style="padding:24px 0 0 0;font-size:0;line-height:0">
<a href="{_e(ctx.primary_url)}" target="_blank" style="display:block;text-decoration:none">{img}</a>
</td></tr>"""


def _chips(services: Sequence[str]) -> str:
    if not services:
        return ""
    # Left-aligned chip tables wrap to a second line on narrow screens.
    chip_tables = "".join(
        f'<table role="presentation" align="left" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="padding:0 6px 8px 0"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td class="bx-chip" style="background:{L["chip"]};border:1px solid {L["hair"]};border-radius:999px;'
        f'padding:5px 11px;font-family:{FONT_STACK};font-size:13px;line-height:18px;color:{L["soft"]};white-space:nowrap">'
        f"{_e(label)}</td></tr></table></td></tr></table>"
        for label in services
    )
    return f"""<tr><td class="bx-pad bx-in" style="padding:20px 40px 0 40px">
<div class="bx-mute" style="font-family:{MONO_STACK};font-size:11px;line-height:16px;letter-spacing:0.4px;color:{L['mute']};padding:0 0 10px 0">Included</div>
{chip_tables}
</td></tr>"""


def _button(ctx: DeliveryEmailContext) -> str:
    url = _e(ctx.primary_url)
    label = _e(ctx.button_label)
    return f"""<tr><td class="bx-pad" style="padding:24px 40px 0 40px">
<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{url}" style="height:48px;v-text-anchor:middle;width:220px" arcsize="13%" stroke="f" fillcolor="{BRICK}"><w:anchorlock/><center style="color:#FFFFFF;font-family:Arial,sans-serif;font-size:15px;font-weight:600">{label}</center></v:roundrect><![endif]-->
<!--[if !mso]><!--><table role="presentation" class="bx-btn-wrap" cellpadding="0" cellspacing="0" border="0"><tr>
<td class="bx-btn" bgcolor="{BRICK}" style="background:{BRICK};border-radius:6px">
<a href="{url}" target="_blank" style="display:inline-block;padding:14px 26px;font-family:{FONT_STACK};font-size:15px;line-height:20px;font-weight:500;color:#FFFFFF;text-decoration:none;border-radius:6px">{label}</a>
</td></tr></table><!--<![endif]-->
</td></tr>"""


def _no_stills_panel(ctx: DeliveryEmailContext) -> str:
    """Video-only / floor-plan-only / Visit-only: a quiet panel instead of a hero."""
    if ctx.stills:
        return ""
    return f"""<tr><td class="bx-pad" style="padding:24px 40px 0 40px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td class="bx-chip" style="background:{L['chip']};border:1px solid {L['hair']};border-radius:8px;padding:18px 20px;font-family:{FONT_STACK};font-size:14px;line-height:21px;color:{L['soft']}">
<span class="bx-soft" style="color:{L['soft']}">Everything for this delivery is waiting on one page, ready to play, open or download.</span>
</td></tr></table>
</td></tr>"""


def _footer(ctx: DeliveryEmailContext) -> str:
    secondary = ""
    if ctx.share_link and ctx.desk_url:
        lead = "Or open your Bricks account" if ctx.client else "Or open the listing file"
        secondary = (
            f'<p class="bx-mute" style="margin:0 0 10px 0;font-family:{FONT_STACK};font-size:13px;line-height:19px;color:{L["mute"]}">'
            f'{lead}: <a class="bx-link" href="{_e(ctx.desk_url)}" target="_blank" style="color:{BRICK};text-decoration:none">'
            f"{_e(ctx.desk_url)}</a></p>"
        )
    return f"""<tr><td class="bx-pad" style="padding:32px 40px 0 40px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td class="bx-hair" style="border-top:1px solid {L['hair']};font-size:0;line-height:0;height:1px">&nbsp;</td></tr></table>
</td></tr>
<tr><td class="bx-pad" style="padding:20px 40px 36px 40px">
{secondary}
<p class="bx-mute" style="margin:0;font-family:{FONT_STACK};font-size:13px;line-height:19px;color:{L['mute']}">Questions? Reply to this email or write <a class="bx-link" href="mailto:{SUPPORT_EMAIL}" style="color:{BRICK};text-decoration:none">{SUPPORT_EMAIL}</a>.</p>
</td></tr>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_delivery_email(ctx: DeliveryEmailContext) -> RenderedEmail:
    addr = (ctx.address or "").strip() or ("your property" if ctx.client else "your listing")
    what, verb = ctx.headline_what, ctx.headline_verb
    subject = subject_for(what, addr, verb)
    name = (ctx.recipient_name or "").strip() or "there"
    date_label = _date_label(ctx.delivered_at)

    if ctx.share_link:
        note = (
            "This link doesn't expire. You can share it with your team as is."
            if ctx.client
            else "This link doesn't expire. You can share it with your seller as is."
        )
    else:
        note = "Sign in to view and download everything."
    intro = f"Hi {name}, your {what} for {addr} {verb} edited, checked and ready to view and download."
    if ctx.capture_note:
        intro = f"{intro} {ctx.capture_note}"
    preheader = f"Your {what} for {addr} {verb} ready to view and download."

    head_title = f"Your {what} {verb} ready."
    html_doc = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no,address=no,email=no,date=no">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(subject)}</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
{_style_block()}
</head>
<body class="bx-page" style="margin:0;padding:0;background:{L['page']};word-spacing:normal">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{L['page']};opacity:0">{_e(preheader)}&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" class="bx-page" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{L['page']}">
<tr><td class="bx-outer" align="center" style="padding:32px 12px">
<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" class="bx-card" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:{L['card']};border:1px solid {L['hair']};border-radius:12px;overflow:hidden">
{_header(date_label)}
{_hero(ctx)}
<tr><td class="bx-pad bx-in" style="padding:32px 40px 0 40px">
<h1 class="bx-ink bx-h1" style="margin:0;font-family:{FONT_STACK};font-size:30px;line-height:36px;font-weight:500;letter-spacing:-0.8px;color:{L['ink']}">{_e(head_title)}</h1>
<p class="bx-soft" style="margin:8px 0 0 0;font-family:{FONT_STACK};font-size:16px;line-height:24px;color:{L['soft']}">{_e(addr)}</p>
<p class="bx-soft" style="margin:20px 0 0 0;font-family:{FONT_STACK};font-size:15px;line-height:24px;color:{L['soft']}">{_e(intro)}</p>
</td></tr>
{_chips(ctx.services)}
{_button(ctx)}
<tr><td class="bx-pad" style="padding:14px 40px 0 40px"><p class="bx-mute" style="margin:0;font-family:{FONT_STACK};font-size:13px;line-height:19px;color:{L['mute']}">{_e(note)}</p></td></tr>
{_no_stills_panel(ctx)}
{_footer(ctx)}
</table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px">
<tr><td align="center" class="bx-mute" style="padding:20px 24px 8px 24px;font-family:{FONT_STACK};font-size:12px;line-height:18px;color:{L['mute']}">Bricks · <a class="bx-link" href="https://www.brxs.ca" target="_blank" style="color:{L['mute']};text-decoration:none">brxs.ca</a></td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body></html>"""

    text = _plain_text(ctx, subject=subject, addr=addr, name=name, what=what, verb=verb, note=note)
    return RenderedEmail(subject=subject, html=html_doc, text=text, preheader=preheader)


def _plain_text(ctx: DeliveryEmailContext, *, subject: str, addr: str, name: str,
                what: str, verb: str, note: str) -> str:
    lines = [
        f"Your {what} {verb} ready.",
        addr,
        "",
        f"Hi {name}, your {what} for {addr} {verb} edited, checked and ready to view and download."
        + (f" {ctx.capture_note}" if ctx.capture_note else ""),
        "",
    ]
    if ctx.services:
        lines += ["Included: " + ", ".join(ctx.services), ""]
    lines += [f"{ctx.button_label}: {ctx.primary_url}", note]
    if ctx.photo_count > 1:
        lines.append(f"{ctx.photo_count} photos in your gallery.")
    if ctx.share_link and ctx.desk_url:
        lead = "Or open your Bricks account" if ctx.client else "Or open the listing file"
        lines += ["", f"{lead}: {ctx.desk_url}"]
    lines += ["", f"Questions? Reply to this email or write {SUPPORT_EMAIL}.", "", "Bricks · brxs.ca"]
    return "\n".join(lines).strip() + "\n"
