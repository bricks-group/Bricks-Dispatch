import { View, Text, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useIsOnline } from '../lib/offline';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';

/** Thin bar shown over every screen while the device has no connection. */
export default function OfflineBanner() {
  const online = useIsOnline();
  const insets = useSafeAreaInsets();
  if (online) return null;
  return (
    <View pointerEvents="none" style={[s.bar, { paddingTop: insets.top + 4 }]}>
      <Text style={s.text}>
        No connection. Showing jobs saved on this phone. Reconnect to upload files.
      </Text>
    </View>
  );
}

const s = StyleSheet.create({
  bar: {
    position: 'absolute', top: 0, left: 0, right: 0, zIndex: 999,
    backgroundColor: TOKENS.brick, paddingHorizontal: 16, paddingBottom: 6,
  },
  text: { color: '#fff', fontSize: 12, fontFamily: TYPOGRAPHY.body, textAlign: 'center' },
});
