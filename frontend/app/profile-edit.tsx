import { useState, useEffect } from 'react';
import { View, Text, StyleSheet, Pressable, Alert, Switch, Image } from 'react-native';
import { useRouter } from 'expo-router';
import { Camera, FileCheck } from 'lucide-react-native';
import * as ImagePicker from 'expo-image-picker';
import { actorHeaders } from '../lib/api';
import { SKILLS, VendorProfile, mediaUrl } from '../lib/vendor';
import { color, type as T, space, radius, layout } from '../theme';
import {
  Screen, NavBar, Section, Group, Row, Field, Chip, ChipRow, Segmented, StatGrid, Pill, Notice, TextLink, LoadingState,
} from '../components/ui';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

type PickerAsset = {
  uri: string;
  fileName?: string | null;
  mimeType?: string | null;
};

async function pickMedia(kind: 'camera' | 'library'): Promise<PickerAsset | null> {
  if (kind === 'camera') {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) {
      Alert.alert('Permission required', 'Allow camera access to photograph your certificate.');
      return null;
    }
    const result = await ImagePicker.launchCameraAsync({ quality: 0.85, allowsEditing: false });
    if (result.canceled || !result.assets?.length) return null;
    return result.assets[0];
  }
  const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) {
    Alert.alert('Permission required', 'Allow photo library access to upload.');
    return null;
  }
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images'],
    quality: 0.85,
    allowsEditing: false,
  });
  if (result.canceled || !result.assets?.length) return null;
  return result.assets[0];
}

function chooseSource(title: string, onPick: (kind: 'camera' | 'library') => void) {
  Alert.alert(title, undefined, [
    { text: 'Camera', onPress: () => onPick('camera') },
    { text: 'Photo library', onPress: () => onPick('library') },
    { text: 'Cancel', style: 'cancel' },
  ]);
}

export default function ProfileEditScreen() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState<VendorProfile | null>(null);
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [homeCity, setHomeCity] = useState('');
  const [areasRaw, setAreasRaw] = useState('');
  const [skills, setSkills] = useState<string[]>([]);
  const [availability, setAvailability] = useState(true);
  const [rpasLevel, setRpasLevel] = useState('advanced');
  const [rpasNumber, setRpasNumber] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const headers = await actorHeaders();
        const res = await fetch(`${API}/api/vendor-os/profile`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const profile: VendorProfile = await res.json();
        setData(profile);
        setName(profile.name || '');
        setPhone(profile.phone || '');
        setHomeCity(profile.home_city || '');
        setAreasRaw((profile.service_areas || []).join(', '));
        setSkills(profile.skills || []);
        setAvailability(!!profile.availability);
        setRpasLevel((profile.rpas_certificate_level || 'advanced').toLowerCase());
        setRpasNumber(profile.rpas_certificate_number || '');
      } catch {
        setError('Could not load profile.');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toggleSkill = (id: string) => {
    setSkills((cur) => (cur.includes(id) ? cur.filter((s) => s !== id) : [...cur, id]));
  };

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API}/api/vendor-os/profile`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({
          name,
          phone,
          availability,
          skills,
          service_areas: areasRaw.split(',').map((v) => v.trim()).filter(Boolean),
          home_city: homeCity,
          rpas_certificate_level: rpasLevel,
          rpas_certificate_number: rpasNumber,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const profile: VendorProfile = await res.json();
      setData(profile);
      router.back();
    } catch {
      setError('Could not save profile.');
    } finally {
      setSaving(false);
    }
  };

  const uploadMultipart = async (path: string, asset: PickerAsset, extra: Record<string, string> = {}) => {
    const form = new FormData();
    form.append('file', {
      uri: asset.uri,
      name: asset.fileName || (path.includes('rpas') ? 'rpas-certificate.jpg' : 'profile.jpg'),
      type: asset.mimeType || 'image/jpeg',
    } as any);
    Object.entries(extra).forEach(([k, v]) => form.append(k, v));
    const headers = await actorHeaders();
    const res = await fetch(`${API}${path}`, { method: 'POST', headers, body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail?.message || err.detail || `HTTP ${res.status}`);
    }
    return res.json() as Promise<VendorProfile>;
  };

  const onPhoto = () => {
    chooseSource('Profile photo', async (kind) => {
      const asset = await pickMedia(kind);
      if (!asset) return;
      setUploading(true);
      setError('');
      try {
        const profile = await uploadMultipart('/api/vendor-os/profile/photo', asset);
        setData(profile);
      } catch (e: any) {
        setError(e.message || 'Could not upload photo.');
      } finally {
        setUploading(false);
      }
    });
  };

  const onRpas = () => {
    chooseSource('Advanced RPAS certificate', async (kind) => {
      const asset = await pickMedia(kind);
      if (!asset) return;
      setUploading(true);
      setError('');
      try {
        const extra: Record<string, string> = { level: rpasLevel || 'advanced' };
        if (rpasNumber.trim()) extra.certificate_number = rpasNumber.trim();
        const profile = await uploadMultipart('/api/vendor-os/profile/rpas', asset, extra);
        setData(profile);
        if (profile.rpas_certificate_level) setRpasLevel(profile.rpas_certificate_level.toLowerCase());
        if (profile.rpas_certificate_number) setRpasNumber(profile.rpas_certificate_number);
      } catch (e: any) {
        setError(e.message || 'Could not upload RPAS certificate.');
      } finally {
        setUploading(false);
      }
    });
  };

  const avatar = mediaUrl(data?.avatar_url);
  const isOwner = (data?.title || '').toLowerCase() === 'owner' || data?.payout_model === 'owner_full';

  return (
    <Screen
      header={
        <NavBar
          title="Edit profile"
          onBack={() => router.back()}
          right={<TextLink tone="accent" title={saving ? 'Saving…' : 'Save'} onPress={saving || loading ? undefined : save} />}
        />
      }
    >
      {loading ? (
        <LoadingState />
      ) : (
        <>
          <View style={s.top} />
          {error ? <View style={s.gapBottom}><Notice tone="bad">{error}</Notice></View> : null}

          <StatGrid items={[
            { label: isOwner ? 'Title · locked' : 'Title', value: data?.title || 'Photographer IC' },
            { label: 'Tier', value: data?.tier_label || '—' },
            { label: 'Payouts', value: isOwner ? '$0 deduction' : (data?.payout_label || 'Rate card') },
          ]} />

          <Section label="Identity">
            <Pressable style={s.photoRow} onPress={onPhoto} disabled={uploading} accessibilityRole="button">
              {avatar ? (
                <Image source={{ uri: avatar }} style={s.avatar} />
              ) : (
                <View style={s.avatarFallback}><Camera size={layout.icon} color={color.textMuted} strokeWidth={layout.iconStroke} /></View>
              )}
              <View style={s.flex}>
                <Text style={[T.bodyMedium, { color: color.text }]}>{avatar ? 'Change photo' : 'Add profile photo'}</Text>
                <Text style={[T.caption, { color: color.textMuted }]}>{uploading ? 'Uploading…' : 'Camera or library'}</Text>
              </View>
            </Pressable>
            <View style={s.fields}>
              <Field label="Display name" value={name} onChangeText={setName} textContentType="name" />
              <Field label="Phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" textContentType="telephoneNumber" />
              <Field label="Email" value={data?.email || ''} editable={false} hint="Email can’t be changed here." style={s.disabled} />
            </View>
          </Section>

          <Section label="Skills">
            <ChipRow>
              {SKILLS.map((sk) => (
                <Chip key={sk.id} label={sk.label} selected={skills.includes(sk.id)} onPress={() => toggleSkill(sk.id)} />
              ))}
            </ChipRow>
          </Section>

          <Section label="Drone certificate">
            <Text style={[T.bodySm, s.hintTop]}>Transport Canada RPAS certificate, kept on your member record.</Text>
            <Segmented
              options={[{ value: 'basic', label: 'Basic' }, { value: 'advanced', label: 'Advanced' }]}
              value={(rpasLevel === 'basic' ? 'basic' : 'advanced') as 'basic' | 'advanced'}
              onChange={setRpasLevel}
            />
            <View style={s.fields}>
              <Field label="Certificate number" value={rpasNumber} onChangeText={setRpasNumber} placeholder="TC number" autoCapitalize="characters" />
            </View>
            <Group style={s.gapTop}>
              <Row
                icon={FileCheck}
                title={data?.rpas_certificate_url ? 'Certificate on file' : 'No certificate yet'}
                subtitle={data?.rpas_certificate_url ? 'Tap to replace' : 'Photograph or upload it'}
                tone={data?.rpas_certificate_url ? 'default' : 'accent'}
                chevron
                loading={uploading}
                onPress={onRpas}
              />
            </Group>
          </Section>

          <Section label="Availability & area">
            <Group>
              <Row
                title="Available for assignments"
                subtitle="Connect a calendar on GO web so auto-assign only offers jobs that fit."
                right={
                  <Switch
                    value={availability}
                    onValueChange={setAvailability}
                    trackColor={{ false: color.surfaceDeep, true: color.ok }}
                    thumbColor={color.text}
                    ios_backgroundColor={color.surfaceDeep}
                    accessibilityLabel="Available for assignments"
                  />
                }
              />
            </Group>
            <View style={s.fields}>
              <Field label="Home city" value={homeCity} onChangeText={setHomeCity} placeholder="Mississauga" />
              <Field label="Service areas" value={areasRaw} onChangeText={setAreasRaw} placeholder="Toronto, Mississauga, GTA" hint="Separate with commas." />
            </View>
          </Section>

          {data ? (
            <View style={s.gapTopLg}>
              <Pill label={data.assignable ? 'Assignable' : 'Not assignable'} tone={data.assignable ? 'ok' : 'warn'} dot />
              {!data.assignable && data.assignable_reason ? (
                <Text style={[T.caption, s.hintTop]}>{data.assignable_reason}</Text>
              ) : null}
            </View>
          ) : null}
        </>
      )}
    </Screen>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1, gap: 2 },
  top: { height: space[20] },
  gapBottom: { marginBottom: space[16] },
  gapTop: { marginTop: space[16] },
  gapTopLg: { marginTop: layout.section, gap: space[8] },
  fields: { gap: space[16], marginTop: space[16] },
  hintTop: { color: color.textMuted, marginBottom: space[12] },
  disabled: { color: color.textMuted },
  photoRow: { flexDirection: 'row', alignItems: 'center', gap: space[14], minHeight: layout.rowMin },
  avatar: { width: 56, height: 56, borderRadius: radius.sm },
  avatarFallback: { width: 56, height: 56, borderRadius: radius.sm, backgroundColor: color.surface, alignItems: 'center', justifyContent: 'center' },
});
