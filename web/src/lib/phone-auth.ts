// Phone (SMS OTP) sign-in helpers for GO web — pure, no React, unit-tested with mocks.
// Same contract as Bricks-OS frontend/src/lib/phoneAuth.js.
// Supabase sends the code via the project's phone provider (Twilio Verify).
// Nothing here sends anything until signInWithOtp is called by a user tap.

export type AuthErrorLike = { code?: string; message?: string; status?: number } | null | undefined;
type OtpClient = {
  auth: {
    signInWithOtp: (args: { phone: string; options?: Record<string, unknown> }) => Promise<{ error: unknown }>;
    verifyOtp: (args: { phone: string; token: string; type: 'sms' }) => Promise<{ data: { session?: unknown; user?: unknown } | null; error: unknown }>;
  };
};
type FetchLike = (url: string, init?: { headers?: Record<string, string> }) => Promise<{ ok: boolean; json: () => Promise<unknown> }>;

export const RESEND_COOLDOWN_SECONDS = 30;
export const OTP_LENGTH = 6;
export const PHONE_UNAVAILABLE_MESSAGE = "Phone sign-in isn't available yet. Use email or Google for now.";

/**
 * Format user input as E.164. Defaults to +1 (Canada/US) when no country code is typed.
 * Returns "" when the input cannot be a valid number.
 */
export function toE164(input: string | null | undefined, defaultCountryCode = "1"): string {
  const raw = String(input || "").trim();
  if (!raw) return "";
  const hasPlus = raw.startsWith("+");
  let digits = raw.replace(/\D/g, "");
  if (!hasPlus && digits.startsWith("00")) digits = digits.slice(2); // 00 international prefix
  else if (!hasPlus) {
    if (digits.length === 10) digits = defaultCountryCode + digits; // 4165550123
    else if (digits.length === 11 && digits.startsWith(defaultCountryCode)) digits = String(digits); // 14165550123
    else if (defaultCountryCode === "1") return ""; // NANP needs 10 digits
    else digits = defaultCountryCode + digits;
  }
  if (digits.length < 8 || digits.length > 15 || digits.startsWith("0")) return "";
  if (digits.startsWith("1") && digits.length !== 11) return ""; // NANP is exactly +1 + 10
  return `+${digits}`;
}

/** Friendly display: +1 (416) 555-0123 for NANP, else the E.164 string. */
export function formatPhoneDisplay(e164: string | null | undefined): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164 || "");
  return m ? `+1 (${m[1]}) ${m[2]}-${m[3]}` : (e164 || "");
}

export function cleanOtp(input: string | null | undefined): string {
  return String(input || "").replace(/\D/g, "").slice(0, OTP_LENGTH);
}

/** True when Supabase says the phone provider is off (before Twilio Verify is enabled). */
export function isPhoneProviderDisabled(raw: unknown): boolean {
  const err = raw as AuthErrorLike;
  const code = String(err?.code || "").toLowerCase();
  const msg = String(err?.message || raw || "").toLowerCase();
  return (
    code === "phone_provider_disabled"
    || (code === "sms_send_failed" && msg.includes("provider"))
    || msg.includes("unsupported phone provider")
    || msg.includes("phone provider")
    || msg.includes("phone logins are disabled")
    || msg.includes("phone signups are disabled")
  );
}

/** Seconds to wait from a Supabase rate-limit message, if any. */
export function retryAfterSeconds(err: AuthErrorLike): number {
  const m = /after (\d+) seconds?/i.exec(String(err?.message || ""));
  return m ? Number(m[1]) : 0;
}

/** Map Supabase auth errors from the phone flow to clear, user-facing copy. */
export function phoneAuthErrorMessage(raw: unknown, step: "send" | "verify" = "send"): string {
  const err = raw as AuthErrorLike;
  if (!err) return "";
  if (isPhoneProviderDisabled(err)) return PHONE_UNAVAILABLE_MESSAGE;
  const code = String(err.code || "").toLowerCase();
  const msg = String(err.message || "").toLowerCase();
  const wait = retryAfterSeconds(err);
  if (wait) return `Too many attempts. Try again in ${wait} seconds.`;
  if (code.includes("rate_limit") || err.status === 429 || msg.includes("rate limit")) {
    return "Too many attempts. Wait a minute and try again.";
  }
  if (code === "otp_expired" || msg.includes("expired") || (msg.includes("invalid") && step === "verify")) {
    return "That code is wrong or expired. Check the text, or send a new code.";
  }
  if (msg.includes("e.164") || msg.includes("invalid phone") || (code === "validation_failed" && step === "send")) {
    return "Enter a valid mobile number, like (416) 555-0123.";
  }
  if (code === "sms_send_failed" || msg.includes("error sending")) {
    return "We couldn't text that number. Check it and try again.";
  }
  if (code === "signup_disabled" || msg.includes("signups not allowed")) {
    return "No account uses that number yet. Create an account first.";
  }
  return err.message || (step === "verify" ? "Could not verify the code." : "Could not send the code.");
}

/** Map errors from adding an email to a phone account. */
export function addEmailErrorMessage(raw: unknown): string {
  const err = raw as AuthErrorLike;
  if (!err) return "";
  const code = String(err.code || "").toLowerCase();
  const msg = String(err.message || "").toLowerCase();
  if (code === "email_exists" || msg.includes("already been registered")) {
    return "That email already has a Bricks account. Sign out and sign in with that email instead.";
  }
  if (code === "email_address_invalid" || (msg.includes("invalid") && msg.includes("email"))) {
    return "Enter a valid email address.";
  }
  const wait = retryAfterSeconds(err);
  if (wait) return `Too many attempts. Try again in ${wait} seconds.`;
  if (code.includes("rate_limit") || err.status === 429) return "Too many attempts. Wait a minute and try again.";
  return err.message || "Could not save your email.";
}

export function isValidEmail(email: string | null | undefined): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(email || "").trim());
}

/**
 * Reads GET {supabaseUrl}/auth/v1/settings. Returns true/false for external.phone,
 * or null when unknown (network error) — callers show the button and let the
 * provider-disabled message handle it.
 */
export async function fetchPhoneProviderEnabled(
  supabaseUrl: string,
  anonKey: string,
  fetchImpl: FetchLike | null = typeof fetch !== "undefined" ? (fetch as unknown as FetchLike) : null,
): Promise<boolean | null> {
  if (!supabaseUrl || !anonKey || !fetchImpl) return null;
  try {
    const res = await fetchImpl(`${supabaseUrl.replace(/\/$/, "")}/auth/v1/settings`, {
      headers: { apikey: anonKey },
    });
    if (!res || !res.ok) return null;
    const body = (await res.json()) as { external?: { phone?: unknown } | null } | null;
    if (!body || typeof body.external !== "object" || body.external === null) return null;
    return body.external.phone === true;
  } catch {
    return null;
  }
}

/** Step 1 — ask Supabase to text a code. */
export async function sendPhoneOtp(
  client: OtpClient,
  phoneInput: string,
  { createUser = true }: { createUser?: boolean } = {},
): Promise<{ phone: string; error: unknown }> {
  const phone = toE164(phoneInput);
  if (!phone) return { phone: "", error: { code: "validation_failed", message: "Invalid phone number format (E.164 required)" } };
  const { error } = await client.auth.signInWithOtp({ phone, options: { shouldCreateUser: createUser, channel: "sms" } });
  return { phone, error: error || null };
}

/** Step 2 — verify the 6-digit code; returns the Supabase session on success. */
export async function verifyPhoneOtp(
  client: OtpClient,
  phone: string,
  codeInput: string,
): Promise<{ session: unknown; user?: unknown; error: unknown }> {
  const token = cleanOtp(codeInput);
  if (token.length !== OTP_LENGTH) {
    return { session: null, error: { code: "otp_expired", message: `Enter the ${OTP_LENGTH}-digit code.` } };
  }
  const { data, error } = await client.auth.verifyOtp({ phone, token, type: "sms" });
  if (error) return { session: null, error };
  return { session: data?.session || null, user: data?.user || null, error: data?.session ? null : { message: "Could not start a session. Try again." } };
}
