import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, Pressable, StyleSheet, Alert, Image } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { ArrowRight, Check as CheckIcon, Camera } from 'lucide-react-native';
import * as ImagePicker from 'expo-image-picker';
import { api } from '../../lib/api';
import BricksAOSelector from '../../components/BricksAOSelector';
import { EnterFadeDown } from '../../components/motion';
import { NavBar, Section, Group, Field, Button, Notice, Overline, LoadingState } from '../../components/ui';
import { color, type as T, space, radius, layout, hairline } from '../../theme';
import { PORTFOLIO_TYPES } from '../../lib/vendor';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

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
      Alert.alert('Required fields', 'Please complete all required fields and accept the contractor agreement.');
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
      Alert.alert('Portfolio', 'Add at least one sample image or link (HDR, drone, floor plan, tour, twilight, or reel).');
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
        Alert.alert('Error', result.detail || 'Failed to submit application.');
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
        'Application received',
        'Your application is under review. You will get access after approval.',
        [{ text: 'OK', onPress: () => router.replace('/onboarding/under-review' as any) }],
      );
    } catch (e) {
      Alert.alert('Error', 'Connection failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <NavBar title="Apply" onBack={router.canGoBack() ? () => router.back() : undefined} border={false} />
      <ScrollView style={styles.flex} contentContainerStyle={styles.content} nestedScrollEnabled keyboardShouldPersistTaps="handled">
        <EnterFadeDown>
          <View style={styles.headerBox}>
            <Overline>Ontario field operations</Overline>
            <Text style={[T.display, { color: color.text }]} accessibilityRole="header">Join the crew</Text>
            <Text style={[T.body, { color: color.textMuted }]}>A few details, a few samples, and you’re in the queue.</Text>
          </View>

          <Section first label="About you">
            <View style={styles.fields}>
              <Field label="Full name" placeholder="Alex Rivera" value={form.name} onChangeText={(v) => setForm({ ...form, name: v })} textContentType="name" testID="onboarding-name" />
              <Field label="Email" placeholder="alex@example.com" keyboardType="email-address" autoCapitalize="none" value={form.email} onChangeText={(v) => setForm({ ...form, email: v })} textContentType="emailAddress" testID="onboarding-email" />
              <Field label="Phone" placeholder="416-555-0100" keyboardType="phone-pad" value={form.phone} onChangeText={(v) => setForm({ ...form, phone: v })} textContentType="telephoneNumber" testID="onboarding-phone" />
              <View style={styles.row}>
                <View style={styles.flex}>
                  <Field label="City" placeholder="Toronto" value={form.city} onChangeText={(v) => setForm({ ...form, city: v })} testID="onboarding-city" />
                </View>
                <View style={styles.radius}>
                  <Field label="Radius (km)" placeholder="50" keyboardType="numeric" value={form.max_travel_km} onChangeText={(v) => setForm({ ...form, max_travel_km: v })} testID="onboarding-radius" />
                </View>
              </View>
            </View>
            <View style={styles.gapTop}>
              <BricksAOSelector onPolygonComplete={(path: any) => setForm({ ...form, operation_polygon: path })} />
            </View>
          </Section>

          <Section label="Portfolio">
            <Text style={[T.bodySm, styles.hint]}>Add a sample for each type you shoot. Links are optional.</Text>
            <Group>
              {PORTFOLIO_TYPES.map((t) => {
                const sample = samples[t.id];
                return (
                  <View key={t.id} style={styles.sample} testID={`onboarding-portfolio-${t.id}`}>
                    <Pressable style={styles.uploadRow} onPress={() => pickSampleImage(t.id)} accessibilityRole="button" accessibilityLabel={`${sample.uri ? 'Change' : 'Upload'} ${t.label} sample`}>
                      {sample.uri ? (
                        <Image source={{ uri: sample.uri }} style={styles.thumb} />
                      ) : (
                        <View style={styles.thumbPlaceholder}><Camera size={layout.icon} color={color.textMuted} strokeWidth={layout.iconStroke} /></View>
                      )}
                      <View style={styles.flex}>
                        <Text style={[T.bodyMedium, { color: color.text }]}>{t.label}</Text>
                        <Text style={[T.caption, { color: sample.uri ? color.ok : color.textMuted }]}>{sample.uri ? 'Image added · tap to change' : 'Tap to add an image'}</Text>
                      </View>
                    </Pressable>
                    <Field
                      placeholder="Optional link (https://…)"
                      autoCapitalize="none"
                      keyboardType="url"
                      value={sample.link}
                      onChangeText={(v) => setSamples((prev) => ({ ...prev, [t.id]: { ...prev[t.id], link: v } }))}
                    />
                  </View>
                );
              })}
            </Group>
          </Section>

          <Section label="Contractor agreement">
            {agreement ? (
              <>
                <Text style={[T.dataSm, styles.hint]}>Version {agreement.version} · effective {agreement.effective_date}</Text>
                <ScrollView style={styles.agreementBox} nestedScrollEnabled testID="onboarding-agreement-text">
                  <Text style={[T.bodySm, { color: color.textBody }]} selectable>{agreement.text}</Text>
                </ScrollView>
              </>
            ) : agreementError ? (
              <Notice tone="bad">{agreementError}</Notice>
            ) : (
              <LoadingState label="Loading agreement…" />
            )}
            {agreementError && !agreement ? <View style={styles.gapTop}><Button variant="secondary" title="Retry" onPress={loadAgreement} /></View> : null}

            <Pressable
              style={styles.termsRow}
              onPress={() => {
                if (!agreement) return;
                setForm({ ...form, accepted_terms: !form.accepted_terms });
              }}
              disabled={!agreement}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: form.accepted_terms, disabled: !agreement }}
              testID="onboarding-terms"
            >
              <View style={[styles.checkbox, form.accepted_terms && styles.checkboxChecked]}>
                {form.accepted_terms && <CheckIcon size={14} color={color.textOnAccent} strokeWidth={2.5} />}
              </View>
              <Text style={[T.bodySm, styles.flex, { color: color.textBody }]}>
                {agreement
                  ? `I accept the Bricks contractor agreement v${agreement.version} (Ontario).`
                  : 'I accept the Bricks contractor agreement (Ontario).'}
              </Text>
            </Pressable>
          </Section>

          <View style={styles.submit}>
            <Button title="Submit application" iconRight={ArrowRight} onPress={handleApply} loading={loading} disabled={!agreement} testID="onboarding-submit" />
          </View>
        </EnterFadeDown>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  safe: { flex: 1, backgroundColor: color.bg },
  content: { paddingHorizontal: layout.gutter, paddingBottom: space[48] },
  headerBox: { paddingTop: space[8], paddingBottom: space[32], gap: space[8] },
  fields: { gap: space[16] },
  row: { flexDirection: 'row', gap: space[10] },
  radius: { width: 112 },
  gapTop: { marginTop: space[16] },
  hint: { color: color.textMuted, marginBottom: space[12] },
  sample: { padding: space[16], gap: space[12] },
  uploadRow: { flexDirection: 'row', alignItems: 'center', gap: space[12], minHeight: layout.tap },
  thumb: { width: 48, height: 48, borderRadius: radius.sm },
  thumbPlaceholder: { width: 48, height: 48, borderRadius: radius.sm, backgroundColor: color.surface, alignItems: 'center', justifyContent: 'center' },
  agreementBox: { maxHeight: 280, borderWidth: hairline, borderColor: color.hair, borderRadius: radius.sm, padding: space[14] },
  termsRow: { flexDirection: 'row', alignItems: 'flex-start', gap: space[12], marginTop: space[20], minHeight: layout.tap },
  checkbox: {
    width: 22, height: 22, borderWidth: 1.5, borderColor: color.hairStrong, borderRadius: radius.sm,
    alignItems: 'center', justifyContent: 'center',
  },
  checkboxChecked: { backgroundColor: color.accent, borderColor: color.accent },
  submit: { marginTop: layout.section },
});
