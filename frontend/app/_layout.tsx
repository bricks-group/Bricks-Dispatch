import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet, Platform } from 'react-native';
import { useFonts } from 'expo-font';
import {
  JetBrainsMono_400Regular,
  JetBrainsMono_500Medium,
} from '@expo-google-fonts/jetbrains-mono';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect, useState } from 'react';
import { TOKENS } from '../constants/Theme';
import { actorHeaders } from '../lib/api';
import { supabase } from '../lib/supabase';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.206:8002';

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
            name: 'Bricks Dispatch',
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
        const headers = await actorHeaders({ 'Content-Type': 'application/json' });
        await fetch(`${API}/api/push-tokens`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ token: tokenData.data, platform: Platform.OS }),
        }).catch(() => {});
      } catch (e) {}
    })();
  }, []);
}

export default function RootLayout() {
  const router = useRouter();
  const segments = useSegments();
  const [fontsLoaded] = useFonts({
    JetBrainsMono_400Regular,
    JetBrainsMono_500Medium,
  });

  const [authChecked, setAuthChecked] = useState(false);
  const [onboardingChecked, setOnboardingChecked] = useState(false);

  useInitPushNotifications();

  // Auth guard: check session on mount and subscribe to auth state changes
  useEffect(() => {
    if (!fontsLoaded) return;

    let mounted = true;

    // Initial session check
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!mounted) return;
      const inAuth = segments[0] === '(auth)';
      if (!session && !inAuth) {
        router.replace('/(auth)/login');
      }
      setAuthChecked(true);
    });

    // Subscribe to future auth state changes
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (event, session) => {
        if (!mounted) return;
        const inAuth = segments[0] === '(auth)';
        if (event === 'SIGNED_OUT' || (!session && !inAuth)) {
          router.replace('/(auth)/login');
        } else if (event === 'SIGNED_IN' && session) {
          const inTabs = segments[0] === '(tabs)';
          if (!inTabs) {
            router.replace('/(tabs)');
          }
        }
      }
    );

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, [fontsLoaded, segments]);

  // Onboarding check: only runs once auth is confirmed
  useEffect(() => {
    if (!fontsLoaded || !authChecked || onboardingChecked) return;

    (async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) {
          setOnboardingChecked(true);
          return;
        }
        const headers = await actorHeaders();
        const res = await fetch(`${API}/api/profile`, { headers });
        const profile = await res.json();

        const inOnboarding = segments[0] === 'onboarding';

        if (profile.role === 'Applicant' && !inOnboarding) {
          router.replace('/onboarding/slides');
        }
      } catch (e) {
        console.error('Onboarding check failed', e);
      } finally {
        setOnboardingChecked(true);
      }
    })();
  }, [fontsLoaded, authChecked, segments, onboardingChecked]);

  useEffect(() => {
    const timer = setTimeout(() => {
      SplashScreen.hideAsync().catch(() => {});
    }, 3000);

    if (fontsLoaded && (authChecked || segments[0] === '(auth)')) {
      SplashScreen.hideAsync().catch(() => {});
    }

    return () => clearTimeout(timer);
  }, [fontsLoaded, authChecked, segments]);

  if (!fontsLoaded) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="small" color={TOKENS.brick} />
      </View>
    );
  }

  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: TOKENS.night },
          animation: 'slide_from_right',
        }}
      >
        <Stack.Screen name="(auth)" options={{ animation: 'fade' }} />
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
    </>
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
