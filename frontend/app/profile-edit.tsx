import { useState, useEffect } from 'react';
import { View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity, ActivityIndicator, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { ArrowLeft, Check } from 'lucide-react-native';
import { actorHeaders } from '../lib/api';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.206:8002';

type Profile = { name: string; email: string; phone: string; role: string };

export default function ProfileEditScreen() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API}/api/profile`, { headers: await actorHeaders() });
        const data: Profile = await res.json();
        setName(data.name);
        setPhone(data.phone);
        setEmail(data.email);
      } catch {
        Alert.alert('Error', 'Could not load profile.');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await fetch(`${API}/api/profile`, {
        method: 'PUT',
        headers: await actorHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ name, phone }),
      });
      router.back();
    } catch {
      Alert.alert('Error', 'Could not save profile.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <View style={s.header}>
        <TouchableOpacity onPress={() => router.back()} activeOpacity={0.7} style={s.back}>
          <ArrowLeft size={18} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={s.title}>Edit Profile</Text>
        <TouchableOpacity onPress={save} activeOpacity={0.7} style={s.saveBtn} disabled={saving}>
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
          <View style={s.section}>
            <Text style={s.sectionLabel}>DISPLAY NAME</Text>
            <TextInput
              style={s.input}
              value={name}
              onChangeText={setName}
              placeholderTextColor={TOKENS.moonFaint}
              selectionColor={TOKENS.brick}
            />
          </View>

          <View style={s.section}>
            <Text style={s.sectionLabel}>PHONE</Text>
            <TextInput
              style={s.input}
              value={phone}
              onChangeText={setPhone}
              keyboardType="phone-pad"
              placeholderTextColor={TOKENS.moonFaint}
              selectionColor={TOKENS.brick}
            />
          </View>

          <View style={s.section}>
            <Text style={s.sectionLabel}>EMAIL</Text>
            <TextInput
              style={[s.input, s.inputDisabled]}
              value={email}
              editable={false}
            />
            <Text style={s.hint}>Email cannot be changed here.</Text>
          </View>
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
  section: { marginBottom: 24 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginBottom: 10 },
  input: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, paddingHorizontal: 16, paddingVertical: 14, color: TOKENS.moon, fontSize: 15, fontFamily: TYPOGRAPHY.mono },
  inputDisabled: { opacity: 0.4 },
  hint: { marginTop: 6, fontSize: 11, color: TOKENS.moonFaint },
});
