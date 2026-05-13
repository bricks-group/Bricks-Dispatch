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
import { ThemeProvider } from '../context/ThemeContext';
import { TOKENS } from '../constants/Theme';
import { actorHeaders } from '../lib/api';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.219:8002';

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
        await fetch(`${API}/api/push-tokens`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
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
  const { isDark } = useTheme();

  const [onboardingChecked, setOnboardingChecked] = useState(false);

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

    if (fontsLoaded && onboardingChecked) {
      SplashScreen.hideAsync().catch(() => {});
    }

    return () => clearTimeout(timer);
  }, [fontsLoaded, onboardingChecked]);

  if (!fontsLoaded) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="small" color={TOKENS.brick} />
      </View>
    );
  }

  return (
    <ThemeProvider>
      <StatusBar style={isDark ? 'light' : 'dark'} />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: isDark ? TOKENS.night : TOKENS.paper },
          animation: 'slide_from_right',
        }}
      >
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
