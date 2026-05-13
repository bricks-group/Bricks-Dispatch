import { useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Switch } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { ArrowLeft, Bell, Moon, Sun, Vibrate } from 'lucide-react-native';
import { useTheme } from '../context/ThemeContext';
import { TYPOGRAPHY } from '../constants/Theme';

export default function SettingsScreen() {
  const router = useRouter();
  const { theme, isDark, toggleTheme } = useTheme();
  const c = theme;
  const [jobAlerts, setJobAlerts] = useState(true);
  const [chatNotifs, setChatNotifs] = useState(true);
  const [payoutNotifs, setPayoutNotifs] = useState(true);
  const [vibrate, setVibrate] = useState(true);

  return (
    <SafeAreaView style={[s.container, { backgroundColor: c.bg }]} edges={['top']}>
      <View style={[s.header, { borderBottomColor: c.border }]}>
        <TouchableOpacity onPress={() => router.back()} activeOpacity={0.7} style={s.back}>
          <ArrowLeft size={18} color={c.text} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={[s.title, { color: c.text }]}>Settings</Text>
        <View style={{ width: 36 }} />
      </View>

      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>
        <View style={s.section}>
          <Text style={[s.sectionLabel, { color: c.textFaint }]}>NOTIFICATIONS</Text>
          <View style={[s.menuCard, { backgroundColor: c.surface, borderColor: c.border }]}>
            {[
              { label: 'Job alerts', value: jobAlerts, set: setJobAlerts, Icon: Bell },
              { label: 'Messages', value: chatNotifs, set: setChatNotifs, Icon: Bell },
              { label: 'Payout updates', value: payoutNotifs, set: setPayoutNotifs, Icon: Bell },
            ].map(({ label, value, set, Icon }, i, arr) => (
              <View key={label} style={[s.row, i < arr.length - 1 && { borderBottomWidth: 1, borderBottomColor: c.border }]}>
                <Icon size={15} color={c.textMuted} strokeWidth={1.5} />
                <Text style={[s.rowLabel, { color: c.text }]}>{label}</Text>
                <Switch
                  value={value}
                  onValueChange={set}
                  trackColor={{ false: c.surfaceDeep, true: '#4A6B3C66' }}
                  thumbColor={value ? '#4A6B3C' : c.textFaint}
                  ios_backgroundColor={c.surfaceDeep}
                />
              </View>
            ))}
            <View style={s.row}>
              <Vibrate size={15} color={c.textMuted} strokeWidth={1.5} />
              <Text style={[s.rowLabel, { color: c.text }]}>Vibration</Text>
              <Switch
                value={vibrate}
                onValueChange={setVibrate}
                trackColor={{ false: c.surfaceDeep, true: '#4A6B3C66' }}
                thumbColor={vibrate ? '#4A6B3C' : c.textFaint}
                ios_backgroundColor={c.surfaceDeep}
              />
            </View>
          </View>
        </View>

        <View style={s.section}>
          <Text style={[s.sectionLabel, { color: c.textFaint }]}>APPEARANCE</Text>
          <View style={[s.menuCard, { backgroundColor: c.surface, borderColor: c.border }]}>
            <View style={s.row}>
              {isDark ? <Moon size={15} color={c.textMuted} strokeWidth={1.5} /> : <Sun size={15} color={c.textMuted} strokeWidth={1.5} />}
              <View style={{ flex: 1 }}>
                <Text style={[s.rowLabel, { color: c.text }]}>Dark mode</Text>
                <Text style={[s.rowSub, { color: c.textFaint }]}>{isDark ? 'On' : 'Off'}</Text>
              </View>
              <Switch
                value={isDark}
                onValueChange={toggleTheme}
                trackColor={{ false: c.surfaceDeep, true: '#4A6B3C66' }}
                thumbColor={isDark ? '#4A6B3C' : c.textFaint}
                ios_backgroundColor={c.surfaceDeep}
              />
            </View>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );

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
