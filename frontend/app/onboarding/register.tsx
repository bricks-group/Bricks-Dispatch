import React, { useState } from 'react';
import { View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert } from 'react-native';
import { useRouter } from 'expo-router';
import { ArrowRight, Check } from 'lucide-react-native';
import { api } from '../../lib/api';
import BricksAOSelector from '../../components/BricksAOSelector';

// Bricks Design Tokens - Dark Mode (Night)
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

export default function OnboardingRegister() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({
    name: '',
    email: '',
    phone: '',
    city: '',
    specialties: ['photo'],
    portfolio_links: [''],
    equipment_list: [''],
    preferred_regions: [],
    operation_polygon: [], // The custom AO
    max_travel_km: '50',
    accepted_terms: false
  });

  const handleApply = async () => {
    if (!form.name || !form.email || !form.accepted_terms) {
      Alert.alert('REQUIRED FIELDS', 'Please complete all required fields and accept the terms.');
      return;
    }

    setLoading(true);
    try {
      const result = await api.onboarding.apply({
        ...form,
        max_travel_km: parseInt(form.max_travel_km, 10),
        operation_polygon: form.operation_polygon
      });
      if (result.success) {
        Alert.alert('SUCCESS', 'Application received. We will review your portfolio shortly.', [
          { text: 'OK', onPress: () => router.replace('/') }
        ]);
      } else {
        Alert.alert('ERROR', result.detail || 'Failed to submit application.');
      }
    } catch (e) {
      Alert.alert('ERROR', 'Connection failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
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
          onChangeText={(v) => setForm({...form, name: v})}
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
          onChangeText={(v) => setForm({...form, email: v})}
          data-testid="onboarding-email"
        />

        <View style={styles.row}>
          <View style={{ flex: 1, marginRight: 8 }}>
            <Text style={styles.label}>CITY</Text>
            <TextInput 
              style={styles.input} 
              placeholder="TORONTO" 
              placeholderTextColor={TOKENS.moonFaint}
              value={form.city}
              onChangeText={(v) => setForm({...form, city: v})}
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
              onChangeText={(v) => setForm({...form, max_travel_km: v})}
              data-testid="onboarding-radius"
            />
          </View>
        </View>

        {/* CUSTOM AO MAP SELECTOR */}
        <BricksAOSelector 
          onPolygonComplete={(path: any) => setForm({...form, operation_polygon: path})}
        />
      </View>

      <View style={styles.section}>
        <Text style={styles.label}>PORTFOLIO URL</Text>
        <TextInput 
          style={styles.input} 
          placeholder="INSTAGRAM.COM/CREATIVE" 
          placeholderTextColor={TOKENS.moonFaint}
          autoCapitalize="none"
          value={form.portfolio_links[0]}
          onChangeText={(v) => setForm({...form, portfolio_links: [v]})}
          data-testid="onboarding-portfolio"
        />
      </View>

      <TouchableOpacity 
        style={styles.termsRow} 
        onPress={() => setForm({...form, accepted_terms: !form.accepted_terms})}
        activeOpacity={0.8}
        data-testid="onboarding-terms"
      >
        <View style={[styles.checkbox, form.accepted_terms && styles.checkboxChecked]}>
          {form.accepted_terms && <Check size={14} color="#FFF" strokeWidth={3} />}
        </View>
        <Text style={styles.termsText}>I ACCEPT THE BRICKS CONTRACTOR AGREEMENT (ONTARIO)</Text>
      </TouchableOpacity>

      <TouchableOpacity 
        style={styles.button} 
        onPress={handleApply}
        disabled={loading}
        activeOpacity={0.9}
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
      </TouchableOpacity>
      
      <View style={{ height: 60 }} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: TOKENS.night,
  },
  content: {
    padding: 36, // content: padding: 0 36px
  },
  headerBox: {
    marginTop: 48,
    marginBottom: 36,
  },
  header: {
    fontSize: 34,
    fontFamily: 'System',
    color: TOKENS.moon,
    letterSpacing: -1.1,
    textTransform: 'lowercase',
  },
  subhead: {
    fontSize: 10.5,
    fontFamily: 'IoskeleyMono-Medium',
    color: TOKENS.moonMute,
    letterSpacing: 1.8, // 0.18em
    marginTop: 8,
  },
  section: {
    marginBottom: 28,
  },
  label: {
    fontSize: 10,
    fontFamily: 'IoskeleyMono-Medium',
    color: TOKENS.moonMute,
    letterSpacing: 1.6, // 0.16em
    marginBottom: 10,
    textTransform: 'uppercase',
  },
  row: {
    flexDirection: 'row',
  },
  input: {
    backgroundColor: TOKENS.nightSoft,
    borderWidth: 1,
    borderColor: TOKENS.nightHair,
    padding: 14,
    color: TOKENS.moon,
    fontSize: 13,
    fontFamily: 'IoskeleyMono-Regular',
    borderRadius: 2,
    marginBottom: 18,
  },
  button: {
    backgroundColor: TOKENS.brick,
    paddingVertical: 14,
    paddingHorizontal: 22,
    borderRadius: 2,
    alignItems: 'center',
    marginTop: 12,
  },
  btnInner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  buttonText: {
    color: '#FFF',
    fontFamily: 'System',
    fontSize: 14,
  },
  termsRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 24,
    gap: 12,
  },
  checkbox: {
    width: 20,
    height: 20,
    borderWidth: 1,
    borderColor: TOKENS.nightHairS,
    borderRadius: 2,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 2,
  },
  checkboxChecked: {
    backgroundColor: TOKENS.brick,
    borderColor: TOKENS.brick,
  },
  termsText: {
    color: TOKENS.moonSoft,
    fontSize: 10,
    fontFamily: 'IoskeleyMono-Medium',
    lineHeight: 16,
    flex: 1,
  }
});
