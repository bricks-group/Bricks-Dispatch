import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";
import { escapeHtml, fmtDate } from "../_shared/email-utils.ts";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

const KNOWN_REGIONS = [
  'Toronto',
  'Mississauga',
  'Brampton',
  'Oakville',
  'Burlington',
  'Vaughan',
  'Markham',
  'Richmond Hill',
  'Newmarket',
  'Aurora',
  'Ajax',
  'Whitby',
  'Oshawa',
  'Pickering',
  'Hamilton',
  'Niagara',
  'St. Catharines',
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, 'Content-Type': 'application/json' },
  });
}

function detectRegion(address: string): string | null {
  if (!address) return null;
  const lower = address.toLowerCase();
  for (const region of KNOWN_REGIONS) {
    if (lower.includes(region.toLowerCase())) return region;
  }
  return null;
}

function getRequiredTier(packageType: string | null, serviceType: string | null): number {
  const pkg = (packageType ?? '').toLowerCase().trim();
  const svc = (serviceType ?? '').toLowerCase().trim();
  if (pkg === 'elite') return 3;
  if (
    pkg === 'premium' ||
    pkg === 'video' ||
    pkg === 'aerial' ||
    pkg === 'brand video' ||
    svc === 'video' ||
    svc === 'aerial' ||
    svc === 'brand video'
  ) {
    return 2;
  }
  return 1;
}

function buildOfferHtml(opts: {
  contractorName: string;
  propertyAddress: string;
  packageType: string;
  preferredDate: string;
  preferredTime: string;
  totalPrice: number;
  acceptUrl: string;
  declineUrl: string;
  expiresAt: string;
}): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'General Sans','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <tr><td style="padding:0 0 24px 0;"><div style="font-size:20px;font-weight:500;letter-spacing:-0.5px;color:#F0F0F0;">bricks</div></td></tr>
        <tr>
          <td style="background:#161616;border:1px solid #2a2a2a;border-radius:12px;padding:36px;">
            <p style="margin:0 0 6px 0;font-size:13px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#D2542B;">New Shoot Offer</p>
            <h1 style="margin:0 0 24px 0;font-size:26px;font-weight:300;color:#F0F0F0;line-height:1.3;">
              ${escapeHtml(opts.contractorName.split(/\s+/)[0] || 'there')}, a booking matches your tier and region.
            </h1>
            <table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #2a2a2a;margin-bottom:24px;">
              <tr><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#888;font-size:13px;width:140px;">Property</td><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(opts.propertyAddress)}</td></tr>
              <tr><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#888;font-size:13px;">Package</td><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(opts.packageType)}</td></tr>
              <tr><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#888;font-size:13px;">Date</td><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(fmtDate(opts.preferredDate))}</td></tr>
              <tr><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#888;font-size:13px;">Time</td><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(opts.preferredTime)}</td></tr>
              <tr><td style="padding:12px 0;color:#888;font-size:13px;">Booked total</td><td style="padding:12px 0;color:#D2542B;font-size:13px;font-weight:600;">$${opts.totalPrice.toLocaleString('en-CA')}</td></tr>
            </table>
            <p style="margin:0 0 20px 0;font-size:14px;color:#999;line-height:1.6;">This offer expires at ${escapeHtml(opts.expiresAt)}. First contractor to accept gets the booking automatically.</p>
            <div style="display:flex;gap:12px;flex-wrap:wrap;">
              <a href="${opts.acceptUrl}" style="display:inline-block;background:#D2542B;color:#ffffff;font-size:13px;font-weight:600;padding:14px 28px;border-radius:6px;text-decoration:none;letter-spacing:0.04em;">Accept Booking</a>
              <a href="${opts.declineUrl}" style="display:inline-block;background:#242424;color:#f0f0f0;font-size:13px;font-weight:600;padding:14px 28px;border-radius:6px;text-decoration:none;letter-spacing:0.04em;border:1px solid #3a3a3a;">Decline</a>
            </div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`;
}

function buildSlotWindow(dateStr: string, timeLabel: string | null) {
  const base = (timeLabel ?? '').toLowerCase();
  if (base.includes('afternoon') && !base.includes('late')) {
    return { start: `${dateStr}T12:00:00.000Z`, end: `${dateStr}T16:00:00.000Z` };
  }
  if (base.includes('twilight') || base.includes('late')) {
    return { start: `${dateStr}T16:00:00.000Z`, end: `${dateStr}T19:00:00.000Z` };
  }
  return { start: `${dateStr}T09:00:00.000Z`, end: `${dateStr}T12:00:00.000Z` };
}

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders });
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  try {
    const serviceRoleKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
    const authHeader = req.headers.get('Authorization') ?? '';

    let authorized = authHeader === `Bearer ${serviceRoleKey}`;
    if (!authorized) {
      const admin = createClient(Deno.env.get('SUPABASE_URL')!, serviceRoleKey);
      const { data: vaultRow } = await admin
        .schema('vault')
        .from('decrypted_secrets')
        .select('decrypted_secret')
        .eq('name', 'booking_trigger_secret')
        .maybeSingle();
      if (vaultRow?.decrypted_secret && authHeader === `Bearer ${vaultRow.decrypted_secret}`) {
        authorized = true;
      }
    }

    if (!authorized) return json({ error: 'Unauthorized' }, 401);

    const { booking_id } = await req.json();
    if (!booking_id) return json({ error: 'booking_id is required' }, 400);

    const supabaseUrl = Deno.env.get('SUPABASE_URL')!;
    const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { autoRefreshToken: false, persistSession: false } });
    // Hoisted above both the direct-assignment and broadcast paths -- it used
    // to be declared (via const) only down near the broadcast path, so the
    // direct-assignment path referenced it before its declaration executed
    // and threw a ReferenceError (JS temporal dead zone) every time exactly
    // one contractor was eligible: the DB assignment already happened, but
    // the function then crashed before sending the email/SMS notification
    // and returned a 500, so the contractor was assigned without ever being
    // told.
    const resendApiKey = Deno.env.get('RESEND_API_KEY');

    const { data: booking, error: bookingError } = await supabase
      .from('bookings')
      .select('id, status, package_type, service_type, preferred_date, preferred_time, property_address, total_price, assigned_photographer, assigned_contractor_id, assignment_status, assignment_round')
      .eq('id', booking_id)
      .single();

    if (bookingError || !booking) return json({ error: 'Booking not found' }, 404);

    if (booking.assigned_contractor_id || booking.assigned_photographer) {
      return json({ success: true, skipped: 'booking_already_assigned' });
    }

    const { data: existingOffers } = await supabase
      .from('assignment_offers')
      .select('id')
      .eq('booking_id', booking_id)
      .eq('status', 'sent')
      .gt('expires_at', new Date().toISOString())
      .limit(1);

    if ((existingOffers?.length ?? 0) > 0) {
      return json({ success: true, skipped: 'existing_active_offers' });
    }

    const { data: settings } = await supabase
      .from('company_calendar_settings')
      .select('broadcast_limit, claim_window_minutes')
      .eq('id', true)
      .maybeSingle();

    const broadcastLimit = Math.max(1, Number(settings?.broadcast_limit ?? 3));
    const claimWindowMinutes = Math.max(5, Number(settings?.claim_window_minutes ?? 30));
    const requiredTier = getRequiredTier(booking.package_type, booking.service_type);
    const detectedRegion = detectRegion(booking.property_address ?? '');

    const { data: contractors, error: contractorsError } = await supabase
      .from('contractors')
      .select('id, full_name, email, phone, tier, geo_regions, auto_assign, status')
      .eq('status', 'active')
      .eq('auto_assign', true)
      .gte('tier', requiredTier);

    if (contractorsError) throw contractorsError;
    if (!contractors || contractors.length === 0) {
      await supabase.from('bookings').update({ assignment_status: 'unassigned' }).eq('id', booking_id);
      return json({ success: true, offers_sent: 0, reason: 'no_eligible_contractors' });
    }

    const geoMatched = detectedRegion
      ? contractors.filter((contractor) =>
          (contractor.geo_regions ?? []).some((region: string) => region.toLowerCase() === detectedRegion.toLowerCase()),
        )
      : contractors;
    const scopedContractors = geoMatched.length > 0 ? geoMatched : contractors;
    const contractorIds = scopedContractors.map((contractor) => contractor.id);

    let manualMap = new Map<string, boolean>();
    let busySet = new Set<string>();
    if (booking.preferred_date) {
      const [{ data: manualRows, error: manualError }, { data: busyRows, error: busyError }] = await Promise.all([
        supabase
          .from('contractor_availability')
          .select('contractor_id, available')
          .in('contractor_id', contractorIds)
          .eq('date', booking.preferred_date),
        supabase
          .from('contractor_busy_slots')
          .select('contractor_id')
          .in('contractor_id', contractorIds)
          .lte('starts_at', `${booking.preferred_date}T23:59:59.999Z`)
          .gte('ends_at', `${booking.preferred_date}T00:00:00.000Z`),
      ]);
      if (manualError) throw manualError;
      if (busyError) throw busyError;
      for (const row of manualRows ?? []) manualMap.set(row.contractor_id, row.available);
      for (const row of busyRows ?? []) busySet.add(row.contractor_id);
    }

    const available = scopedContractors
      .filter((contractor) => {
        if (manualMap.has(contractor.id)) return manualMap.get(contractor.id) === true;
        return !busySet.has(contractor.id);
      })
      .sort((a, b) => {
        const aGeo = detectedRegion ? (a.geo_regions ?? []).some((r: string) => r.toLowerCase() === detectedRegion.toLowerCase()) : false;
        const bGeo = detectedRegion ? (b.geo_regions ?? []).some((r: string) => r.toLowerCase() === detectedRegion.toLowerCase()) : false;
        if (aGeo !== bGeo) return aGeo ? -1 : 1;
        if (b.tier !== a.tier) return b.tier - a.tier;
        return a.full_name.localeCompare(b.full_name);
      })
      .slice(0, broadcastLimit);

    if (available.length === 0) {
      await supabase.from('bookings').update({ assignment_status: 'unassigned' }).eq('id', booking_id);
      return json({ success: true, offers_sent: 0, reason: 'no_available_contractors' });
    }

    if (available.length === 1) {
      const contractor = available[0];
      const nowIso = new Date().toISOString();

      await supabase
        .from('bookings')
        .update({
          assigned_contractor_id: contractor.id,
          assigned_photographer: contractor.email,
          assignment_status: 'assigned',
          assignment_round: Number(booking.assignment_round ?? 0) + 1,
          assignment_last_sent_at: nowIso,
          status: booking.status === 'pending' ? 'confirmed' : booking.status,
        })
        .eq('id', booking_id);

      if (booking.preferred_date) {
        const slot = buildSlotWindow(booking.preferred_date, booking.preferred_time ?? null);
        await supabase.from('contractor_busy_slots').insert({
          contractor_id: contractor.id,
          starts_at: slot.start,
          ends_at: slot.end,
          event_uid: `dispatch-${booking_id}`,
          summary: booking.property_address ?? 'Bricks booking',
          source: 'dispatch',
        });
      }

      if (contractor.email && resendApiKey) {
        await fetch('https://api.resend.com/emails', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${resendApiKey}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            from: 'Bricks Ops <bookings@brxs.ca>',
            to: [contractor.email],
            subject: `Assigned Bricks shoot — ${booking.property_address}`,
            html: `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;padding:0;background:#0a0a0a;font-family:'General Sans','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"><table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 20px;"><tr><td align="center"><table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;"><tr><td style="padding:0 0 24px 0;"><div style="font-size:20px;font-weight:500;letter-spacing:-0.5px;color:#F0F0F0;">bricks</div></td></tr><tr><td style="background:#161616;border:1px solid #2a2a2a;border-radius:12px;padding:36px;"><p style="margin:0 0 6px 0;font-size:13px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#D2542B;">Shoot Assigned</p><h1 style="margin:0 0 24px 0;font-size:26px;font-weight:300;color:#F0F0F0;line-height:1.3;">${escapeHtml(contractor.full_name.split(/\s+/)[0] || 'there')}, this booking has been assigned to you.</h1><table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #2a2a2a;margin-bottom:24px;"><tr><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#888;font-size:13px;width:140px;">Property</td><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(booking.property_address ?? '')}</td></tr><tr><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#888;font-size:13px;">Package</td><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(booking.package_type ?? '')}</td></tr><tr><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#888;font-size:13px;">Date</td><td style="padding:12px 0;border-bottom:1px solid #2a2a2a;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(fmtDate(booking.preferred_date ?? ''))}</td></tr><tr><td style="padding:12px 0;color:#888;font-size:13px;">Time</td><td style="padding:12px 0;color:#F0F0F0;font-size:13px;font-weight:500;">${escapeHtml(booking.preferred_time ?? '')}</td></tr></table><p style="margin:0;font-size:14px;color:#999;line-height:1.6;">You can see this booking in your contractor dashboard now.</p></td></tr></table></td></tr></table></body></html>`,
          }),
        }).catch((error) => console.error('dispatch-booking direct-assign email failed:', error));
      }

      if (contractor.phone) {
        const smsBody = `Assigned Bricks shoot: ${booking.package_type} at ${booking.property_address} on ${fmtDate(booking.preferred_date ?? '')} (${booking.preferred_time}). Check your contractor dashboard for details.`;
        fetch(`${supabaseUrl}/functions/v1/send-sms`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${serviceRoleKey}`,
          },
          body: JSON.stringify({ to: contractor.phone, message: smsBody }),
        }).catch((error) => console.error('dispatch-booking direct-assign sms failed:', error));
      }

      return json({
        success: true,
        direct_assignment: true,
        contractor_id: contractor.id,
        contractor_email: contractor.email,
      });
    }

    const now = new Date();
    const expiresAt = new Date(now.getTime() + claimWindowMinutes * 60_000).toISOString();
    const responseBase = `${supabaseUrl}/functions/v1/respond-assignment-offer`;

    const offers = available.map((contractor) => ({
      booking_id,
      contractor_id: contractor.id,
      token: crypto.randomUUID().replace(/-/g, ''),
      status: 'sent',
      expires_at: expiresAt,
      delivery_channels: [
        contractor.email ? 'email' : null,
        contractor.phone ? 'sms' : null,
      ].filter(Boolean),
    }));

    const { data: insertedOffers, error: offerError } = await supabase
      .from('assignment_offers')
      .insert(offers)
      .select('id, contractor_id, token');

    if (offerError) throw offerError;

    await supabase
      .from('bookings')
      .update({
        assignment_status: 'broadcasting',
        assignment_round: Number(booking.assignment_round ?? 0) + 1,
        assignment_last_sent_at: now.toISOString(),
      })
      .eq('id', booking_id);

    const expiresLabel = new Date(expiresAt).toLocaleString('en-CA', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZone: 'America/Toronto',
    });

    for (const contractor of available) {
      const offer = insertedOffers?.find((row) => row.contractor_id === contractor.id);
      if (!offer) continue;
      const acceptUrl = `${responseBase}?token=${offer.token}&action=accept`;
      const declineUrl = `${responseBase}?token=${offer.token}&action=decline`;

      if (contractor.email && resendApiKey) {
        await fetch('https://api.resend.com/emails', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${resendApiKey}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            from: 'Bricks Ops <bookings@brxs.ca>',
            to: [contractor.email],
            subject: `New Bricks shoot offer — ${booking.property_address}`,
            html: buildOfferHtml({
              contractorName: contractor.full_name ?? 'there',
              propertyAddress: booking.property_address ?? '',
              packageType: booking.package_type ?? '',
              preferredDate: booking.preferred_date ?? '',
              preferredTime: booking.preferred_time ?? '',
              totalPrice: booking.total_price ?? 0,
              acceptUrl,
              declineUrl,
              expiresAt: expiresLabel,
            }),
          }),
        }).catch((error) => console.error('dispatch-booking email failed:', error));
      }

      if (contractor.phone) {
        const smsBody = `Bricks shoot offer: ${booking.package_type} at ${booking.property_address} on ${fmtDate(booking.preferred_date ?? '')} (${booking.preferred_time}). First to accept gets it. Accept: ${acceptUrl}`;
        fetch(`${supabaseUrl}/functions/v1/send-sms`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${serviceRoleKey}`,
          },
          body: JSON.stringify({ to: contractor.phone, message: smsBody }),
        }).catch((error) => console.error('dispatch-booking sms failed:', error));
      }
    }

    return json({
      success: true,
      offers_sent: available.length,
      detected_region: detectedRegion,
      required_tier: requiredTier,
    });
  } catch (err) {
    console.error('dispatch-booking error:', err);
    return json({ error: (err as Error).message }, 500);
  }
});
