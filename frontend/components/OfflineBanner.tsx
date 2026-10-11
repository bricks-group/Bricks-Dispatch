import { View, Text, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useIsOnline } from '../lib/offline';
import { color, type as T, space, hairline, radius } from '../theme';

/** Thin bar shown over every screen while the device has no connection. */
export default function OfflineBanner() {
  const online = useIsOnline();
  const insets = useSafeAreaInsets();
  if (online) return null;
  return (
    <View pointerEvents="none" style={[s.bar, { paddingTop: insets.top + space[4] }]} accessibilityLiveRegion="polite">
      <View style={s.dot} />
      <Text style={[T.caption, s.text]}>Offline. Showing jobs saved on this phone; uploads resume when you reconnect.</Text>
    </View>
  );
}

const s = StyleSheet.create({
  bar: {
    position: 'absolute', top: 0, left: 0, right: 0, zIndex: 999,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space[8],
    backgroundColor: color.surfaceDeep, borderBottomWidth: hairline, borderBottomColor: color.hairStrong,
    paddingHorizontal: space[16], paddingBottom: space[8],
  },
  dot: { width: 6, height: 6, borderRadius: radius.pill, backgroundColor: color.warn },
  text: { color: color.text, flexShrink: 1 },
});
