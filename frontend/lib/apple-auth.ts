// Sign in with Apple (App Store Guideline 4.8).
//
// Code-ready but OFF until Apple credentials exist. To turn on:
//   1. Apple Developer: enable "Sign in with Apple" on App ID com.bricks.go.
//   2. Supabase (rbywjnnctjglhcdemnnj) > Auth > Providers > Apple: enable and add
//      com.bricks.go to "Client IDs" (native flow needs no secret key).
//   3. Set EXPO_PUBLIC_APPLE_SIGN_IN=1 in eas.json production env and rebuild.
// While OFF, the iOS login screen also hides "Continue with Google", so the iOS build
// offers only Bricks email/password and never violates 4.8.
import { Platform } from 'react-native';
import * as AppleAuthentication from 'expo-apple-authentication';
import * as Crypto from 'expo-crypto';
import { supabase } from './supabase';

export const APPLE_SIGN_IN_ENABLED = process.env.EXPO_PUBLIC_APPLE_SIGN_IN === '1';

export async function appleSignInAvailable(): Promise<boolean> {
  if (!APPLE_SIGN_IN_ENABLED || Platform.OS !== 'ios') return false;
  try {
    return await AppleAuthentication.isAvailableAsync();
  } catch {
    return false;
  }
}

/** Returns true on success, false if the user cancelled. Throws on real errors. */
export async function signInWithApple(): Promise<boolean> {
  const rawNonce = Crypto.randomUUID();
  const hashedNonce = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, rawNonce);
  try {
    const cred = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL,
      ],
      nonce: hashedNonce,
    });
    if (!cred.identityToken) throw new Error('Apple did not return an identity token.');
    const { error } = await supabase.auth.signInWithIdToken({
      provider: 'apple',
      token: cred.identityToken,
      nonce: rawNonce,
    });
    if (error) throw error;
    // Apple only sends the name on first sign-in; keep it on the auth user.
    const fullName = [cred.fullName?.givenName, cred.fullName?.familyName].filter(Boolean).join(' ');
    if (fullName) await supabase.auth.updateUser({ data: { full_name: fullName } }).catch(() => {});
    return true;
  } catch (e: any) {
    if (e?.code === 'ERR_REQUEST_CANCELED') return false;
    throw e;
  }
}

export { AppleAuthentication };
