import { Stack } from 'expo-router';
import { TOKENS } from '../../constants/Theme';

export default function AuthLayout() {
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: TOKENS.night },
        animation: 'fade',
      }}
    />
  );
}
