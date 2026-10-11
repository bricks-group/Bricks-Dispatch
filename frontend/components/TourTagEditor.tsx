import { useMemo, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
} from 'react-native';
import { COLORS, FONTS } from '../constants/CaptureTheme';
import { color, radius, layout } from '../theme';
import { AMENITY_PRESETS } from '../constants/amenityPresets';
import { addTourTag, deleteTourTag } from '../lib/capture-api';
import type { CaptureSession, CaptureStation, TourTag } from '../lib/capture-api';

type Props = {
  jobId: string;
  session: CaptureSession;
  onSession: (s: CaptureSession) => void;
};

function tagsFrom(session: CaptureSession): TourTag[] {
  if (Array.isArray(session.tags) && session.tags.length) return session.tags;
  const out: TourTag[] = [];
  for (const st of session.stations || []) {
    for (const t of st.tags || []) {
      out.push({ ...t, station_index: t.station_index ?? st.station_index });
    }
  }
  return out;
}

function parseDeg(raw: string, fallback = 0): number {
  const n = Number(String(raw).trim());
  return Number.isFinite(n) ? n : fallback;
}

export function TourTagEditor({ jobId, session, onSession }: Props) {
  const stations: CaptureStation[] = session.stations || [];
  const sessionId = session.sessionId || '';
  const [stationIndex, setStationIndex] = useState<number>(
    stations[0]?.station_index ?? 0,
  );
  const [label, setLabel] = useState('');
  const [note, setNote] = useState('');
  const [yawText, setYawText] = useState('0');
  const [pitchText, setPitchText] = useState('0');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const tags = useMemo(() => tagsFrom(session), [session]);

  const stationLabel = (idx: number) => {
    const st = stations.find((s) => s.station_index === idx);
    return st?.room ? `${st.room} · ${idx}` : `Station ${idx}`;
  };

  const stationOptions = stations.length
    ? stations
    : [{ station_index: 0, room: 'Station 0' } as CaptureStation];

  const handleAdd = async () => {
    const lab = label.trim();
    if (!lab) {
      setMsg('Tag needs a label — e.g. Walk-in closet. Empty amenities are not invented.');
      return;
    }
    if (!sessionId) {
      setMsg('No capture session');
      return;
    }
    let yaw = parseDeg(yawText, 0);
    while (yaw > 180) yaw -= 360;
    while (yaw < -180) yaw += 360;
    const pitch = Math.max(-80, Math.min(80, parseDeg(pitchText, 0)));
    setBusy(true);
    setMsg(null);
    const result = await addTourTag(jobId, sessionId, {
      station_index: stationIndex,
      label: lab,
      note: note.trim() || undefined,
      yaw: Math.round(yaw * 10) / 10,
      pitch: Math.round(pitch * 10) / 10,
    });
    setBusy(false);
    if (result.session) onSession(result.session);
    if (!result.ok) {
      setMsg(result.error ?? 'Could not add tag');
      return;
    }
    setLabel('');
    setNote('');
    setMsg(
      result.rebuilt === false
        ? 'Tag saved. Rebuild the tour player so the listing iframe picks it up.'
        : 'Tag on the tour. Hard-refresh the listing iframe to see it.',
    );
  };

  const handleDelete = async (tagId: string) => {
    if (!sessionId) return;
    setBusy(true);
    setMsg(null);
    const result = await deleteTourTag(jobId, sessionId, tagId);
    setBusy(false);
    if (result.session) onSession(result.session);
    if (!result.ok) setMsg(result.error ?? 'Could not remove tag');
  };

  if (!sessionId) return null;

  const msgOk = !!msg && (msg.startsWith('Tag on') || msg.startsWith('Tag saved'));

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Feature tags</Text>
      <Text style={styles.hint}>
        Pick a station, set look (yaw / pitch), then add a short label. Same API as web —
        tags compile into the listing tour player. Phone does not drag a 360 look strip;
        type degrees (yaw −180…180, pitch −80…80).
      </Text>

      <Text style={styles.fieldLabel}>Station</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.stationRow}>
        {stationOptions.map((s) => {
          const active = s.station_index === stationIndex;
          return (
            <TouchableOpacity
              key={s.station_index}
              style={[styles.stationChip, active && styles.stationChipOn]}
              onPress={() => setStationIndex(s.station_index)}
              disabled={busy}
            >
              <Text style={[styles.stationChipText, active && styles.stationChipTextOn]}>
                {stationLabel(s.station_index)}
              </Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      <View style={styles.row2}>
        <View style={{ flex: 1 }}>
          <Text style={styles.fieldLabel}>Yaw °</Text>
          <TextInput
            style={styles.input}
            value={yawText}
            onChangeText={setYawText}
            keyboardType="numbers-and-punctuation"
            placeholder="0"
            placeholderTextColor={COLORS.ink3}
            editable={!busy}
          />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.fieldLabel}>Pitch °</Text>
          <TextInput
            style={styles.input}
            value={pitchText}
            onChangeText={setPitchText}
            keyboardType="numbers-and-punctuation"
            placeholder="0"
            placeholderTextColor={COLORS.ink3}
            editable={!busy}
          />
        </View>
      </View>

      <Text style={styles.fieldLabel}>Presets</Text>
      <Text style={styles.presetHint}>
        Tap to fill label, then Add — nothing is invented onto the tour.
      </Text>
      <View style={styles.presetWrap}>
        {AMENITY_PRESETS.map((p) => {
          const on = label.trim().toLowerCase() === p.toLowerCase();
          return (
            <TouchableOpacity
              key={p}
              style={[styles.presetChip, on && styles.presetChipOn]}
              onPress={() => setLabel(p)}
              disabled={busy}
            >
              <Text style={[styles.presetChipText, on && styles.presetChipTextOn]}>{p}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <Text style={styles.fieldLabel}>Label</Text>
      <TextInput
        style={styles.input}
        value={label}
        onChangeText={setLabel}
        placeholder="Walk-in closet"
        placeholderTextColor={COLORS.ink3}
        maxLength={80}
        editable={!busy}
      />

      <Text style={styles.fieldLabel}>Note (optional)</Text>
      <TextInput
        style={styles.input}
        value={note}
        onChangeText={setNote}
        placeholder="Cedar lining"
        placeholderTextColor={COLORS.ink3}
        maxLength={240}
        editable={!busy}
      />

      <TouchableOpacity
        style={[styles.addBtn, (busy || !label.trim()) && { opacity: 0.5 }]}
        onPress={handleAdd}
        disabled={busy || !label.trim()}
      >
        <Text style={styles.addBtnText}>{busy ? 'Saving…' : 'Add tag'}</Text>
      </TouchableOpacity>

      {msg ? (
        <Text style={[styles.msg, msgOk ? styles.msgOk : styles.msgErr]}>{msg}</Text>
      ) : null}

      <Text style={styles.count}>
        {tags.length
          ? `${tags.length} tag${tags.length === 1 ? '' : 's'} on this tour`
          : 'No feature tags yet — none are invented.'}
      </Text>

      {tags.map((t) => (
        <View key={t.id} style={styles.tagRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.tagLabel}>
              {t.label}
              {t.note ? ` — ${t.note}` : ''}
            </Text>
            <Text style={styles.tagMeta}>
              {stationLabel(t.station_index ?? 0)} · {Number(t.yaw).toFixed(0)}° / {Number(t.pitch).toFixed(0)}°
            </Text>
          </View>
          <TouchableOpacity
            style={styles.removeBtn}
            onPress={() => handleDelete(t.id)}
            disabled={busy}
          >
            <Text style={styles.removeBtnText}>Remove</Text>
          </TouchableOpacity>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    marginTop: 16,
    borderWidth: 1,
    borderColor: COLORS.hair,
    borderRadius: radius.sm,
    padding: 16,
    gap: 8,
    backgroundColor: COLORS.paper,
  },
  title: {
    fontFamily: FONTS.bodySemibold,
    fontSize: 16,
    color: COLORS.ink,
  },
  hint: {
    fontFamily: FONTS.body,
    fontSize: 12,
    color: COLORS.ink3,
    lineHeight: 17,
    marginBottom: 4,
  },
  fieldLabel: {
    fontFamily: FONTS.mono,
    fontSize: 12,
    letterSpacing: 1.4,
    textTransform: 'uppercase',
    color: COLORS.ink3,
    marginTop: 6,
  },

  presetHint: {
    fontFamily: FONTS.body,
    fontSize: 12,
    color: COLORS.ink3,
    lineHeight: 15,
    marginTop: 2,
  },
  presetWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 6,
    marginBottom: 4,
  },
  presetChip: {
    borderWidth: 1,
    borderColor: COLORS.hair,
    borderRadius: radius.sm,
    paddingHorizontal: 10,
    paddingVertical: 10,
  },
  presetChipOn: {
    borderColor: COLORS.brick,
    backgroundColor: color.accentTint,
  },
  presetChipText: {
    fontFamily: FONTS.body,
    fontSize: 12,
    color: COLORS.ink2,
  },
  presetChipTextOn: {
    color: COLORS.ink,
    fontFamily: FONTS.bodySemibold,
  },
  stationRow: { marginTop: 4, marginBottom: 4 },
  stationChip: {
    borderWidth: 1,
    borderColor: COLORS.hair,
    borderRadius: radius.sm,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginRight: 8,
  },
  stationChipOn: {
    borderColor: COLORS.brick,
    backgroundColor: color.accentTint,
  },
  stationChipText: {
    fontFamily: FONTS.body,
    fontSize: 12,
    color: COLORS.ink2,
  },
  stationChipTextOn: {
    color: COLORS.ink,
    fontFamily: FONTS.bodySemibold,
  },
  row2: { flexDirection: 'row', gap: 10 },
  input: {
    borderWidth: 1,
    borderColor: COLORS.hair,
    borderRadius: radius.sm,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontFamily: FONTS.body,
    fontSize: 14,
    color: COLORS.ink,
    backgroundColor: COLORS.paperSoft || COLORS.paper,
    marginTop: 4,
  },
  addBtn: {
    marginTop: 10,
    alignSelf: 'flex-start',
    backgroundColor: COLORS.brick,
    borderRadius: radius.sm,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  addBtnText: {
    fontFamily: FONTS.bodySemibold,
    fontSize: 14,
    color: color.textOnAccent,
  },
  msg: {
    fontFamily: FONTS.body,
    fontSize: 12,
    marginTop: 4,
  },
  msgOk: { color: COLORS.ok },
  msgErr: { color: COLORS.bad },
  count: {
    fontFamily: FONTS.mono,
    fontSize: 12,
    color: COLORS.ink3,
    marginTop: 10,
  },
  tagRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: COLORS.hair,
  },
  tagLabel: {
    fontFamily: FONTS.bodySemibold,
    fontSize: 13,
    color: COLORS.ink,
  },
  tagMeta: {
    fontFamily: FONTS.mono,
    fontSize: 12,
    color: COLORS.ink3,
    marginTop: 2,
  },
  removeBtn: {
    borderWidth: 1,
    borderColor: COLORS.hair,
    borderRadius: radius.sm,
    paddingHorizontal: 10,
    paddingVertical: 10,
  },
  removeBtnText: {
    fontFamily: FONTS.body,
    fontSize: 12,
    color: COLORS.ink2,
  },
});
