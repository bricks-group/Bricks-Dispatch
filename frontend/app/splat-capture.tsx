import { useRef, useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity,
  Alert, Animated, Easing, Platform,
} from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { actorHeaders } from '../lib/api';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';
const MAX_DURATION_S = 90;

const GUIDANCE_STEPS = [
  'Walk around the exterior slowly',
  'Move indoors — orbit each main room',
  'Keep camera level and steady',
  'Capture corners and ceilings',
];

type UploadState = 'idle' | 'uploading' | 'success' | 'failure';

export default function SplatCaptureScreen() {
  const { jobId } = useLocalSearchParams<{ jobId: string }>();
  const router = useRouter();

  // Permissions
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [micPermission, requestMicPermission] = useMicrophonePermissions();

  // Camera ref
  const cameraRef = useRef<CameraView>(null);

  // Recording state
  const [isRecording, setIsRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [recordingUri, setRecordingUri] = useState<string | null>(null);
  const [guidanceIdx, setGuidanceIdx] = useState(0);

  // Upload state
  const [uploadState, setUploadState] = useState<UploadState>('idle');
  const [showConfirm, setShowConfirm] = useState(false);

  // Animated values
  const progressAnim = useRef(new Animated.Value(0)).current;
  const blinkAnim = useRef(new Animated.Value(1)).current;
  const guidanceOpacity = useRef(new Animated.Value(1)).current;

  // Timers
  const elapsedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const guidanceRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Progress ring animation ──────────────────────────────
  useEffect(() => {
    Animated.timing(progressAnim, {
      toValue: elapsed / MAX_DURATION_S,
      duration: 800,
      easing: Easing.out(Easing.quad),
      useNativeDriver: false,
    }).start();
  }, [elapsed]);

  // ── Blink animation ──────────────────────────────────────
  useEffect(() => {
    if (isRecording) {
      const loop = Animated.loop(
        Animated.sequence([
          Animated.timing(blinkAnim, { toValue: 0, duration: 500, useNativeDriver: true }),
          Animated.timing(blinkAnim, { toValue: 1, duration: 500, useNativeDriver: true }),
        ])
      );
      loop.start();
      return () => loop.stop();
    } else {
      blinkAnim.setValue(1);
    }
  }, [isRecording]);

  // ── Timers start/stop ────────────────────────────────────
  const startTimers = useCallback(() => {
    elapsedRef.current = 0;
    timerRef.current = setInterval(() => {
      elapsedRef.current += 1;
      setElapsed(elapsedRef.current);
      if (elapsedRef.current >= MAX_DURATION_S) {
        stopRecording();
      }
    }, 1000);

    guidanceRef.current = setInterval(() => {
      // Fade out → update → fade in
      Animated.timing(guidanceOpacity, { toValue: 0, duration: 300, useNativeDriver: true }).start(() => {
        setGuidanceIdx(i => (i + 1) % GUIDANCE_STEPS.length);
        Animated.timing(guidanceOpacity, { toValue: 1, duration: 400, useNativeDriver: true }).start();
      });
    }, 8000);
  }, []);

  const clearTimers = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (guidanceRef.current) { clearInterval(guidanceRef.current); guidanceRef.current = null; }
  }, []);

  // ── Permissions guard ────────────────────────────────────
  useEffect(() => {
    (async () => {
      if (!cameraPermission?.granted) await requestCameraPermission();
      if (!micPermission?.granted) await requestMicPermission();
    })();
  }, []);

  // ── Recording ────────────────────────────────────────────
  const startRecording = async () => {
    if (!cameraRef.current || isRecording) return;
    setIsRecording(true);
    setElapsed(0);
    startTimers();
    try {
      const video = await cameraRef.current.recordAsync({ maxDuration: MAX_DURATION_S });
      if (video?.uri) {
        setRecordingUri(video.uri);
        setShowConfirm(true);
      }
    } catch (e) {
      console.error('Recording error', e);
      Alert.alert('Recording failed', 'Could not start recording. Check camera permissions.');
    } finally {
      setIsRecording(false);
      clearTimers();
    }
  };

  const stopRecording = useCallback(() => {
    clearTimers();
    cameraRef.current?.stopRecording();
    setIsRecording(false);
  }, [clearTimers]);

  const handleRecordToggle = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  // ── Upload ────────────────────────────────────────────────
  const uploadVideo = async () => {
    if (!recordingUri || !jobId) return;
    setUploadState('uploading');

    try {
      const headers = await actorHeaders();
      const form = new FormData();
      const filename = `splat_${jobId}_${Date.now()}.mp4`;
      // React Native FormData accepts file objects this way
      (form as any).append('file', {
        uri: recordingUri,
        name: filename,
        type: 'video/mp4',
      });

      const res = await fetch(
        `${API}/api/v1/post-production/splat-upload/${jobId}`,
        {
          method: 'POST',
          headers: { ...headers }, // do NOT set Content-Type — let fetch set multipart boundary
          body: form,
        }
      );

      if (res.ok) {
        setUploadState('success');
      } else {
        const text = await res.text().catch(() => '');
        console.error('Splat upload error', res.status, text);
        setUploadState('failure');
      }
    } catch (e) {
      console.error('Splat upload exception', e);
      setUploadState('failure');
    }
  };

  const discardAndGoBack = () => {
    setRecordingUri(null);
    setShowConfirm(false);
    setUploadState('idle');
    setElapsed(0);
    router.back();
  };

  // ── Formatted timer ──────────────────────────────────────
  const timerLabel = (() => {
    const m = Math.floor(elapsed / 60).toString().padStart(2, '0');
    const s = (elapsed % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  })();

  // ── Ring geometry ────────────────────────────────────────
  const RING_SIZE = 180;
  const STROKE = 5;
  const RADIUS = (RING_SIZE - STROKE) / 2;
  const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

  if (!cameraPermission?.granted || !micPermission?.granted) {
    return (
      <View style={s.permContainer}>
        <Text style={s.permText}>Camera and microphone access required for 3D capture.</Text>
        <TouchableOpacity
          onPress={async () => {
            await requestCameraPermission();
            await requestMicPermission();
          }}
          style={s.permBtn}
        >
          <Text style={s.permBtnText}>GRANT ACCESS</Text>
        </TouchableOpacity>
      </View>
    );
  }

  // ── Upload confirmation overlay ───────────────────────────
  if (showConfirm) {
    return (
      <View style={s.confirmContainer}>
        <View style={s.confirmSheet}>
          <View style={s.sheetHandle} />

          {uploadState === 'idle' && (
            <>
              <Text style={s.confirmBadge}>3D CAPTURE COMPLETE</Text>
              <Text style={s.confirmTitle}>
                Upload this {elapsed > 59 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`} capture{'\n'}for Gaussian splat processing.
              </Text>
              <Text style={s.confirmSub}>
                Processing takes approx. 20 minutes.{'\n'}You'll be notified when the 3D tour is ready.
              </Text>
              <TouchableOpacity style={s.uploadBtn} onPress={uploadVideo} activeOpacity={0.85}>
                <Text style={s.uploadBtnText}>UPLOAD FOR PROCESSING</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={discardAndGoBack} style={s.discardBtn}>
                <Text style={s.discardText}>Discard</Text>
              </TouchableOpacity>
            </>
          )}

          {uploadState === 'uploading' && (
            <>
              <View style={s.spinnerWrap}>
                <Text style={s.spinnerIcon}>◌</Text>
              </View>
              <Text style={s.confirmTitle}>Uploading capture…</Text>
              <Text style={s.confirmSub}>Keep the app open.</Text>
            </>
          )}

          {uploadState === 'success' && (
            <>
              <Text style={s.successIcon}>✓</Text>
              <Text style={s.confirmTitle}>Upload complete</Text>
              <Text style={s.confirmSub}>
                Gaussian splat processing has started.{'\n'}Your 3D tour will be ready in ~20 min.
              </Text>
              <TouchableOpacity style={s.uploadBtn} onPress={discardAndGoBack} activeOpacity={0.85}>
                <Text style={s.uploadBtnText}>DONE</Text>
              </TouchableOpacity>
            </>
          )}

          {uploadState === 'failure' && (
            <>
              <Text style={s.failIcon}>!</Text>
              <Text style={s.confirmTitle}>Upload failed</Text>
              <Text style={s.confirmSub}>Network error — check your connection.</Text>
              <View style={s.retryRow}>
                <TouchableOpacity onPress={discardAndGoBack} style={s.discardBtn}>
                  <Text style={s.discardText}>Discard</Text>
                </TouchableOpacity>
                <TouchableOpacity style={s.uploadBtn} onPress={uploadVideo} activeOpacity={0.85}>
                  <Text style={s.uploadBtnText}>RETRY</Text>
                </TouchableOpacity>
              </View>
            </>
          )}
        </View>
      </View>
    );
  }

  // ── Main capture UI ──────────────────────────────────────
  return (
    <View style={s.root}>
      {/* Camera */}
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
        mode="video"
      />

      {/* Vignette */}
      <View style={s.vignette} pointerEvents="none" />

      {/* Top bar */}
      <View style={s.topBar}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={16}>
          <Text style={s.cancelTopText}>CANCEL</Text>
        </TouchableOpacity>

        <View style={s.badge}>
          <Text style={s.badgeText}>3D CAPTURE</Text>
        </View>

        {/* Spacer mirror */}
        <Text style={[s.cancelTopText, { opacity: 0 }]}>CANCEL</Text>
      </View>

      {/* Center: progress ring + timer */}
      <View style={s.ringWrap}>
        {/* SVG-like ring using border */}
        <View style={[s.ringTrack, { width: RING_SIZE, height: RING_SIZE, borderRadius: RING_SIZE / 2 }]}>
          {/* Animated progress overlay — use a conic approximation via rotation + clip */}
          <Animated.View
            style={[
              s.ringProgress,
              {
                width: RING_SIZE,
                height: RING_SIZE,
                borderRadius: RING_SIZE / 2,
                opacity: progressAnim.interpolate({
                  inputRange: [0, 1],
                  outputRange: [0.3, 1],
                }),
              },
            ]}
          />
        </View>

        {/* Timer */}
        <View style={s.ringCenter}>
          <Text style={s.timerText}>{timerLabel}</Text>
          {isRecording ? (
            <View style={s.recRow}>
              <Animated.View style={[s.recDot, { opacity: blinkAnim }]} />
              <Text style={s.recLabel}>REC</Text>
            </View>
          ) : (
            <Text style={s.maxLabel}>90 SEC MAX</Text>
          )}
        </View>
      </View>

      {/* Guidance text */}
      <Animated.Text style={[s.guidance, { opacity: guidanceOpacity }]}>
        {GUIDANCE_STEPS[guidanceIdx]}
      </Animated.Text>

      {/* Bottom controls */}
      <View style={s.bottomControls}>
        {/* Record button */}
        <TouchableOpacity
          style={s.recordOuter}
          onPress={handleRecordToggle}
          activeOpacity={0.85}
          accessibilityLabel={isRecording ? 'Stop recording' : 'Start recording'}
        >
          {isRecording ? (
            <View style={s.stopSquare} />
          ) : (
            <View style={s.recordInner} />
          )}
        </TouchableOpacity>

        {/* Cancel */}
        <TouchableOpacity onPress={() => router.back()} hitSlop={12} style={s.cancelBtn}>
          <Text style={s.cancelBtnText}>CANCEL</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────

const s = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: TOKENS.night,
    alignItems: 'center',
  },

  // Camera vignette
  vignette: {
    ...StyleSheet.absoluteFillObject,
    // Top gradient
    backgroundColor: 'transparent',
  },

  // Top bar
  topBar: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 40,
    left: 0,
    right: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 24,
    zIndex: 10,
  },
  cancelTopText: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 11,
    color: 'rgba(244, 241, 235, 0.65)',
    letterSpacing: 1.4,
  },
  badge: {
    backgroundColor: 'rgba(210, 84, 43, 0.14)',
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 2,
  },
  badgeText: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 11,
    color: TOKENS.brick,
    letterSpacing: 2,
  },

  // Ring
  ringWrap: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    right: 0,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 5,
  },
  ringTrack: {
    borderWidth: 5,
    borderColor: 'rgba(244, 241, 235, 0.10)',
    position: 'absolute',
  },
  ringProgress: {
    position: 'absolute',
    borderWidth: 5,
    borderColor: TOKENS.brick,
    borderRadius: 90,
  },
  ringCenter: {
    alignItems: 'center',
    gap: 6,
  },
  timerText: {
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 42,
    color: TOKENS.moon,
    letterSpacing: -1,
  },
  recRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  recDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: TOKENS.brick,
  },
  recLabel: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 9,
    color: TOKENS.brick,
    letterSpacing: 1.8,
  },
  maxLabel: {
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 9,
    color: 'rgba(244, 241, 235, 0.35)',
    letterSpacing: 1.4,
  },

  // Guidance
  guidance: {
    position: 'absolute',
    bottom: 170,
    left: 32,
    right: 32,
    textAlign: 'center',
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 14,
    color: 'rgba(244, 241, 235, 0.85)',
    zIndex: 10,
  },

  // Bottom controls
  bottomControls: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 52 : 32,
    alignItems: 'center',
    gap: 22,
    zIndex: 10,
  },
  recordOuter: {
    width: 80,
    height: 80,
    borderRadius: 40,
    borderWidth: 3,
    borderColor: TOKENS.moon,
    alignItems: 'center',
    justifyContent: 'center',
  },
  recordInner: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: TOKENS.brick,
  },
  stopSquare: {
    width: 28,
    height: 28,
    borderRadius: 4,
    backgroundColor: TOKENS.brick,
  },
  cancelBtn: {
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  cancelBtnText: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 11,
    color: 'rgba(244, 241, 235, 0.5)',
    letterSpacing: 1.8,
  },

  // Permission screen
  permContainer: {
    flex: 1,
    backgroundColor: TOKENS.night,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },
  permText: {
    fontFamily: TYPOGRAPHY.body,
    fontSize: 14,
    color: TOKENS.moonSoft,
    textAlign: 'center',
    lineHeight: 22,
    marginBottom: 28,
  },
  permBtn: {
    backgroundColor: TOKENS.brick,
    paddingHorizontal: 28,
    paddingVertical: 14,
    borderRadius: 2,
  },
  permBtnText: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 12,
    color: '#FFF',
    letterSpacing: 1.4,
  },

  // Confirm sheet
  confirmContainer: {
    flex: 1,
    backgroundColor: 'rgba(14, 11, 9, 0.85)',
    justifyContent: 'flex-end',
  },
  confirmSheet: {
    backgroundColor: TOKENS.nightSoft,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 28,
    paddingBottom: Platform.OS === 'ios' ? 48 : 32,
    paddingTop: 20,
    alignItems: 'center',
    gap: 14,
  },
  sheetHandle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: 'rgba(244, 241, 235, 0.18)',
    marginBottom: 10,
  },
  confirmBadge: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 11,
    color: TOKENS.brick,
    letterSpacing: 1.8,
  },
  confirmTitle: {
    fontFamily: TYPOGRAPHY.heading,
    fontSize: 16,
    color: TOKENS.moon,
    textAlign: 'center',
    lineHeight: 24,
  },
  confirmSub: {
    fontFamily: TYPOGRAPHY.body,
    fontSize: 12,
    color: 'rgba(244, 241, 235, 0.45)',
    textAlign: 'center',
    lineHeight: 19,
  },
  uploadBtn: {
    backgroundColor: TOKENS.brick,
    paddingHorizontal: 28,
    paddingVertical: 15,
    borderRadius: 2,
    alignSelf: 'stretch',
    alignItems: 'center',
    marginTop: 4,
  },
  uploadBtnText: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 13,
    color: '#FFF',
    letterSpacing: 1.2,
  },
  discardBtn: {
    paddingVertical: 10,
    paddingHorizontal: 16,
  },
  discardText: {
    fontFamily: TYPOGRAPHY.body,
    fontSize: 13,
    color: 'rgba(244, 241, 235, 0.4)',
  },
  retryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
    marginTop: 4,
  },

  // Uploading / success / failure
  spinnerWrap: {
    paddingTop: 12,
  },
  spinnerIcon: {
    fontSize: 40,
    color: TOKENS.brick,
  },
  successIcon: {
    fontSize: 48,
    color: TOKENS.brick,
    fontWeight: '200',
  },
  failIcon: {
    fontSize: 40,
    color: TOKENS.brick,
    fontWeight: '300',
  },
});
