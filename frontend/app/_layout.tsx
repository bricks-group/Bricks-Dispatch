import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, StyleSheet, Platform } from 'react-native';
import BricksLoader from '../components/BricksLoader';
import { useFonts } from 'expo-font';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { ThemeProvider } from '../context/ThemeContext';
import { color, fontAssets } from '../theme';
import { actorHeaders } from '../lib/api';
import { hasAppAccess, needsRegister, isUnderReview } from '../lib/vendor';
import { supabase } from '../lib/supabase';
import { configureNotificationHandler, ensureAndroidChannel, registerForPush, routeForNotification } from '../lib/push';
import { clearOfflineCache } from '../lib/offline';
import OfflineBanner from '../components/OfflineBanner';
import type { Session } from '@supabase/supabase-js';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

SplashScreen.preventAutoHideAsync();

function useInitPushNotifications(signedIn: boolean) {
  const router = useRouter();
  // Handler, Android channel, and tap routing are set up once on launch.
  useEffect(() => {
    if (Platform.OS === 'web') return;
    configureNotificationHandler();
    ensureAndroidChannel(color.accent).catch(() => {});
    const Notifications = require('expo-notifications');
    const open = (resp: any) => {
      const route = routeForNotification(resp?.notification?.request?.content?.data);
      if (route) router.push(route as any);
    };
    Notifications.getLastNotificationResponseAsync?.().then((r: any) => r && open(r)).catch(() => {});
    const sub = Notifications.addNotificationResponseReceivedListener(open);
    return () => sub.remove();
  }, []);
  // Permission prompt + token registration only after the vendor signs in,
  // so the token is stored against their account (POST /api/push-tokens is auth-only).
  useEffect(() => {
    if (Platform.OS === 'web' || !signedIn) return;
    registerForPush().catch(() => {});
  }, [signedIn]);
}

function ThemedStatusBar() {
  // GO is dark always (design system): light status-bar content everywhere.
  return <StatusBar style="light" />;
}

export default function RootLayout() {
  const router = useRouter();
  const segments = useSegments();
  const [session, setSession] = useState<Session | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [fontsLoaded] = useFonts(fontAssets);

  const [onboardingChecked, setOnboardingChecked] = useState(false);

  // Auth session management
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session: s } }) => {
      setSession(s);
      setAuthChecked(true);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      if (event === 'SIGNED_OUT') {
        setOnboardingChecked(false);
        clearOfflineCache();
      }
    });
    return () => subscription.unsubscribe();
  }, []);

  // Auth routing guard — new users see welcome slides first
  useEffect(() => {
    if (!authChecked || !fontsLoaded) return;
    const segs = segments as string[];
    const inAuth = segs[0] === '(auth)';
    const inOnboardingSlides = segs[0] === 'onboarding' && segs[1] === 'slides';
    if (!session && !inAuth && !inOnboardingSlides) {
      AsyncStorage.getItem('welcome_seen').then(seen => {
        if (!seen) router.replace('/onboarding/slides' as any);
        else router.replace('/(auth)/login');
      });
    } else if (session && inAuth) {
      router.replace('/(tabs)');
    }
  }, [session, authChecked, fontsLoaded, segments]);

  useInitPushNotifications(!!session);

  useEffect(() => {
    if (!fontsLoaded || !authChecked || !session) {
      if (authChecked && !session) setOnboardingChecked(true);
      return;
    }

    (async () => {
      try {
        const headers = await actorHeaders();
        const res = await fetch(`${API}/api/vendor-os/profile`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const profile = await res.json();

        const segs = segments as string[];
        const inOnboarding = segs[0] === 'onboarding';
        const onRegister = inOnboarding && segs[1] === 'register';
        const onUnderReview = inOnboarding && segs[1] === 'under-review';
        const onSlides = inOnboarding && segs[1] === 'slides';

        // Gate Today/Jobs/Capture until Kam approves in Hiring.
        // Detect pending via onboarding_status, is_active, app_access, or Applicant role
        // (public_profile historically masked role as Photographer IC).
        if (!hasAppAccess(profile)) {
          if (needsRegister(profile)) {
            if (!onRegister && !onSlides) {
              router.replace('/onboarding/register' as any);
            }
          } else if (isUnderReview(profile) || profile.onboarding_status === 'pending' || profile.is_active === false) {
            if (!onUnderReview) {
              router.replace('/onboarding/under-review' as any);
            }
          }
        }
      } catch (e) {
        console.error("Onboarding check failed", e);
      } finally {
        setOnboardingChecked(true);
      }
    })();
  }, [fontsLoaded, authChecked, session, segments]);

  useEffect(() => {
    const timer = setTimeout(() => {
      SplashScreen.hideAsync().catch(() => {});
    }, 3000);

    if (fontsLoaded && onboardingChecked && authChecked) {
      SplashScreen.hideAsync().catch(() => {});
    }

    return () => clearTimeout(timer);
  }, [fontsLoaded, onboardingChecked]);

  if (!fontsLoaded) {
    return (
      <View style={styles.loadingContainer}>
        <BricksLoader variant="assemble" size={160} startAtHold />
      </View>
    );
  }

  return (
    <ThemeProvider>
      <ThemedStatusBar />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: color.bg },
          animation: 'none',
        }}
      >
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen
          name="(capture)"
          options={{
            headerShown: false,
            presentation: 'fullScreenModal',
            animation: 'fade',
            animationDuration: 220,
          }}
        />
        <Stack.Screen
          name="job/[id]"
          options={{
            headerShown: false,
            presentation: 'card',
            animation: 'fade',
            animationDuration: 220,
          }}
        />
        <Stack.Screen
          name="onboarding"
          options={{
            headerShown: false,
            presentation: 'card',
          }}
        />
        <Stack.Screen name="profile-edit" options={{ headerShown: false, presentation: 'card' }} />
        <Stack.Screen name="settings" options={{ headerShown: false, presentation: 'card' }} />
      </Stack>
      <OfflineBanner />
    </ThemeProvider>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    backgroundColor: color.bg,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
