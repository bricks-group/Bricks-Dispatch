import { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ArrowRight } from 'lucide-react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import BricksWordmark from '../../components/BricksWordmark';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const SLIDES = [
  {
    eye: 'Bricks GO · The photographer network',
    head: 'Shoot.\nUpload.\nGet paid.',
    body: 'Bricks routes jobs to you by tier and drive time. You shoot and upload — we handle editing and pay you out.',
    img: 'BRICKS GO FACADE',
  },
  {
    eye: 'Auto-dispatch',
    head: 'Jobs come to you.',
    body: 'No bidding, no DMs. Every shoot is confirmed, timed, and routed before it reaches your phone.',
    img: 'JOB OFFER CARD',
  },
  {
    eye: 'Tiered payouts',
    head: 'Tier C to Tier A — move up.',
    body: 'Start at 35%. Every delivered job builds your score. Tier A specialists earn 55% of each shoot.',
    img: 'EARNINGS STRIPE CHART',
  },
];

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

  const skip = () => finish();

  const slide = SLIDES[current];

  return (
    <SafeAreaView style={s.container}>
      {/* Top bar: wordmark + skip */}
      <View style={s.topBar}>
        <BricksWordmark size={0.85} color={TOKENS.brick} pressable />
        <TouchableOpacity onPress={skip} activeOpacity={0.7}>
          <Text style={s.skip}>Skip</Text>
        </TouchableOpacity>
      </View>

      <View style={s.body}>
        {/* Image placeholder */}
        <View style={s.imgPlaceholder}>
          <Text style={s.imgLabel}>{slide.img}</Text>
        </View>

        {/* Copy */}
        <View style={s.copy}>
          <Text style={s.eye}>{slide.eye}</Text>
          <Text style={s.head}>{slide.head}</Text>
          <Text style={s.desc}>{slide.body}</Text>
        </View>

        {/* Progress bars + CTA */}
        <View style={s.footer}>
          <View style={s.bars}>
            {SLIDES.map((_, i) => (
              <View
                key={i}
                style={[s.bar, { flex: i === current ? 2 : 1, backgroundColor: i === current ? TOKENS.brick : TOKENS.nightHairS }]}
              />
            ))}
          </View>
          <TouchableOpacity style={s.btn} onPress={next} activeOpacity={0.85}>
            <Text style={s.btnText}>{current === SLIDES.length - 1 ? 'GET STARTED' : 'NEXT'}</Text>
            <ArrowRight size={16} color="#FFF" strokeWidth={2} />
          </TouchableOpacity>
        </View>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },

  topBar: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 24, paddingVertical: 12 },
  skip: { fontFamily: TYPOGRAPHY.body, fontSize: 12, color: TOKENS.moonFaint },

  body: { flex: 1, paddingHorizontal: 24, paddingBottom: 24, flexDirection: 'column' },

  imgPlaceholder: {
    flex: 1,
    backgroundColor: TOKENS.nightSoft,
    borderWidth: 1,
    borderColor: TOKENS.nightHair,
    borderRadius: 2,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 32,
  },
  imgLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 10, color: TOKENS.moonFaint, letterSpacing: 1.4 },

  copy: { marginBottom: 32 },
  eye: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 10, color: TOKENS.brick, letterSpacing: 1.8, marginBottom: 10 },
  head: { fontSize: 28, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.8, lineHeight: 34, marginBottom: 10 },
  desc: { fontFamily: TYPOGRAPHY.body, fontSize: 14, color: TOKENS.moonSoft, lineHeight: 22 },

  footer: {},
  bars: { flexDirection: 'row', gap: 6, height: 2, marginBottom: 20 },
  bar: { height: 2, borderRadius: 1 },
  btn: { backgroundColor: TOKENS.brick, paddingVertical: 16, borderRadius: 2, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 },
  btnText: { color: '#FFF', fontFamily: TYPOGRAPHY.monoMedium, fontSize: 13, letterSpacing: 1.2 },
});
