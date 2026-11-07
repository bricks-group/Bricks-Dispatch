export const CALENDAR_INTRO_TITLE = 'Jobs that fit your schedule';
export const CALENDAR_EDU_LINES = [
  'Connect your calendar so we can avoid times you’ve already booked.',
  'Your working hours still apply, with or without a connected calendar.',
] as const;
export const CALENDAR_PROVIDER_HINTS: Record<string, string> = {
  google: 'Use the calendar you already use with Google.',
  microsoft: 'Use your Outlook or Microsoft 365 calendar.',
  apple: 'Create an app-specific password in your Apple Account security settings.',
  ics: 'Paste a sharing link from your calendar settings.',
};
export const CALENDAR_NUDGE_TITLE = CALENDAR_INTRO_TITLE;
export const CALENDAR_NUDGE_BODY = CALENDAR_EDU_LINES[0];
