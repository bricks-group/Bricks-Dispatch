import { useState } from 'react';
import { Alert, Linking, Text, TouchableOpacity, View, StyleSheet, ActivityIndicator } from 'react-native';
import { Trash2 } from 'lucide-react-native';
import { requestAccountDeletion, DELETION_INFO_URL } from '../lib/account-deletion';
import { clearOfflineCache } from '../lib/offline';
import { supabase } from '../lib/supabase';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';

/** "Delete account" row for the Me tab (App Store 5.1.1(v) / Google Play account deletion). */
export default function DeleteAccountRow() {
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    const r = await requestAccountDeletion('go');
    setBusy(false);
    if (!r.ok) {
      Alert.alert('Could not send request', r.error);
      return;
    }
    if (r.via === 'email') {
      Alert.alert('Finish in your email app', 'Send the pre-filled email to help@brxs.ca to confirm the deletion.');
      return;
    }
    Alert.alert(
      'Deletion requested',
      'We will delete your Bricks account and profile within 30 days and email you when it is done. Payout and tax records we are required to keep are retained for the legal period. You have been signed out.',
    );
    await clearOfflineCache();
    await supabase.auth.signOut();
  };

  const confirm = () =>
    Alert.alert(
      'Delete your account?',
      'This permanently deletes your Bricks GO account, profile, availability, and saved devices. Jobs already delivered to clients stay with the client. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'What gets deleted', onPress: () => Linking.openURL(DELETION_INFO_URL).catch(() => {}) },
        { text: 'Delete account', style: 'destructive', onPress: run },
      ],
    );

  return (
    <TouchableOpacity style={s.row} activeOpacity={0.7} onPress={confirm} disabled={busy}>
      {busy ? <ActivityIndicator size="small" color={TOKENS.bad} /> : <Trash2 size={15} color={TOKENS.bad} strokeWidth={1.5} />}
      <View style={{ flex: 1 }}>
        <Text style={s.label}>Delete account</Text>
        <Text style={s.sub}>Permanently remove your account and data</Text>
      </View>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  row: {
    flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 14,
    borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, backgroundColor: TOKENS.nightSoft,
  },
  label: { fontFamily: TYPOGRAPHY.body, fontSize: 14, color: TOKENS.bad },
  sub: { fontFamily: TYPOGRAPHY.body, fontSize: 11, color: TOKENS.moonFaint, marginTop: 2 },
});
