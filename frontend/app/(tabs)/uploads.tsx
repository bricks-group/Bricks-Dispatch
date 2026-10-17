import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, RefreshControl, Alert } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { Upload, FileText } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { color, type as T, space, layout, type Tone } from '../../theme';
import { Screen, PageHeader, Section, Group, Row, Button, Pill, Check, Chip, ChipRow, EmptyState, LoadingState } from '../../components/ui';
import {
  BRACKET_HINT,
  cameraRawRejectionMessage,
  isCameraRawName,
  requiresBracketSet,
  validateHdrBracketSelection,
} from '../../lib/bracket-require';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

const STILLS_TYPES = [
  { id: 'hdr', label: 'HDR' },
  { id: 'drone', label: 'Drone' },
  { id: 'reel', label: 'Reel' },
  { id: 'twilight', label: 'Twilight' },
  { id: 'staging', label: 'Staging' },
  { id: 'floorplan', label: 'Floor plan' },
] as const;
type StillsType = (typeof STILLS_TYPES)[number]['id'];

async function putStill(jobId: string, asset: { uri: string; fileName?: string | null; mimeType?: string | null; fileSize?: number | null }, mediaType: StillsType, headers: Record<string, string>) {
  const filename = asset.fileName || `upload_${Date.now()}_${Math.random().toString(36).slice(2, 7)}.jpg`;
  const mime = asset.mimeType || 'image/jpeg';
  const signRes = await fetch(`${API}/api/uploads/sign`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      job_id: jobId,
      filename,
      mime_type: mime,
      file_size: asset.fileSize || 0,
      media_type: mediaType,
    }),
  });
  const sign = await signRes.json().catch(() => ({}));
  if (!signRes.ok || !sign.signed_url) {
    throw new Error(sign.detail || sign.message || `Sign failed (${signRes.status})`);
  }
  const blobRes = await fetch(asset.uri);
  const blob = await blobRes.blob();
  const put = await fetch(sign.signed_url, {
    method: 'PUT',
    headers: { 'Content-Type': sign.content_type || mime },
    body: blob,
  });
  if (!put.ok) throw new Error(`Upload failed (${put.status})`);
  const done = await fetch(`${API}/api/uploads/complete`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      job_id: jobId,
      upload_id: sign.upload_id,
      storage_path: sign.storage_path,
      filename,
      mime_type: mime,
      file_size: asset.fileSize || blob.size || 0,
      media_type: mediaType,
    }),
  });
  if (!done.ok) throw new Error(`Complete failed (${done.status})`);
  return done.json();
}


type UploadRecord = {
  id: string; job_id: string; filename: string; file_size: number;
  mime_type: string; created_at: string; media_type?: string;
};

type Job = {
  id: string; address: string; status: string;
  deliverables_count: number; uploaded_count: number;
};

type JobGroup = {
  job: Job;
  uploads: UploadRecord[];
};

function fmtBytes(bytes: number) {
  if (!bytes) return '—';
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

function jobStatusPill(job: Job): { label: string; tone: Tone } {
  if (job.status === 'completed') return { label: 'Delivered', tone: 'ok' };
  if (job.status === 'in_progress') return { label: 'In progress', tone: 'accent' };
  return { label: 'Scheduled', tone: 'neutral' };
}

const TYPE_HINT: Record<StillsType, string> = {
  hdr: `${BRACKET_HINT} At least 3 exposures per angle; single frames are rejected. JPEG or HEIC, no camera RAW.`,
  twilight: `${BRACKET_HINT} Real dusk only; the dusk sky is kept, never swapped.`,
  drone: 'JPEG or MP4 straight from the card.',
  reel: 'MP4 straight from the card.',
  staging: 'Delivered to the listing as the original. No enhance.',
  floorplan: 'CubiCasa, PDF or PNG export. Delivered as the original.',
};

export default function UploadsScreen() {
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [stillsType, setStillsType] = useState<StillsType>('hdr');
  const [targetJobId, setTargetJobId] = useState('');

  const fetchData = useCallback(async () => {
    try {
      const headers = await actorHeaders();
      const [upRes, jobRes] = await Promise.all([
        fetch(`${API}/api/uploads`, { headers }),
        fetch(`${API}/api/jobs`, { headers }),
      ]);
      if (!upRes.ok) throw new Error(`Uploads API error: ${upRes.status}`);
      if (!jobRes.ok) throw new Error(`Jobs API error: ${jobRes.status}`);
      setUploads(await upRes.json());
      setJobs(await jobRes.json());
    } catch (e) {
      console.error('Uploads fetch error', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const pickAndUpload = async () => {
    const activeJobs = jobs.filter(j => ['in_progress', 'completed', 'processing'].includes(j.status));
    if (activeJobs.length === 0) {
      Alert.alert('No active jobs', 'Check in on a job before uploading stills.');
      return;
    }
    const targetJob = activeJobs.find(j => j.id === targetJobId) || activeJobs[0];

    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert('Permission required', 'Allow photo library access to upload files.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images', 'videos'],
      quality: 1,
      allowsMultipleSelection: true,
      allowsEditing: false,
    });
    if (result.canceled || !result.assets?.length) return;

    const assetNames = result.assets.map((a, i) => {
      const fromName = a.fileName || '';
      const fromUri = (a.uri || '').split(/[/\\]/).pop() || '';
      return fromName || fromUri || `upload_${Date.now()}_${i}.jpg`;
    });
    if (assetNames.some((n) => isCameraRawName(n))) {
      Alert.alert('Camera RAW not supported', cameraRawRejectionMessage);
      return;
    }

    setUploading(true);
    try {
      if (requiresBracketSet(stillsType)) {
        const existing = uploads
          .filter((u) => u.job_id === targetJob.id)
          .map((u) => u.filename);
        const names = assetNames;
        const issue = validateHdrBracketSelection(names, existing);
        if (issue) {
          Alert.alert('Brackets required', issue.message);
          return;
        }
      }
      const headers = await actorHeaders();
      for (const asset of result.assets) {
        // Preserve camera filename (±EV) for backend group_brackets.
        await putStill(targetJob.id, asset, stillsType, headers);
      }
      await fetchData();
    } catch (e) {
      Alert.alert('Upload failed', e instanceof Error ? e.message : 'Network error — check your connection.');
    } finally {
      setUploading(false);
    }
  };

  // Group uploads by job
  const jobMap = new Map(jobs.map(j => [j.id, j]));
  const groupMap = new Map<string, JobGroup>();
  for (const u of uploads) {
    const job = jobMap.get(u.job_id);
    if (!job) continue;
    if (!groupMap.has(u.job_id)) groupMap.set(u.job_id, { job, uploads: [] });
    groupMap.get(u.job_id)!.uploads.push(u);
  }
  const groups = Array.from(groupMap.values());
  const inProgress = groups.filter(g => g.job.status === 'in_progress');
  const recent = groups.filter(g => g.job.status !== 'in_progress');

  const activeJobs = jobs.filter(j => ['in_progress', 'completed', 'processing'].includes(j.status));
  const selectedJobId = targetJobId || activeJobs[0]?.id;

  return (
    <Screen
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchData(); }} tintColor={color.accent} />}
    >
      <PageHeader title="Upload" subtitle="Straight from your camera card." />

      <Section first label="What are you uploading?">
        <ChipRow>
          {STILLS_TYPES.map((t) => (
            <Chip key={t.id} label={t.label} selected={stillsType === t.id} onPress={() => setStillsType(t.id)} />
          ))}
        </ChipRow>
        <Text style={[T.bodySm, s.hint]}>{TYPE_HINT[stillsType]}</Text>
      </Section>

      {activeJobs.length > 1 ? (
        <Section label="Job">
          <Group>
            {activeJobs.map((j) => (
              <Row key={j.id} title={j.address} right={<Check checked={selectedJobId === j.id} />} onPress={() => setTargetJobId(j.id)} />
            ))}
          </Group>
        </Section>
      ) : null}

      <View style={s.cta}>
        <Button icon={Upload} title={uploading ? 'Uploading…' : 'Choose files'} onPress={pickAndUpload} loading={uploading} />
      </View>

      {loading && groups.length === 0 ? <LoadingState /> : null}

      {inProgress.length > 0 && (
        <Section label="In progress">
          <Group>
            {inProgress.map(({ job, uploads: ups }) => {
              const totalBytes = ups.reduce((sum, u) => sum + u.file_size, 0);
              const pct = job.deliverables_count > 0
                ? Math.round((job.uploaded_count / job.deliverables_count) * 100)
                : null;
              return (
                <View key={job.id} style={s.progressRow}>
                  <View style={s.progressTop}>
                    <View style={s.flex}>
                      <Text style={[T.bodyMedium, { color: color.text }]}>{job.address}</Text>
                      <Text style={[T.dataSm, { color: color.textMuted }]}>{ups.length} files · {fmtBytes(totalBytes)}</Text>
                    </View>
                    {pct !== null && <Text style={[T.dataMd, { color: color.accentText }]}>{pct}%</Text>}
                  </View>
                  {pct !== null && (
                    <View style={s.barTrack}>
                      <View style={[s.barFill, { width: `${pct}%` as any }]} />
                    </View>
                  )}
                </View>
              );
            })}
          </Group>
        </Section>
      )}

      {recent.length > 0 && (
        <Section label="Recent">
          <Group>
            {recent.map(({ job, uploads: ups }) => {
              const totalBytes = ups.reduce((sum, u) => sum + u.file_size, 0);
              const pill = jobStatusPill(job);
              return (
                <Row
                  key={job.id}
                  icon={FileText}
                  title={job.address}
                  subtitle={`${ups.length} file${ups.length === 1 ? '' : 's'} · ${fmtBytes(totalBytes)}`}
                  right={<Pill label={pill.label} tone={pill.tone} />}
                />
              );
            })}
          </Group>
        </Section>
      )}

      {!loading && groups.length === 0 && (
        <EmptyState icon={Upload} title="No uploads yet" body="Files you send for a job show up here with their delivery status." />
      )}
    </Screen>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1 },
  hint: { color: color.textMuted, marginTop: space[12] },
  cta: { marginTop: layout.section },
  progressRow: { padding: space[16], gap: space[12] },
  progressTop: { flexDirection: 'row', alignItems: 'flex-start', gap: space[12] },
  barTrack: { height: 2, backgroundColor: color.hair },
  barFill: { height: 2, backgroundColor: color.accent },
});
