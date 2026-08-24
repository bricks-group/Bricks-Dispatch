import { Stack } from 'expo-router';

export default function CaptureLayout() {
  return (
    <Stack screenOptions={{ headerShown: false, presentation: 'fullScreenModal' }}>
      <Stack.Screen name="capture" />
      <Stack.Screen name="review" />
    </Stack>
  );
}
