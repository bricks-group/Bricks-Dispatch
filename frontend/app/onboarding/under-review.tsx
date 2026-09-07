import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LogOut, Clock } from 'lucide-react-native';
import { supabase } from '../../lib/supabase';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

export default function UnderReviewScreen() {
  const onSignOut = () => {
    Alert.alert('Sign out', 'Are you sure?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign out',
        style: 'destructive',
        onPress: () => supabase.auth.signOut(),
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <View style={styles.inner}>
        <View style={styles.iconWrap}>
          <Clock size={28} color={TOKENS.brick} strokeWidth={2} />
        </View>
        <Text style={styles.title}>Under review</Text>
        <Text style={styles.body}>
          Your application is under review. You will get access to Today, Jobs, and Capture after Kam approves you in Hiring.
        </Text>
        <Text style={styles.meta}>BRICKS GO · APPLICATION PENDING</Text>

        <TouchableOpacity
          style={styles.signOut}
          onPress={onSignOut}
          activeOpacity={0.85}
          data-testid="under-review-sign-out"
        >
          <LogOut size={16} color={TOKENS.bad || '#C44'} strokeWidth={2} />
          <Text style={styles.signOutLabel}>Sign out</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: TOKENS.night,
  },
  inner: {
    flex: 1,
    paddingHorizontal: 36,
    justifyContent: 'center',
  },
  iconWrap: {
    width: 56,
    height: 56,
    borderRadius: 2,
    borderWidth: 1,
    borderColor: TOKENS.nightHairS || '#F4F1EB26',
    backgroundColor: TOKENS.nightSoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 24,
  },
  title: {
    fontSize: 34,
    color: TOKENS.moon,
    letterSpacing: -1.1,
    textTransform: 'lowercase',
    marginBottom: 14,
  },
  body: {
    fontSize: 14,
    lineHeight: 22,
    color: TOKENS.moonSoft,
    fontFamily: TYPOGRAPHY?.body || 'System',
    marginBottom: 18,
  },
  meta: {
    fontSize: 10,
    fontFamily: 'IoskeleyMono-Medium',
    color: TOKENS.moonMute,
    letterSpacing: 1.6,
    marginBottom: 40,
  },
  signOut: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    alignSelf: 'flex-start',
    backgroundColor: TOKENS.nightSoft,
    borderWidth: 1,
    borderColor: TOKENS.nightHair,
    borderRadius: 2,
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  signOutLabel: {
    fontFamily: TYPOGRAPHY?.heading || 'System',
    fontSize: 14,
    color: TOKENS.bad || '#C44',
  },
});
