const actorId = process.env.EXPO_PUBLIC_ACTOR_ID || 'default_user';

export function actorHeaders(extra: Record<string, string> = {}) {
  return {
    'X-User-Id': actorId,
    ...extra,
  };
}

