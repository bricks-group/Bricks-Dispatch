"""Federal GST/HST only; contractor supply follows the property's province."""
import re
RATES = {"ON": .13, "NS": .14, "NB": .15, "NL": .15, "PE": .15,
         "BC": .05, "AB": .05, "SK": .05, "MB": .05, "QC": .05,
         "NT": .05, "NU": .05, "YT": .05}
FSA = dict(zip("ABCEGHJKLMNPRSTVXY", ["NL","NS","PE","NB","QC","QC","QC","ON","ON","ON","ON","ON","MB","SK","AB","BC","NT","YT"]))
def property_tax_rate(job):
    province = str(job.get("tax_province") or job.get("province") or job.get("state") or "").strip().upper()
    if not province:
        province = str(job.get("tax_jurisdiction") or "").removeprefix("CA-")
    if province not in RATES:
        m = re.search(r"(?:^|[\s,])(ON|NS|NB|NL|PE|BC|AB|SK|MB|QC|NT|NU|YT)(?:[\s,]|$)", str(job.get("address") or ""), re.I)
        if m: province = m.group(1).upper()
    if province not in RATES:
        postal = str(job.get("postal_code") or job.get("zip_code") or "").strip().upper()
        province = FSA.get(postal[:1])
    if province not in RATES:
        raise ValueError("Property province is required before a registered contractor's tax can be paid")
    return RATES[province]
