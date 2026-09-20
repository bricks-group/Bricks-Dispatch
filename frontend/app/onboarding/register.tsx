import React, { useEffect, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet,
  ActivityIndicator, Alert, Image,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { ArrowRight, Check, Camera } from 'lucide-react-native';
import * as ImagePicker from 'expo-image-picker';
import { api } from '../../lib/api';
import BricksAOSelector from '../../components/BricksAOSelector';
import { EnterFadeDown, PressScale } from '../../components/motion';
import { PORTFOLIO_TYPES } from '../../lib/vendor';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

const TOKENS = {
  night: '#141210',
  nightSoft: '#1C1A17',
  nightHair: '#F4F1EB14',
  nightHairS: '#F4F1EB26',
  moon: '#F4F1EB',
  moonSoft: '#D6CFC2',
  moonMute: '#8F8678',
  moonFaint: '#5E574C',
  brick: '#D2542B',
};

type LocalSample = {
  media_type: string;
  link: string;
  uri?: string;
  fileName?: string;
  mimeType?: string;
};

async function fileToBase64(uri: string): Promise<string> {
  const res = await fetch(uri);
  const blob = await res.blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = String(reader.result || '');
      const b64 = result.includes(',') ? result.split(',')[1] : result;
      resolve(b64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export default function OnboardingRegister() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [agreement, setAgreement] = useState<{
    version: string;
    effective_date: string;
    text: string;
  } | null>(null);
  const [agreementError, setAgreementError] = useState('');
  const [samples, setSamples] = useState<Record<string, LocalSample>>(
    Object.fromEntries(PORTFOLIO_TYPES.map((t) => [t.id, { media_type: t.id, link: '' }]))
  );
  const [form, setForm] = useState({
    name: '',
    email: '',
    phone: '',
    city: '',
    specialties: ['photo'],
    equipment_list: ['Camera'],
    preferred_regions: [] as string[],
    operation_polygon: [] as any[],
    max_travel_km: '50',
    accepted_terms: false,
    hst_exempt: true,
  });

  const loadAgreement = async () => {
    setAgreementError('');
    try {
      const data = await api.onboarding.getAgreement();
      setAgreement(data);
    } catch (e) {
      setAgreement(null);
      setForm((prev) => ({ ...prev, accepted_terms: false }));
      setAgreementError('Could not load the contractor agreement. Tap to retry.');
    }
  };

  useEffect(() => {
    loadAgreement();
  }, []);

  const pickSampleImage = async (mediaType: string) => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert('Permission required', 'Allow photo library access to upload portfolio samples.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.85,
      allowsEditing: false,
    });
    if (result.canceled || !result.assets?.length) return;
    const asset = result.assets[0];
    setSamples((prev) => ({
      ...prev,
      [mediaType]: {
        ...prev[mediaType],
        media_type: mediaType,
        uri: asset.uri,
        fileName: asset.fileName || `${mediaType}.jpg`,
        mimeType: asset.mimeType || 'image/jpeg',
      },
    }));
  };

  const handleApply = async () => {
    if (!form.name || !form.email || !form.phone || !form.accepted_terms || !agreement) {
      Alert.alert('REQUIRED FIELDS', 'Please complete all required fields and accept the contractor agreement.');
      return;
    }

    const portfolio_samples = Object.values(samples)
      .filter((s) => s.link.trim() || s.uri)
      .map((s) => ({
        media_type: s.media_type,
        link: s.link.trim() || undefined,
      }));
    const portfolio_links = Object.values(samples)
      .map((s) => s.link.trim())
      .filter(Boolean);

    if (portfolio_samples.length === 0 && portfolio_links.length === 0) {
      Alert.alert('PORTFOLIO', 'Add at least one sample image or link (HDR, drone, floor plan, tour, twilight, or reel).');
      return;
    }

    setLoading(true);
    try {
      const result = await api.onboarding.apply({
        ...form,
        max_travel_km: parseInt(form.max_travel_km, 10),
        operation_polygon: form.operation_polygon,
        ic_agreement_version: agreement.version,
        portfolio_links,
        portfolio_samples,
        hst_exempt: true,
      });
      if (!result.success) {
        Alert.alert('ERROR', result.detail || 'Failed to submit application.');
        return;
      }

      const memberId = result.member_id as string;
      const token = result.application_token as string | undefined;
      const withImages = Object.values(samples).filter((s) => s.uri);
      for (const sample of withImages) {
        try {
          const file_data = await fileToBase64(sample.uri!);
          const body = new FormData();
          body.append('member_id', memberId);
          body.append('media_type', sample.media_type);
          body.append('filename', sample.fileName || `${sample.media_type}.jpg`);
          body.append('file_data', file_data);
          if (sample.link.trim()) body.append('link', sample.link.trim());
          if (token) body.append('token', token);
          await fetch(`${API}/api/onboarding/portfolio`, { method: 'POST', body });
        } catch (e) {
          console.warn('portfolio upload failed', sample.media_type, e);
        }
      }

      Alert.alert(
        'APPLICATION RECEIVED',
        'Your application is under review. You will get access after approval.',
        [{ text: 'OK', onPress: () => router.replace('/onboarding/under-review' as any) }],
      );
    } catch (e) {
      Alert.alert('ERROR', 'Connection failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
    <ScrollView style={styles.container} contentContainerStyle={styles.content} nestedScrollEnabled>
      <EnterFadeDown>
      <View style={styles.headerBox}>
        <Text style={styles.header}>join the crew</Text>
        <Text style={styles.subhead}>ONTARIO FIELD OPERATIONS</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.label}>FULL NAME</Text>
        <TextInput
          style={styles.input}
          placeholder="ALEX RIVERA"
          placeholderTextColor={TOKENS.moonFaint}
          value={form.name}
          onChangeText={(v) => setForm({ ...form, name: v })}
          data-testid="onboarding-name"
        />

        <Text style={styles.label}>EMAIL ADDRESS</Text>
        <TextInput
          style={styles.input}
          placeholder="ALEX@EXAMPLE.COM"
          placeholderTextColor={TOKENS.moonFaint}
          keyboardType="email-address"
          autoCapitalize="none"
          value={form.email}
          onChangeText={(v) => setForm({ ...form, email: v })}
          data-testid="onboarding-email"
        />

        <Text style={styles.label}>PHONE</Text>
        <TextInput
          style={styles.input}
          placeholder="416-555-0100"
          placeholderTextColor={TOKENS.moonFaint}
          keyboardType="phone-pad"
          value={form.phone}
          onChangeText={(v) => setForm({ ...form, phone: v })}
          data-testid="onboarding-phone"
        />

        <View style={styles.row}>
          <View style={{ flex: 1, marginRight: 8 }}>
            <Text style={styles.label}>CITY</Text>
            <TextInput
              style={styles.input}
              placeholder="TORONTO"
              placeholderTextColor={TOKENS.moonFaint}
              value={form.city}
              onChangeText={(v) => setForm({ ...form, city: v })}
              data-testid="onboarding-city"
            />
          </View>
          <View style={{ width: 100 }}>
            <Text style={styles.label}>RADIUS (KM)</Text>
            <TextInput
              style={styles.input}
              placeholder="50"
              placeholderTextColor={TOKENS.moonFaint}
              keyboardType="numeric"
              value={form.max_travel_km}
              onChangeText={(v) => setForm({ ...form, max_travel_km: v })}
              data-testid="onboarding-radius"
            />
          </View>
        </View>

        <BricksAOSelector
          onPolygonComplete={(path: any) => setForm({ ...form, operation_polygon: path })}
        />
      </View>

      <View style={styles.section}>
        <Text style={styles.label}>PORTFOLIO BY TYPE</Text>
        <Text style={styles.hint}>
          Upload a sample for each media type you shoot. Links are optional and supplemental.
        </Text>
        {PORTFOLIO_TYPES.map((t) => {
          const sample = samples[t.id];
          return (
            <View key={t.id} style={styles.sampleCard} data-testid={`onboarding-portfolio-${t.id}`}>
              <Text style={styles.sampleTitle}>{t.label.toUpperCase()}</Text>
              <TouchableOpacity
                style={styles.uploadRow}
                onPress={() => pickSampleImage(t.id)}
                activeOpacity={0.85}
              >
                {sample.uri ? (
                  <Image source={{ uri: sample.uri }} style={styles.thumb} />
                ) : (
                  <View style={styles.thumbPlaceholder}>
                    <Camera size={16} color={TOKENS.moonMute} />
                  </View>
                )}
                <Text style={styles.uploadLabel}>
                  {sample.uri ? 'Change image' : 'Upload image'}
                </Text>
              </TouchableOpacity>
              <TextInput
                style={styles.input}
                placeholder="Optional link (https://…)"
                placeholderTextColor={TOKENS.moonFaint}
                autoCapitalize="none"
                value={sample.link}
                onChangeText={(v) =>
                  setSamples((prev) => ({
                    ...prev,
                    [t.id]: { ...prev[t.id], link: v },
                  }))
                }
              />
            </View>
          );
        })}
      </View>

      <View style={styles.section}>
        <Text style={styles.label}>CONTRACTOR AGREEMENT</Text>
        {agreement ? (
          <>
            <Text style={styles.agreementMeta}>V{agreement.version} · EFFECTIVE {agreement.effective_date}</Text>
            <ScrollView style={styles.agreementBox} nestedScrollEnabled data-testid="onboarding-agreement-text">
              <Text style={styles.agreementText} selectable>{agreement.text}</Text>
            </ScrollView>
          </>
        ) : agreementError ? (
          <TouchableOpacity onPress={loadAgreement} activeOpacity={0.8}>
            <Text style={styles.agreementError}>{agreementError}</Text>
          </TouchableOpacity>
        ) : (
          <Text style={styles.agreementMeta}>LOADING AGREEMENT…</Text>
        )}
      </View>

      <TouchableOpacity
        style={styles.termsRow}
        onPress={() => {
          if (!agreement) return;
          setForm({ ...form, accepted_terms: !form.accepted_terms });
        }}
        activeOpacity={0.8}
        disabled={!agreement}
        data-testid="onboarding-terms"
      >
        <View style={[styles.checkbox, form.accepted_terms && styles.checkboxChecked]}>
          {form.accepted_terms && <Check size={14} color="#FFF" strokeWidth={3} />}
        </View>
        <Text style={styles.termsText}>
          {agreement
            ? `I ACCEPT THE BRICKS CONTRACTOR AGREEMENT V${agreement.version} (ONTARIO)`
            : 'I ACCEPT THE BRICKS CONTRACTOR AGREEMENT (ONTARIO)'}
        </Text>
      </TouchableOpacity>

      <PressScale
        style={styles.button}
        onPress={handleApply}
        disabled={loading || !agreement}
        data-testid="onboarding-submit"
      >
        {loading ? (
          <ActivityIndicator color="#FFF" />
        ) : (
          <View style={styles.btnInner}>
            <Text style={styles.buttonText}>SUBMIT APPLICATION</Text>
            <ArrowRight size={18} color="#FFF" strokeWidth={2} />
          </View>
        )}
      </PressScale>

      <View style={{ height: 60 }} />
      </EnterFadeDown>
    </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: TOKENS.night },
  container: { flex: 1, backgroundColor: TOKENS.night },
  content: { padding: 36 },
  headerBox: { marginTop: 16, marginBottom: 36 },
  header: {
    fontSize: 34, fontFamily: 'System', color: TOKENS.moon,
    letterSpacing: -1.1, textTransform: 'lowercase',
  },
  subhead: {
    fontSize: 10.5, fontFamily: 'IoskeleyMono-Medium', color: TOKENS.moonMute,
    letterSpacing: 1.8, marginTop: 8,
  },
  section: { marginBottom: 28 },
  label: {
    fontSize: 10, fontFamily: 'IoskeleyMono-Medium', color: TOKENS.moonMute,
    letterSpacing: 1.6, marginBottom: 10, textTransform: 'uppercase',
  },
  hint: {
    fontSize: 12, color: TOKENS.moonSoft, marginBottom: 14, lineHeight: 18,
  },
  row: { flexDirection: 'row' },
  input: {
    backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair,
    padding: 14, color: TOKENS.moon, fontSize: 13, fontFamily: 'IoskeleyMono-Regular',
    borderRadius: 2, marginBottom: 18,
  },
  sampleCard: {
    borderWidth: 1, borderColor: TOKENS.nightHair, backgroundColor: TOKENS.nightSoft,
    borderRadius: 2, padding: 12, marginBottom: 14,
  },
  sampleTitle: {
    fontSize: 10, fontFamily: 'IoskeleyMono-Medium', color: TOKENS.moonMute,
    letterSpacing: 1.4, marginBottom: 10,
  },
  uploadRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 10 },
  thumb: { width: 48, height: 48, borderRadius: 2 },
  thumbPlaceholder: {
    width: 48, height: 48, borderRadius: 2, borderWidth: 1, borderColor: TOKENS.nightHairS,
    alignItems: 'center', justifyContent: 'center', backgroundColor: TOKENS.night,
  },
  uploadLabel: { color: TOKENS.moonSoft, fontSize: 12, fontFamily: 'IoskeleyMono-Medium' },
  button: {
    backgroundColor: TOKENS.brick, paddingVertical: 14, paddingHorizontal: 22,
    borderRadius: 2, alignItems: 'center', marginTop: 12,
  },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  buttonText: { color: '#FFF', fontFamily: 'System', fontSize: 14 },
  termsRow: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 24, gap: 12 },
  checkbox: {
    width: 20, height: 20, borderWidth: 1, borderColor: TOKENS.nightHairS,
    borderRadius: 2, alignItems: 'center', justifyContent: 'center', marginTop: 2,
  },
  checkboxChecked: { backgroundColor: TOKENS.brick, borderColor: TOKENS.brick },
  termsText: {
    color: TOKENS.moonSoft, fontSize: 10, fontFamily: 'IoskeleyMono-Medium',
    lineHeight: 16, flex: 1,
  },
  agreementMeta: {
    fontSize: 10, fontFamily: 'IoskeleyMono-Medium', color: TOKENS.moonFaint,
    letterSpacing: 1.2, marginBottom: 10,
  },
  agreementBox: {
    maxHeight: 280, backgroundColor: TOKENS.nightSoft, borderWidth: 1,
    borderColor: TOKENS.nightHair, borderRadius: 2, padding: 12,
  },
  agreementText: {
    color: TOKENS.moonSoft, fontSize: 11, fontFamily: 'IoskeleyMono-Regular', lineHeight: 16,
  },
  agreementError: {
    color: TOKENS.brick, fontSize: 11, fontFamily: 'IoskeleyMono-Medium', lineHeight: 16,
  },
});
