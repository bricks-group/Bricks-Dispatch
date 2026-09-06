import { useState, useEffect } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity,
  ActivityIndicator, Alert, Switch, Image,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { ArrowLeft, Check, Camera } from 'lucide-react-native';
import * as ImagePicker from 'expo-image-picker';
import { actorHeaders } from '../lib/api';
import { SKILLS, VendorProfile, mediaUrl } from '../lib/vendor';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';

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
    <SafeAreaView style={s.container} edges={['top']}>
      <View style={s.header}>
        <TouchableOpacity onPress={() => router.back()} activeOpacity={0.7} style={s.back}>
          <ArrowLeft size={18} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={s.title}>Edit Profile</Text>
        <TouchableOpacity onPress={save} activeOpacity={0.7} style={s.saveBtn} disabled={saving || loading}>
          {saving
            ? <ActivityIndicator size="small" color={TOKENS.brick} />
            : <Check size={18} color={TOKENS.brick} strokeWidth={2} />}
        </TouchableOpacity>
      </View>

      {loading ? (
        <View style={s.center}>
          <ActivityIndicator size="small" color={TOKENS.brick} />
        </View>
      ) : (
        <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">
          {error ? <Text style={s.err}>{error}</Text> : null}

          <View style={s.metrics}>
            <View style={s.metric}>
              <Text style={s.metricLabel}>TITLE</Text>
              <Text style={s.metricVal}>{data?.title || 'Photographer IC'}</Text>
              {isOwner ? <Text style={s.metricSub}>Locked · Owner</Text> : <Text style={s.metricSub}>Photographer IC</Text>}
            </View>
            <View style={s.metric}>
              <Text style={s.metricLabel}>TIER</Text>
              <Text style={s.metricVal}>{data?.tier_label || '—'}</Text>
              <Text style={s.metricSub}>{isOwner ? 'Highest · Owner' : `Tier ${data?.tier_label || ''}`}</Text>
            </View>
            <View style={[s.metric, { flex: 1.2 }]}>
              <Text style={s.metricLabel}>PAYOUTS</Text>
              <Text style={s.metricValSm}>{data?.payout_label || 'Fixed rate card'}</Text>
              <Text style={s.metricSub}>{isOwner ? '$0 deduction' : 'Fixed rate card'}</Text>
            </View>
          </View>

          <View style={s.section}>
            <Text style={s.sectionLabel}>IDENTITY</Text>
            <TouchableOpacity style={s.photoRow} onPress={onPhoto} activeOpacity={0.7} disabled={uploading}>
              {avatar ? (
                <Image source={{ uri: avatar }} style={s.avatar} />
              ) : (
                <View style={s.avatarFallback}>
                  <Camera size={16} color={TOKENS.moonMute} strokeWidth={1.5} />
                </View>
              )}
              <View style={{ flex: 1 }}>
                <Text style={s.menuLabel}>{avatar ? 'Change photo' : 'Add profile photo'}</Text>
                <Text style={s.hint}>{uploading ? 'Uploading…' : 'Camera or library'}</Text>
              </View>
            </TouchableOpacity>
            <Text style={s.fieldLabel}>DISPLAY NAME</Text>
            <TextInput
              style={s.input}
              value={name}
              onChangeText={setName}
              placeholderTextColor={TOKENS.moonFaint}
              selectionColor={TOKENS.brick}
            />
            <Text style={s.fieldLabel}>PHONE</Text>
            <TextInput
              style={s.input}
              value={phone}
              onChangeText={setPhone}
              keyboardType="phone-pad"
              placeholderTextColor={TOKENS.moonFaint}
              selectionColor={TOKENS.brick}
            />
            <Text style={s.fieldLabel}>EMAIL</Text>
            <TextInput style={[s.input, s.inputDisabled]} value={data?.email || ''} editable={false} />
            <Text style={s.hint}>Email cannot be changed here.</Text>
          </View>

          <View style={s.section}>
            <Text style={s.sectionLabel}>SKILLS</Text>
            <View style={s.chipRow}>
              {SKILLS.map((sk) => {
                const on = skills.includes(sk.id);
                return (
                  <TouchableOpacity
                    key={sk.id}
                    style={[s.chip, on && s.chipOn]}
                    onPress={() => toggleSkill(sk.id)}
                    activeOpacity={0.7}
                  >
                    <Text style={[s.chipText, on && s.chipTextOn]}>{sk.label}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </View>

          <View style={s.section}>
            <Text style={s.sectionLabel}>ADVANCED RPAS</Text>
            <Text style={s.hint}>
              Transport Canada Advanced certificate. Stored on your member record for the hiring file.
            </Text>
            <View style={s.chipRow}>
              {(['basic', 'advanced'] as const).map((lvl) => {
                const on = rpasLevel === lvl;
                return (
                  <TouchableOpacity
                    key={lvl}
                    style={[s.chip, on && s.chipOn]}
                    onPress={() => setRpasLevel(lvl)}
                    activeOpacity={0.7}
                  >
                    <Text style={[s.chipText, on && s.chipTextOn]}>{lvl === 'advanced' ? 'Advanced' : 'Basic'}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            <Text style={s.fieldLabel}>CERTIFICATE NUMBER</Text>
            <TextInput
              style={s.input}
              value={rpasNumber}
              onChangeText={setRpasNumber}
              placeholder="TC number"
              placeholderTextColor={TOKENS.moonFaint}
              selectionColor={TOKENS.brick}
              autoCapitalize="characters"
            />
            <Text style={s.fieldLabel}>CERTIFICATE PHOTO</Text>
            {data?.rpas_certificate_url ? (
              <Text style={s.onFile}>On file</Text>
            ) : (
              <Text style={s.hint}>No certificate uploaded yet</Text>
            )}
            <TouchableOpacity style={s.uploadBtn} onPress={onRpas} disabled={uploading} activeOpacity={0.7}>
              {uploading
                ? <ActivityIndicator size="small" color={TOKENS.moon} />
                : <Text style={s.uploadBtnText}>{data?.rpas_certificate_url ? 'Replace certificate' : 'Upload certificate'}</Text>}
            </TouchableOpacity>
          </View>

          <View style={s.section}>
            <Text style={s.sectionLabel}>SERVICE AREA</Text>
            <View style={s.toggleRow}>
              <Text style={s.toggleLabel}>Available for assignments</Text>
              <Text style={{ color: TOKENS.moonMute, fontSize: 12, marginTop: 8, lineHeight: 16 }}>
                Connect Google, Outlook, Apple, or an ICS calendar on web Availability so auto-assign only offers jobs that fit your real schedule.
              </Text>
              <Switch
                value={availability}
                onValueChange={setAvailability}
                trackColor={{ false: TOKENS.nightDeep, true: TOKENS.ok + '66' }}
                thumbColor={availability ? TOKENS.ok : TOKENS.moonFaint}
                ios_backgroundColor={TOKENS.nightDeep}
              />
            </View>
            <Text style={s.fieldLabel}>HOME CITY</Text>
            <TextInput
              style={s.input}
              value={homeCity}
              onChangeText={setHomeCity}
              placeholder="Mississauga"
              placeholderTextColor={TOKENS.moonFaint}
              selectionColor={TOKENS.brick}
            />
            <Text style={s.fieldLabel}>SERVICE AREAS</Text>
            <TextInput
              style={s.input}
              value={areasRaw}
              onChangeText={setAreasRaw}
              placeholder="Toronto, Mississauga, GTA"
              placeholderTextColor={TOKENS.moonFaint}
              selectionColor={TOKENS.brick}
            />
            <Text style={s.hint}>Comma-separated. Office field layer, not a public marketplace.</Text>
          </View>

          {data ? (
            <Text style={s.hint}>
              {data.assignable ? 'Assignable' : (data.assignable_reason || 'Not assignable')}
            </Text>
          ) : null}

          <View style={{ height: 80 }} />
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  back: { width: 36, height: 36, justifyContent: 'center' },
  title: { flex: 1, fontSize: 16, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, textAlign: 'center' },
  saveBtn: { width: 36, height: 36, justifyContent: 'center', alignItems: 'flex-end' },
  scroll: { padding: 24 },
  err: { color: TOKENS.bad, fontSize: 13, marginBottom: 16, fontFamily: TYPOGRAPHY.body },
  metrics: { flexDirection: 'row', gap: 8, marginBottom: 28 },
  metric: { flex: 1, backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, padding: 12 },
  metricLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 8, color: TOKENS.moonFaint, letterSpacing: 1.4, marginBottom: 6 },
  metricVal: { fontFamily: TYPOGRAPHY.heading, fontSize: 16, color: TOKENS.moon, marginBottom: 4 },
  metricValSm: { fontFamily: TYPOGRAPHY.heading, fontSize: 13, color: TOKENS.moon, marginBottom: 4 },
  metricSub: { fontFamily: TYPOGRAPHY.body, fontSize: 11, color: TOKENS.moonFaint },
  section: { marginBottom: 24 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginBottom: 10 },
  fieldLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginTop: 14, marginBottom: 8 },
  input: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, paddingHorizontal: 16, paddingVertical: 14, color: TOKENS.moon, fontSize: 15, fontFamily: TYPOGRAPHY.mono },
  inputDisabled: { opacity: 0.4 },
  hint: { marginTop: 6, fontSize: 11, color: TOKENS.moonFaint, fontFamily: TYPOGRAPHY.body, lineHeight: 16 },
  onFile: { marginTop: 4, fontSize: 13, color: TOKENS.ok, fontFamily: TYPOGRAPHY.body },
  photoRow: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, padding: 12, marginBottom: 4 },
  avatar: { width: 56, height: 56, borderRadius: 28 },
  avatarFallback: { width: 56, height: 56, borderRadius: 28, backgroundColor: TOKENS.nightDeep, alignItems: 'center', justifyContent: 'center' },
  menuLabel: { fontFamily: TYPOGRAPHY.heading, fontSize: 14, color: TOKENS.moon, marginBottom: 2 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 },
  chip: { borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: 2, paddingHorizontal: 12, paddingVertical: 8, backgroundColor: TOKENS.nightSoft },
  chipOn: { borderColor: TOKENS.brick, backgroundColor: TOKENS.brick + '22' },
  chipText: { fontFamily: TYPOGRAPHY.body, fontSize: 13, color: TOKENS.moonMute },
  chipTextOn: { color: TOKENS.moon },
  uploadBtn: { marginTop: 12, height: 44, borderRadius: TOKENS.radius, backgroundColor: TOKENS.brick, alignItems: 'center', justifyContent: 'center' },
  uploadBtnText: { fontSize: 14, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading },
  toggleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, paddingHorizontal: 16, paddingVertical: 12 },
  toggleLabel: { fontFamily: TYPOGRAPHY.body, fontSize: 14, color: TOKENS.moon, flex: 1, paddingRight: 12 },
});
