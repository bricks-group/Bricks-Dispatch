import { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Linking } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';
import { A1_BANNER_KEY, A1_BANNER_UNTIL, A1_GUIDE_LINE } from '../lib/legal-terms';

/** In-app notice of contractor agreement amendment A1. Dismissible; gone after the notice period. Same as web. */
export function AmendmentBanner() {
  const [hidden, setHidden] = useState(true);
  useEffect(() => {
    let live = true;
    AsyncStorage.getItem(A1_BANNER_KEY).then((v) => { if (live) setHidden(v === '1'); }, () => { if (live) setHidden(false); });
    return () => { live = false; };
  }, []);
  if (hidden || Date.now() >= A1_BANNER_UNTIL) return null;
  const close = () => {
    AsyncStorage.setItem(A1_BANNER_KEY, '1').catch(() => {});
    setHidden(true);
  };
  return (
    <View style={s.bar} accessibilityRole="summary" testID="amendment-banner">
      <Text style={s.text}>
        <Text style={s.strong}>Contractor agreement update.</Text> {A1_GUIDE_LINE} It applies from 16 November 2026, or from when you accept it. Questions? Email{' '}
        <Text style={s.link} onPress={() => Linking.openURL('mailto:help@brxs.ca')}>help@brxs.ca</Text>.
      </Text>
      <TouchableOpacity onPress={close} accessibilityRole="button" accessibilityLabel="Dismiss" hitSlop={8} testID="amendment-banner-close">
        <Text style={s.close}>Got it</Text>
      </TouchableOpacity>
    </View>
  );
}

const s = StyleSheet.create({
  bar: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, padding: 14, marginBottom: 16, borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: TOKENS.radius, backgroundColor: TOKENS.nightSoft },
  text: { flex: 1, fontFamily: TYPOGRAPHY.body, fontSize: 13, lineHeight: 20, color: TOKENS.moonSoft },
  strong: { fontFamily: TYPOGRAPHY.heading, color: TOKENS.moon },
  link: { color: TOKENS.moon, textDecorationLine: 'underline' },
  close: { fontFamily: TYPOGRAPHY.heading, fontSize: 13, color: TOKENS.moon, textDecorationLine: 'underline' },
});
