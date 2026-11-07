import React from 'react';
import { View, Text, StyleSheet, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LogOut, Clock } from 'lucide-react-native';
import { supabase } from '../../lib/supabase';
import { color, type as T, space, radius, layout, hairline } from '../../theme';
import { Button, Pill } from '../../components/ui';

export default function UnderReviewScreen() {
  const onSignOut = () => {
    Alert.alert('Sign out', 'Are you sure?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign out', style: 'destructive', onPress: () => supabase.auth.signOut() },
    ]);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <View style={styles.inner}>
        <View style={styles.iconWrap}>
          <Clock size={24} color={color.accent} strokeWidth={layout.iconStroke} />
        </View>
        <Pill label="Application pending" tone="warn" dot />
        <Text style={[T.display, styles.title]} accessibilityRole="header">We’re reviewing your application</Text>
        <Text style={[T.body, styles.body]}>
          Today, Jobs and Capture open up as soon as you’re approved. We’ll email you.
        </Text>
      </View>
      <View style={styles.footer}>
        <Button variant="secondary" icon={LogOut} title="Sign out" onPress={onSignOut} testID="under-review-sign-out" />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  inner: { flex: 1, paddingHorizontal: layout.gutter, justifyContent: 'center', gap: space[16] },
  iconWrap: {
    width: 56, height: 56, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hairStrong,
    backgroundColor: color.surface, alignItems: 'center', justifyContent: 'center', marginBottom: space[8],
  },
  title: { color: color.text },
  body: { color: color.textBody },
  footer: { paddingHorizontal: layout.gutter, paddingBottom: space[16] },
});
