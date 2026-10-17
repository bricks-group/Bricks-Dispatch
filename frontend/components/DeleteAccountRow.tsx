import { useState } from 'react';
import { Alert, Linking } from 'react-native';
import { Trash2 } from 'lucide-react-native';
import { requestAccountDeletion, DELETION_INFO_URL } from '../lib/account-deletion';
import { clearOfflineCache } from '../lib/offline';
import { supabase } from '../lib/supabase';
import { Row } from './ui';

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
      'This permanently deletes your Bricks account, profile, availability, and saved devices. Jobs already delivered to clients stay with the client. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'What gets deleted', onPress: () => Linking.openURL(DELETION_INFO_URL).catch(() => {}) },
        { text: 'Delete account', style: 'destructive', onPress: run },
      ],
    );

  // Bare row: place inside a <Group> so it shares the list's hairlines.
  return (
    <Row
      icon={Trash2}
      title="Delete account"
      subtitle="Permanently remove your account and data"
      tone="bad"
      onPress={confirm}
      loading={busy}
    />
  );
}
