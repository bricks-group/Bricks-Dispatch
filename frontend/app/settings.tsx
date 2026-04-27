import { useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Switch } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { ArrowLeft, Bell, Moon, Vibrate } from 'lucide-react-native';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';

export default function SettingsScreen() {
  const router = useRouter();
  const [jobAlerts, setJobAlerts] = useState(true);
  const [chatNotifs, setChatNotifs] = useState(true);
  const [payoutNotifs, setPayoutNotifs] = useState(true);
  const [vibrate, setVibrate] = useState(true);

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <View style={s.header}>
        <TouchableOpacity onPress={() => router.back()} activeOpacity={0.7} style={s.back}>
          <ArrowLeft size={18} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={s.title}>Settings</Text>
        <View style={{ width: 36 }} />
      </View>

      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>
        <View style={s.section}>
          <Text style={s.sectionLabel}>NOTIFICATIONS</Text>
          <View style={s.menuCard}>
            <View style={[s.row, s.rowBorder]}>
              <Bell size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              <Text style={s.rowLabel}>Job alerts</Text>
              <Switch
                value={jobAlerts}
                onValueChange={setJobAlerts}
                trackColor={{ false: TOKENS.nightDeep, true: TOKENS.ok + '66' }}
                thumbColor={jobAlerts ? TOKENS.ok : TOKENS.moonFaint}
                ios_backgroundColor={TOKENS.nightDeep}
              />
            </View>
            <View style={[s.row, s.rowBorder]}>
              <Bell size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              <Text style={s.rowLabel}>Messages</Text>
              <Switch
                value={chatNotifs}
                onValueChange={setChatNotifs}
                trackColor={{ false: TOKENS.nightDeep, true: TOKENS.ok + '66' }}
                thumbColor={chatNotifs ? TOKENS.ok : TOKENS.moonFaint}
                ios_backgroundColor={TOKENS.nightDeep}
              />
            </View>
            <View style={[s.row, s.rowBorder]}>
              <Bell size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              <Text style={s.rowLabel}>Payout updates</Text>
              <Switch
                value={payoutNotifs}
                onValueChange={setPayoutNotifs}
                trackColor={{ false: TOKENS.nightDeep, true: TOKENS.ok + '66' }}
                thumbColor={payoutNotifs ? TOKENS.ok : TOKENS.moonFaint}
                ios_backgroundColor={TOKENS.nightDeep}
              />
            </View>
            <View style={s.row}>
              <Vibrate size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              <Text style={s.rowLabel}>Vibration</Text>
              <Switch
                value={vibrate}
                onValueChange={setVibrate}
                trackColor={{ false: TOKENS.nightDeep, true: TOKENS.ok + '66' }}
                thumbColor={vibrate ? TOKENS.ok : TOKENS.moonFaint}
                ios_backgroundColor={TOKENS.nightDeep}
              />
            </View>
          </View>
        </View>

        <View style={s.section}>
          <Text style={s.sectionLabel}>APPEARANCE</Text>
          <View style={s.menuCard}>
            <View style={s.row}>
              <Moon size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              <View style={{ flex: 1 }}>
                <Text style={s.rowLabel}>Theme</Text>
                <Text style={s.rowSub}>Dark</Text>
              </View>
            </View>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  back: { width: 36, height: 36, justifyContent: 'center' },
  title: { flex: 1, fontSize: 16, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, textAlign: 'center' },
  scroll: { padding: 24 },
  section: { marginBottom: 24 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginBottom: 12 },
  menuCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 14, gap: 12 },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  rowLabel: { flex: 1, fontSize: 14, color: TOKENS.moon },
  rowSub: { fontSize: 11, color: TOKENS.moonFaint, marginTop: 2 },
});
