export const BRICKS_GO_AUTH_CALLBACK: string;
export function codeFromAuthCallback(
  callbackUrl: string,
  expectedRedirect: string,
): string | null;
