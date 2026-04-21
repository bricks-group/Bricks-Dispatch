import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Dimensions } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ArrowRight, Camera, Map, Zap } from 'lucide-react-native';
import { TOKENS } from '../../constants/Theme';

const { width } = Dimensions.get('window');

const SLIDES = [
  {
    title: 'Focus on the art.',
    desc: 'We handle the booking, billing, and logistics so you can focus on making great images.',
    icon: Camera,
  },
  {
    title: 'Claim your territory.',
    desc: 'Define your Area of Operations (AO) and get assigned shoots within your exact fence.',
    icon: Map,
  },
  {
    title: 'Get paid faster.',
    desc: 'Instant payouts and transparent tracking. No more chasing invoices.',
    icon: Zap,
  }
];

export default function OnboardingSlides() {
  const [current, setCurrent] = useState(0);
  const router = useRouter();

  const next = () => {
    if (current < SLIDES.length - 1) {
      setCurrent(current + 1);
    } else {
      router.push('/onboarding/register');
    }
  };

  const Slide = SLIDES[current];
  const Icon = Slide.icon;

  return (
    <SafeAreaView style={s.ctn}>
      <View style={s.content}>
        <View style={s.iconBox}>
          <Icon size={48} color={TOKENS.brick} strokeWidth={1} />
        </View>
        
        <Text style={s.title}>{Slide.title}</Text>
        <Text style={s.desc}>{Slide.desc}</Text>
        
        <View style={s.dots}>
          {SLIDES.map((_, i) => (
            <View key={i} style={[s.dot, i === current && s.dotActive]} />
          ))}
        </View>
      </View>

      <View style={s.footer}>
        <TouchableOpacity style={s.btn} onPress={next}>
          <Text style={s.btnText}>{current === SLIDES.length - 1 ? 'GET STARTED' : 'NEXT'}</Text>
          <ArrowRight size={18} color="#FFF" />
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  ctn: { flex: 1, backgroundColor: TOKENS.night },
  content: { flex: 1, padding: 48, justifyContent: 'center', alignItems: 'center' },
  iconBox: { marginBottom: 48 },
  title: { fontSize: 34, fontFamily: 'System', color: TOKENS.moon, letterSpacing: -1.1, textAlign: 'center', marginBottom: 16 },
  desc: { fontSize: 16, fontFamily: 'System', color: TOKENS.moonSoft, textAlign: 'center', lineHeight: 24 },
  dots: { flexDirection: 'row', gap: 8, marginTop: 48 },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: TOKENS.nightHairS },
  dotActive: { backgroundColor: TOKENS.brick, width: 20 },
  footer: { padding: 48, borderTopWidth: 1, borderColor: TOKENS.nightHair },
  btn: { backgroundColor: TOKENS.brick, padding: 18, borderRadius: 2, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 12 },
  btnText: { color: '#FFF', fontFamily: 'JetBrainsMono-Medium', fontSize: 14, letterSpacing: 1 },
});
