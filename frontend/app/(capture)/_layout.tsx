import { Stack } from 'expo-router';

export default function CaptureLayout() {
  return (
    <Stack screenOptions={{ headerShown: false, presentation: 'fullScreenModal' }}>
      <Stack.Screen name="mode" />
      <Stack.Screen name="capture" />
      <Stack.Screen name="floorplan" />
      <Stack.Screen name="review" />
    </Stack>
  );
}
