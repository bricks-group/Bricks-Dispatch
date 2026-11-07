import { Stack } from 'expo-router';
import { useReducedMotion } from 'react-native-reanimated';

/**
 * Capture stack: opacity (fade) between screens only.
 * Do not change capture pipeline / upload / Fair Use media logic in route screens.
 */
export default function CaptureLayout() {
  const reduced = useReducedMotion();
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        presentation: 'fullScreenModal',
        // Opacity-only transitions between capture steps
        animation: 'fade',
        animationDuration: reduced ? 120 : 220,
      }}
    >
      <Stack.Screen name="mode" />
      <Stack.Screen name="capture" />
      <Stack.Screen name="floorplan" />
      <Stack.Screen name="visit" />
      <Stack.Screen name="visit-video" />
      <Stack.Screen name="visit-prep" />
      <Stack.Screen name="visit-record" options={{ gestureEnabled: false }} />
      <Stack.Screen name="visit-review" />
      <Stack.Screen name="review" />
    </Stack>
  );
}
