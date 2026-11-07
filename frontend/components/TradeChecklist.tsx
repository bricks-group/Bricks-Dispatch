/**
 * Trade job checklist (cleaner, stager, de-staging, repairs, inspector, measurer).
 * Tick each item and attach its proof; the API saves it on the job and won't
 * let the job be marked complete until every item is ticked with its proof.
 *
 * Self-contained on purpose: React Native core + expo-image-picker only, colours
 * passed in, so the same file runs on the store binary's OTA branch.
 */
import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Image, Linking, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { actorHeaders } from '../lib/api';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export type ChecklistPalette = {
  text: string;
  muted: string;
  faint: string;
  line: string;
  surface: string;
  accent: string;
  accentText: string;
  ok: string;
  error: string;
};

type Proof = 'tick' | 'photo' | 'inventory' | 'report' | 'findings';
type InventoryRow = { item: string; room: string; note?: string };
type Item = {
  id: string;
  label: string;
  completed: boolean;
  proof: Proof;
  photos: { id: string; url?: string | null }[];
  inventory?: InventoryRow[];
  report?: { filename?: string | null; url?: string | null } | null;
  note?: string | null;
  none_found?: boolean;
  photo_required?: boolean;
  missing?: string | null;
};
export type TradeChecklistData = {
  job_id: string;
  title: string;
  editable: boolean;
  items: Item[];
  complete: boolean;
  missing: string[];
};

async function call(path: string, init: RequestInit = {}, json = true): Promise<TradeChecklistData> {
  const headers = await actorHeaders(json && init.body ? { 'Content-Type': 'application/json' } : {});
  const res = await fetch(`${API}${path}`, { ...init, headers: { ...headers, ...(init.headers as any || {}) } });
  if (!res.ok) {
    const err = await res.json().catch(() => ({} as any));
    const d = err?.detail;
    throw new Error((d && typeof d === 'object' && d.message) || (typeof d === 'string' ? d : '') || `Couldn't save (${res.status}).`);
  }
  return res.json();
}

function imagePart(asset: ImagePicker.ImagePickerAsset, name: string) {
  return { uri: asset.uri, name, type: asset.mimeType || 'image/jpeg' } as any;
}

export function TradeChecklist({
  jobId, status, palette: c, onChange,
}: {
  jobId: string;
  status: string;
  palette: ChecklistPalette;
  onChange?: (d: TradeChecklistData) => void;
}) {
  const [data, setData] = useState<TradeChecklistData | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const base = `/api/vendor-os/jobs/${jobId}/checklist`;

  const apply = useCallback((d: TradeChecklistData) => { setData(d); onChange?.(d); }, [onChange]);

  useEffect(() => {
    let off = false;
    call(base).then((d) => { if (!off) apply(d); }).catch((e) => { if (!off) setErr(e.message); });
    return () => { off = true; };
  }, [base, status]);

  const run = async (id: string, fn: () => Promise<TradeChecklistData | null>) => {
    setBusy(id); setErr('');
    try { const d = await fn(); if (d) apply(d); }
    catch (e: any) { setErr(e?.message || 'Couldn’t save. Try again.'); }
    finally { setBusy(null); }
  };

  const pick = async (camera: boolean, multiple: boolean) => {
    if (camera) {
      const perm = await ImagePicker.requestCameraPermissionsAsync();
      if (!perm.granted) {
        Alert.alert('Camera is off', 'Allow camera access in Settings to add photos.');
        return [];
      }
      const shot = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.4, exif: false });
      return shot.canceled ? [] : shot.assets;
    }
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'], quality: 0.4, exif: false, allowsMultipleSelection: multiple, selectionLimit: multiple ? 20 : 1,
    });
    return res.canceled ? [] : res.assets;
  };

  const addPhotos = (item: Item, camera: boolean) => run(item.id, async () => {
    const assets = await pick(camera, true);
    let last: TradeChecklistData | null = null;
    for (const a of assets) {
      const form = new FormData();
      form.append('photo', imagePart(a, 'photo.jpg'));
      form.append('taken_at', new Date().toISOString());
      last = await call(`${base}/${item.id}/photos`, { method: 'POST', body: form }, false);
      apply(last);
    }
    return last;
  });

  const addReport = (item: Item, camera: boolean) => run(item.id, async () => {
    const [a] = await pick(camera, false);
    if (!a) return null;
    const form = new FormData();
    form.append('report', imagePart(a, 'report.jpg'));
    return call(`${base}/${item.id}/report`, { method: 'POST', body: form }, false);
  });

  const choose = (title: string, onCamera: () => void, onLibrary: () => void) => {
    Alert.alert(title, undefined, [
      { text: 'Take photo', onPress: onCamera },
      { text: 'Choose from library', onPress: onLibrary },
      { text: 'Cancel', style: 'cancel' },
    ]);
  };

  const patch = (item: Item, body: Record<string, unknown>) =>
    run(item.id, () => call(`${base}/${item.id}`, { method: 'PATCH', body: JSON.stringify(body) }));

  if (!data) {
    return err ? <Text style={[st.small, { color: c.error }]}>{err}</Text> : <ActivityIndicator color={c.muted} />;
  }
  const editable = data.editable && ['assigned', 'in_progress'].includes(status);

  return (
    <View style={st.wrap}>
      <Text style={[st.small, { color: c.muted }]}>
        {editable
          ? 'Tick each item as you finish it. Items that need photos, an inventory or a report can only be ticked once those are added.'
          : data.complete ? 'All done.' : 'This job is closed.'}
      </Text>
      {data.items.map((item) => (
        <ItemRow
          key={item.id}
          item={item}
          c={c}
          editable={editable}
          busy={busy === item.id}
          locked={busy !== null}
          onTick={(v) => patch(item, { completed: v })}
          onPatch={(b) => patch(item, b)}
          onAddPhotos={() => choose('Add photos', () => addPhotos(item, true), () => addPhotos(item, false))}
          onRemovePhoto={(pid) => run(item.id, () => call(`${base}/${item.id}/photos/${pid}`, { method: 'DELETE' }))}
          onReport={() => choose('Add the report', () => addReport(item, true), () => addReport(item, false))}
        />
      ))}
      {err ? <Text style={[st.small, { color: c.error }]}>{err}</Text> : null}
    </View>
  );
}

function Btn({ title, onPress, c, disabled, primary }: { title: string; onPress: () => void; c: ChecklistPalette; disabled?: boolean; primary?: boolean }) {
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled}
      style={[st.btn, { borderColor: c.line, backgroundColor: primary ? c.accent : 'transparent', opacity: disabled ? 0.45 : 1 }]}
    >
      <Text style={[st.btnText, { color: primary ? c.accentText : c.text }]}>{title}</Text>
    </Pressable>
  );
}

function ItemRow({
  item, c, editable, busy, locked, onTick, onPatch, onAddPhotos, onRemovePhoto, onReport,
}: {
  item: Item; c: ChecklistPalette; editable: boolean; busy: boolean; locked: boolean;
  onTick: (v: boolean) => void;
  onPatch: (b: Record<string, unknown>) => void;
  onAddPhotos: () => void;
  onRemovePhoto: (id: string) => void;
  onReport: () => void;
}) {
  const [note, setNote] = useState(item.note || '');
  const blank = [{ item: '', room: '' }];
  const [rows, setRows] = useState<InventoryRow[]>(item.inventory?.length ? item.inventory : blank);
  useEffect(() => { setNote(item.note || ''); }, [item.note]);
  const savedRows = JSON.stringify(item.inventory || []);
  useEffect(() => { setRows(item.inventory?.length ? item.inventory : blank); }, [savedRows]);

  const off = !editable || locked;
  const canTick = !item.missing || item.missing === 'tick it';
  const takesPhotos = item.proof === 'photo' || item.proof === 'findings';
  const cleanRows = rows.filter((r) => r.item.trim() || r.room.trim() || (r.note || '').trim());
  const input = [st.input, { color: c.text, borderColor: c.line, backgroundColor: c.surface }];

  return (
    <View style={[st.item, { borderTopColor: c.line }]}>
      <Pressable
        accessibilityRole="checkbox"
        accessibilityState={{ checked: item.completed, disabled: off || (!item.completed && !canTick) }}
        accessibilityLabel={item.label}
        disabled={off || (!item.completed && !canTick)}
        onPress={() => onTick(!item.completed)}
        style={st.tickRow}
      >
        <View style={[st.box, { borderColor: item.completed ? c.ok : c.faint, backgroundColor: item.completed ? c.ok : 'transparent' }]}>
          {item.completed ? <Text style={st.boxMark}>✓</Text> : null}
        </View>
        <View style={st.flex}>
          <Text style={[st.label, { color: item.completed ? c.faint : c.text }]}>{item.label}</Text>
          {item.missing && item.missing !== 'tick it' ? (
            <Text style={[st.small, { color: c.muted }]}>To tick this, {item.missing}.</Text>
          ) : null}
        </View>
        {busy ? <ActivityIndicator size="small" color={c.muted} /> : null}
      </Pressable>

      {takesPhotos ? (
        <View style={st.indent}>
          {item.photos.length ? (
            <View style={st.thumbs}>
              {item.photos.map((p) => (
                <Pressable
                  key={p.id}
                  onPress={() => p.url && Linking.openURL(p.url)}
                  onLongPress={() => editable && Alert.alert('Remove this photo?', undefined, [
                    { text: 'Cancel', style: 'cancel' },
                    { text: 'Remove', style: 'destructive', onPress: () => onRemovePhoto(p.id) },
                  ])}
                  accessibilityLabel="Photo. Long press to remove."
                >
                  {p.url ? <Image source={{ uri: p.url }} style={[st.thumb, { backgroundColor: c.surface }]} /> : <View style={[st.thumb, { backgroundColor: c.surface }]} />}
                </Pressable>
              ))}
            </View>
          ) : null}
          {editable ? <Btn c={c} disabled={off} title={item.photos.length ? 'Add more photos' : 'Add photos'} onPress={onAddPhotos} /> : null}
          {editable && item.photos.length ? <Text style={[st.small, { color: c.faint }]}>Long press a photo to remove it.</Text> : null}
        </View>
      ) : null}

      {item.proof === 'findings' ? (
        <View style={st.indent}>
          <TextInput
            value={note}
            onChangeText={setNote}
            editable={!off && !item.none_found}
            placeholder="What you found, and where"
            placeholderTextColor={c.faint}
            multiline
            style={[input, st.multi]}
          />
          {editable ? (
            <View style={st.btnRow}>
              <Btn c={c} disabled={off || note.trim() === (item.note || '').trim()} title="Save note" onPress={() => onPatch({ note })} />
              <Btn c={c} disabled={off} title={item.none_found ? 'Undo nothing to report' : 'Nothing to report'} onPress={() => onPatch({ none_found: !item.none_found })} />
            </View>
          ) : null}
        </View>
      ) : null}

      {item.proof === 'inventory' ? (
        <View style={st.indent}>
          {rows.map((r, i) => (
            <View key={i} style={st.invRow}>
              <TextInput value={r.item} editable={!off} placeholder="Item" placeholderTextColor={c.faint} style={[input, st.flex]}
                onChangeText={(v) => setRows(rows.map((x, j) => j === i ? { ...x, item: v } : x))} accessibilityLabel="Item" />
              <TextInput value={r.room} editable={!off} placeholder="Room" placeholderTextColor={c.faint} style={[input, st.flex]}
                onChangeText={(v) => setRows(rows.map((x, j) => j === i ? { ...x, room: v } : x))} accessibilityLabel="Room" />
              {editable && rows.length > 1 ? (
                <Pressable accessibilityLabel="Remove line" onPress={() => setRows(rows.filter((_, j) => j !== i))} style={st.remove}>
                  <Text style={{ color: c.muted, fontSize: 18 }}>×</Text>
                </Pressable>
              ) : null}
            </View>
          ))}
          {editable ? (
            <View style={st.btnRow}>
              <Btn c={c} disabled={off} title="Add a line" onPress={() => setRows([...rows, { item: '', room: '' }])} />
              <Btn c={c} primary disabled={off || JSON.stringify(cleanRows) === savedRows} title="Save inventory" onPress={() => onPatch({ inventory: cleanRows })} />
            </View>
          ) : null}
        </View>
      ) : null}

      {item.proof === 'report' ? (
        <View style={st.indent}>
          {item.report ? (
            <Text style={[st.small, { color: c.text }]} onPress={() => item.report?.url && Linking.openURL(item.report.url)}>
              {item.report.filename || 'Report'} · added
            </Text>
          ) : null}
          {editable ? <Btn c={c} disabled={off} title={item.report ? 'Replace report' : 'Add the report'} onPress={onReport} /> : null}
          <Text style={[st.small, { color: c.faint }]}>Add a photo of each report page here, or upload the PDF in Bricks on a computer.</Text>
        </View>
      ) : null}
    </View>
  );
}

const st = StyleSheet.create({
  wrap: { gap: 4 },
  flex: { flex: 1 },
  item: { borderTopWidth: StyleSheet.hairlineWidth, paddingVertical: 10, gap: 8 },
  tickRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, minHeight: 44 },
  box: { width: 24, height: 24, borderRadius: 6, borderWidth: 2, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  boxMark: { color: '#fff', fontSize: 14, fontWeight: '700' },
  label: { fontSize: 16, lineHeight: 22 },
  small: { fontSize: 13, lineHeight: 18 },
  indent: { marginLeft: 36, gap: 8 },
  thumbs: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  thumb: { width: 56, height: 56, borderRadius: 6 },
  btn: { minHeight: 44, paddingHorizontal: 14, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center', alignSelf: 'flex-start' },
  btnText: { fontSize: 15, fontWeight: '600' },
  btnRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  input: { minHeight: 44, borderWidth: StyleSheet.hairlineWidth, borderRadius: 8, paddingHorizontal: 10, fontSize: 15 },
  multi: { minHeight: 72, paddingTop: 10, textAlignVertical: 'top' },
  invRow: { flexDirection: 'row', gap: 6, alignItems: 'center' },
  remove: { width: 36, height: 44, alignItems: 'center', justifyContent: 'center' },
});
