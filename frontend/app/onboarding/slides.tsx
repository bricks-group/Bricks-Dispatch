import { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ArrowRight, Camera, UploadCloud, Wallet, MapPin, Clock, ChevronUp } from 'lucide-react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import BricksWordmark from '../../components/BricksWordmark';
import { EnterFadeDown, PressScale } from '../../components/motion';
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
    body: 'No bidding, no DMs. Keep your availability current so auto-assign only offers slots that fit your real schedule.',
    img: 'JOB OFFER CARD',
  },
  {
    eye: 'Tiered payouts',
    head: 'Tier C to Tier A — move up.',
    body: 'Paid on a published fixed rate card for www à-la-carte media (Photos only → Full suite), size, and tier — never a percent of rack. Virtual Visit is a paid add-on. Deliver jobs to move up tiers and unlock higher rates.',
    img: 'EARNINGS STRIPE CHART',
  },
];

function SlideArt({ index }: { index: number }) {
  if (index === 0) {
    return (
      <View style={s.artRow}>
        {[
          { Icon: Camera, label: 'SHOOT' },
          { Icon: UploadCloud, label: 'UPLOAD' },
          { Icon: Wallet, label: 'GET PAID' },
        ].map(({ Icon, label }) => (
          <View key={label} style={s.artTile}>
            <Icon size={28} color={TOKENS.brick} strokeWidth={1.5} />
            <Text style={s.artLabel}>{label}</Text>
          </View>
        ))}
      </View>
    );
  }
  if (index === 1) {
    return (
      <View style={s.artCard}>
        <Text style={s.artEye}>EXAMPLE JOB OFFER</Text>
        <Text style={s.artTitle}>Listing photos</Text>
        <View style={s.artLine}><MapPin size={14} color={TOKENS.moonFaint} strokeWidth={1.5} /><Text style={s.artMeta}>Address shown once assigned</Text></View>
        <View style={s.artLine}><Clock size={14} color={TOKENS.moonFaint} strokeWidth={1.5} /><Text style={s.artMeta}>Time window that fits your availability</Text></View>
      </View>
    );
  }
  return (
    <View style={s.artLadder}>
      {['TIER A', 'TIER B', 'TIER C'].map((t, i) => (
        <View key={t} style={[s.artStep, { width: `${92 - i * 18}%` as any, opacity: 1 - i * 0.25 }]}>
          {i === 0 ? <ChevronUp size={14} color="#FFF" strokeWidth={2} /> : null}
          <Text style={s.artStepText}>{t}</Text>
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

      <EnterFadeDown key={current} distance={10} duration={260} style={s.body}>
        {/* Illustration (drawn in-app, no stock imagery) */}
        <View style={s.imgPlaceholder}>
          <SlideArt index={current} />
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
          <PressScale style={s.btn} onPress={next}>
            <Text style={s.btnText}>{current === SLIDES.length - 1 ? 'GET STARTED' : 'NEXT'}</Text>
            <ArrowRight size={16} color="#FFF" strokeWidth={2} />
          </PressScale>
        </View>
      </EnterFadeDown>
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
  artRow: { flexDirection: 'row', gap: 12, paddingHorizontal: 16 },
  artTile: { flex: 1, aspectRatio: 0.8, borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: 2, alignItems: 'center', justifyContent: 'center', gap: 12 },
  artLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonSoft, letterSpacing: 1.4 },
  artCard: { width: '84%', padding: 18, borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: 2, backgroundColor: TOKENS.night, gap: 10 },
  artEye: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.brick, letterSpacing: 1.6 },
  artTitle: { fontFamily: TYPOGRAPHY.heading, fontSize: 18, color: TOKENS.moon },
  artLine: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  artMeta: { fontFamily: TYPOGRAPHY.body, fontSize: 12, color: TOKENS.moonSoft },
  artLadder: { width: '100%', alignItems: 'center', gap: 10 },
  artStep: { height: 44, backgroundColor: TOKENS.brick, borderRadius: 2, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 },
  artStepText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 11, color: '#FFF', letterSpacing: 1.4 },
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
