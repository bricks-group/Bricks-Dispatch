import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, StyleSheet, Platform } from 'react-native';
import { BrickLoader } from '../components/BricksWordmark';
import { useFonts } from 'expo-font';
import {
  JetBrainsMono_400Regular,
  JetBrainsMono_500Medium,
} from '@expo-google-fonts/jetbrains-mono';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect, useState } from 'react';
import { ThemeProvider, useTheme } from '../context/ThemeContext';
import { TOKENS } from '../constants/Theme';
import { actorHeaders } from '../lib/api';
import { supabase } from '../lib/supabase';
import type { Session } from '@supabase/supabase-js';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

SplashScreen.preventAutoHideAsync();

function useInitPushNotifications() {
  useEffect(() => {
    if (Platform.OS === 'web') return;
    (async () => {
      try {
        const Notifications = require('expo-notifications');
        const Device = require('expo-device');
        const Constants = require('expo-constants');

        Notifications.setNotificationHandler({
          handleNotification: async () => ({
            shouldPlaySound: true,
            shouldSetBadge: true,
            shouldShowBanner: true,
            shouldShowList: true,
          }),
        });

        if (Platform.OS === 'android') {
          await Notifications.setNotificationChannelAsync('default', {
            name: 'Bricks GO',
            importance: Notifications.AndroidImportance.MAX,
            vibrationPattern: [0, 250, 250, 250],
            lightColor: TOKENS.brick,
          });
        }

        if (!Device.isDevice) return;
        const { status: existing } = await Notifications.getPermissionsAsync();
        let finalStatus = existing;
        if (existing !== 'granted') {
          const { status } = await Notifications.requestPermissionsAsync();
          finalStatus = status;
        }
        if (finalStatus !== 'granted') return;

        const tokenData = await Notifications.getExpoPushTokenAsync();
        await fetch(`${API}/api/push-tokens`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: tokenData.data, platform: Platform.OS }),
        }).catch(() => {});
      } catch (e) {}
    })();
  }, []);
}

function ThemedStatusBar() {
  const { isDark } = useTheme();
  return <StatusBar style={isDark ? 'light' : 'dark'} />;
}

export default function RootLayout() {
  const router = useRouter();
  const segments = useSegments();
  const [session, setSession] = useState<Session | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [fontsLoaded] = useFonts({
    JetBrainsMono_400Regular,
    JetBrainsMono_500Medium,
  });

  const [onboardingChecked, setOnboardingChecked] = useState(false);

  // Auth session management
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session: s } }) => {
      setSession(s);
      setAuthChecked(true);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      if (event === 'SIGNED_OUT') setOnboardingChecked(false);
    });
    return () => subscription.unsubscribe();
  }, []);

  // Auth routing guard
  useEffect(() => {
    if (!authChecked || !fontsLoaded) return;
    const inAuth = segments[0] === '(auth)';
    if (!session && !inAuth) router.replace('/(auth)/login');
    else if (session && inAuth) router.replace('/(tabs)');
  }, [session, authChecked, fontsLoaded, segments]);

  useInitPushNotifications();

  useEffect(() => {
    if (!fontsLoaded || onboardingChecked) return;

    (async () => {
      try {
        const headers = await actorHeaders();
        const res = await fetch(`${API}/api/profile`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const profile = await res.json();
        
        const inOnboarding = segments[0] === 'onboarding';
        
        // If user is new/not approved, force onboarding
        if (profile.role === 'Applicant' && !inOnboarding) {
          router.replace('/onboarding/slides');
        }
      } catch (e) {
        console.error("Onboarding check failed", e);
      } finally {
        setOnboardingChecked(true);
      }
    })();
  }, [fontsLoaded, segments, onboardingChecked]);

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
        <BrickLoader />
      </View>
    );
  }

  return (
    <ThemeProvider>
      <ThemedStatusBar />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: TOKENS.night },
          animation: 'none',
        }}
      >
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen
          name="job/[id]"
          options={{
            headerShown: false,
            presentation: 'card',
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
    </ThemeProvider>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    backgroundColor: TOKENS.night,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
