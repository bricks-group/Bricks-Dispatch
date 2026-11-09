import { Pressable, Text, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { color, type as T, space, hairline } from '../theme';

/**
 * Shown when GO couldn't check the contractor agreement at sign-in (offline,
 * server busy). Sign-in stays open; the server still refuses to accept or
 * start jobs until the agreement is accepted, so this offers a retry.
 */
export default function TermsRetryBanner({ visible, onRetry }: { visible: boolean; onRetry: () => void }) {
  const insets = useSafeAreaInsets();
  if (!visible) return null;
  return (
    <View style={[s.bar, { paddingTop: insets.top + space[4] }]} accessibilityLiveRegion="polite">
      <Text style={[T.caption, s.text]}>We couldn't check your contractor agreement. You can't accept or start jobs until it's checked.</Text>
      <Pressable onPress={onRetry} accessibilityRole="button" hitSlop={8} style={s.btn}>
        <Text style={[T.caption, s.btnText]}>Retry</Text>
      </Pressable>
    </View>
  );
}

const s = StyleSheet.create({
  bar: {
    position: 'absolute', top: 0, left: 0, right: 0, zIndex: 998,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space[8],
    backgroundColor: color.surfaceDeep, borderBottomWidth: hairline, borderBottomColor: color.hairStrong,
    paddingHorizontal: space[16], paddingBottom: space[8],
  },
  text: { color: color.text, flexShrink: 1 },
  btn: { borderWidth: hairline, borderColor: color.hairStrong, paddingHorizontal: space[8], paddingVertical: space[4], borderRadius: 0 },
  btnText: { color: color.text },
});
