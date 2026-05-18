import { Stack } from 'expo-router';
import { TOKENS } from '../../constants/Theme';

export default function OnboardingLayout() {
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: TOKENS.night },
        animation: 'none',
      }}
    />
  );
}
