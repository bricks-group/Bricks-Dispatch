import { useState } from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Check } from 'lucide-react-native';
import { color, type as T, space, layout, radius } from '../../theme';
import { Screen, NavBar, Section, Group, Field, Chip, ChipRow, Segmented, Footer, Button } from '../../components/ui';
import { PREP_ITEMS, ROOM_PRESETS, type RecordingScope } from '../../lib/visit-capture';

const FLOOR_PRESETS = ['Main floor', 'Upper floor', 'Lower level', 'Basement'];

function CheckRow({ title, body, checked, onToggle }: { title: string; body: string; checked: boolean; onToggle: () => void }) {
  return (
    <Pressable
      onPress={onToggle}
      accessibilityRole="checkbox"
      accessibilityState={{ checked }}
      accessibilityLabel={title}
      style={({ pressed }) => [styles.check, pressed && { backgroundColor: color.surface }]}
    >
      <View style={[styles.box, checked && styles.boxOn]}>
        {checked ? <Check size={14} color={color.textOnAccent} strokeWidth={2.5} /> : null}
      </View>
      <View style={styles.flex}>
        <Text style={[T.bodyMedium, { color: color.text }]}>{title}</Text>
        <Text style={[T.bodySm, { color: color.textMuted }]}>{body}</Text>
      </View>
    </Pressable>
  );
}

/** Step 1 of the guided Visit capture: prepare the home, then name what you're about to record. */
export default function VisitPrep() {
  const router = useRouter();
  const { jobId, floor } = useLocalSearchParams<{ jobId: string; floor?: string }>();
  const floorIndex = Number(floor || 0) || 0;
  const [done, setDone] = useState<Record<string, boolean>>({});
  const [scope, setScope] = useState<RecordingScope>('floor');
  const [name, setName] = useState(floorIndex === 0 ? 'Main floor' : '');

  const allChecked = PREP_ITEMS.every((i) => done[i.key]);
  const presets = scope === 'floor' ? FLOOR_PRESETS : ROOM_PRESETS.slice(0, 8);
  const ready = allChecked && name.trim().length > 0;

  const go = () => router.replace({
    pathname: '/(capture)/visit-record',
    params: { jobId, name: name.trim(), scope, floor: String(floorIndex) },
  } as any);

  return (
    <Screen
      header={<NavBar title="Before you record" onBack={() => router.back()} />}
      contentStyle={styles.body}
      footer={(
        <Footer>
          <Button title={allChecked ? 'Open camera' : `Check ${PREP_ITEMS.length - PREP_ITEMS.filter((i) => done[i.key]).length} more`} onPress={go} disabled={!ready} />
        </Footer>
      )}
    >
      <Section first label="The home">
        <Group>
          {PREP_ITEMS.map((i) => (
            <CheckRow key={i.key} title={i.title} body={i.body} checked={!!done[i.key]} onToggle={() => setDone((d) => ({ ...d, [i.key]: !d[i.key] }))} />
          ))}
        </Group>
      </Section>

      <Section first label="This recording">
        <View style={styles.naming}>
          <Segmented
            value={scope}
            onChange={(v) => { setScope(v as RecordingScope); setName(''); }}
            options={[{ value: 'floor', label: 'Whole floor' }, { value: 'room', label: 'One room' }]}
          />
          <Field
            label={scope === 'floor' ? 'Floor name' : 'Room name'}
            value={name}
            onChangeText={setName}
            placeholder={scope === 'floor' ? 'Main floor' : 'Kitchen'}
            maxLength={60}
            hint={scope === 'floor' ? 'One take for the floor. You mark each room as you walk in.' : 'Use for a room you missed or want to redo.'}
          />
          <ChipRow>
            {presets.map((p) => <Chip key={p} label={p} selected={name === p} onPress={() => setName(p)} />)}
          </ChipRow>
        </View>
      </Section>
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { paddingTop: space[16], gap: space[24] },
  flex: { flex: 1 },
  check: { flexDirection: 'row', alignItems: 'center', gap: space[16], minHeight: layout.rowMin, paddingVertical: space[12], paddingHorizontal: space[16] },
  box: {
    width: 22, height: 22, borderRadius: radius.sm, borderWidth: 1.5, borderColor: color.hairStrong,
    alignItems: 'center', justifyContent: 'center',
  },
  boxOn: { backgroundColor: color.accent, borderColor: color.accent },
  naming: { gap: space[16] },
});
