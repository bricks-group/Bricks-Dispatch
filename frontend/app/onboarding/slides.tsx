import { useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ArrowRight, Camera, UploadCloud, Wallet, MapPin, Clock, ChevronUp } from 'lucide-react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import BricksWordmark from '../../components/BricksWordmark';
import { EnterFadeDown } from '../../components/motion';
import { Button, Overline, TextLink, Pill } from '../../components/ui';
import { color, type as T, space, radius, layout, hairline } from '../../theme';

const SLIDES = [
  {
    eye: 'Bricks',
    head: 'Real estate shoots on your schedule.',
    body: 'Jobs are routed to you by tier and drive time. You shoot and upload; we edit and pay you.',
  },
  {
    eye: 'Auto-dispatch',
    head: 'Jobs come to you.',
    body: 'No bidding, no DMs. Connect a calendar so auto-assign only offers jobs that fit your real schedule. Manual hours still apply if none is connected.',
  },
  {
    eye: 'Tiered payouts',
    head: 'Move up a tier.',
    body: 'A fixed, published rate card by media, home size and tier. Never a percentage. Deliver well to unlock higher rates.',
  },
];

function SlideArt({ index }: { index: number }) {
  if (index === 0) {
    return (
      <View style={s.steps}>
        {[
          { Icon: Camera, label: 'Shoot' },
          { Icon: UploadCloud, label: 'Upload' },
          { Icon: Wallet, label: 'Get paid' },
        ].map(({ Icon, label }, i) => (
          <View key={label} style={[s.step, i > 0 && s.stepDivider]}>
            <Text style={[T.dataSm, { color: color.textFaint }]}>{`0${i + 1}`}</Text>
            <Text style={[T.title, s.flex, { color: color.text }]}>{label}</Text>
            <Icon size={24} color={color.accent} strokeWidth={layout.iconStroke} />
          </View>
        ))}
      </View>
    );
  }
  if (index === 1) {
    return (
      <View style={s.offer}>
        <Pill label="New offer" tone="accent" dot />
        <Text style={[T.headline, { color: color.text }]}>Listing photos</Text>
        <View style={s.offerLine}>
          <MapPin size={layout.icon} color={color.textMuted} strokeWidth={layout.iconStroke} />
          <Text style={[T.bodySm, { color: color.textBody }]}>Address shown once assigned</Text>
        </View>
        <View style={s.offerLine}>
          <Clock size={layout.icon} color={color.textMuted} strokeWidth={layout.iconStroke} />
          <Text style={[T.bodySm, { color: color.textBody }]}>A window that fits your availability</Text>
        </View>
      </View>
    );
  }
  return (
    <View style={s.ladder}>
      {['Tier A', 'Tier B', 'Tier C'].map((t, i) => (
        <View key={t} style={[s.rung, { width: `${100 - i * 18}%` as any, opacity: 1 - i * 0.28 }]}>
          {i === 0 ? <ChevronUp size={16} color={color.textOnAccent} strokeWidth={2} /> : null}
          <Text style={[T.callout, { color: color.textOnAccent }]}>{t}</Text>
        </View>
      ))}
    </View>
  );
}

export default function OnboardingSlides() {
  const [current, setCurrent] = useState(0);
  const router = useRouter();

  const finish = async () => {
    await AsyncStorage.setItem('welcome_seen', '1');
    router.replace('/(auth)/login' as any);
  };

  const next = () => {
    if (current < SLIDES.length - 1) setCurrent(current + 1);
    else finish();
  };

  const slide = SLIDES[current];
  const last = current === SLIDES.length - 1;

  return (
    <SafeAreaView style={s.container}>
      <View style={s.topBar}>
        <BricksWordmark size={0.85} color={color.accent} pressable />
        <TextLink title="Skip" onPress={finish} />
      </View>

      <EnterFadeDown key={current} style={s.body}>
        {/* Drawn in-app; no tray around it (Kam's lock: no painted borders around media). */}
        <View style={s.art}>
          <SlideArt index={current} />
        </View>

        <View style={s.copy}>
          <Overline tone="accent" style={s.eye}>{slide.eye}</Overline>
          <Text style={[T.display, { color: color.text }]} accessibilityRole="header">{slide.head}</Text>
          <Text style={[T.body, s.desc]}>{slide.body}</Text>
        </View>

        <View style={s.progress} accessibilityLabel={`Step ${current + 1} of ${SLIDES.length}`}>
          {SLIDES.map((_, i) => (
            <View key={i} style={[s.bar, i === current ? s.barOn : null]} />
          ))}
        </View>
        <Button title={last ? 'Get started' : 'Next'} iconRight={ArrowRight} onPress={next} />
      </EnterFadeDown>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1 },
  container: { flex: 1, backgroundColor: color.bg },
  topBar: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingLeft: layout.gutter, paddingRight: space[12], minHeight: 56,
  },
  body: { flex: 1, paddingHorizontal: layout.gutter, paddingBottom: space[16] },

  art: { flex: 1, justifyContent: 'center' },
  steps: { borderTopWidth: hairline, borderBottomWidth: hairline, borderColor: color.hair },
  step: { flexDirection: 'row', alignItems: 'center', gap: space[16], paddingVertical: space[20] },
  stepDivider: { borderTopWidth: hairline, borderTopColor: color.hair },
  offer: {
    gap: space[12], padding: space[20], borderRadius: radius.sm,
    backgroundColor: color.surface, borderWidth: hairline, borderColor: color.hairStrong,
  },
  offerLine: { flexDirection: 'row', alignItems: 'center', gap: space[10] },
  ladder: { alignItems: 'center', gap: space[10] },
  rung: {
    height: layout.control, backgroundColor: color.accent, borderRadius: radius.sm,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space[6],
  },

  copy: { marginTop: space[32], marginBottom: space[28] },
  eye: { marginBottom: space[12] },
  desc: { color: color.textBody, marginTop: space[12] },

  progress: { flexDirection: 'row', gap: space[6], marginBottom: space[20] },
  bar: { flex: 1, height: 2, backgroundColor: color.hairStrong },
  barOn: { flex: 2, backgroundColor: color.accent },
});
