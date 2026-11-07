/**
 * Guided capture camera (native). VisionCamera v5 (Nitro): AVFoundation on iOS, CameraX on Android.
 *
 * What this pins, from the guide's CameraTarget:
 *   lens          back ultra-wide-angle physical device (falls back to the default back camera)
 *   resolution    UHD 16:9 via resolutionBias on the video output (nearest available, e.g. 1080p)
 *   fps           30 when the device supports it
 *   stabilisation 'standard' when supported, otherwise 'off'
 *   HDR           off: video dynamic range forced to SDR 8-bit
 *   codec         H.265 when the output offers it, else H.264; bitrate from the guide; no audio
 *   locks         focusTo(centre, AE+AF+AWB, adaptiveness 'locked'), then on iOS
 *                 lockCurrentExposure / lockCurrentFocus / lockCurrentWhiteBalance.
 *                 CameraX has no true lock API in VisionCamera, so Android reports
 *                 'metering-locked' (the 3A loop is held on the metered region).
 *
 * No custom native module: everything above is public VisionCamera API.
 */
import React, { forwardRef, useCallback, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { Platform, StyleSheet, View, type LayoutChangeEvent, type StyleProp, type ViewStyle } from 'react-native';
import {
  Camera, CommonDynamicRanges, CommonResolutions, useCameraDevice, useCameraPermission, useVideoOutput,
  type CameraRef, type CameraSessionConfig, type Constraint, type Recorder, type RecordingFinishedReason,
} from 'react-native-vision-camera';
import type { CaptureGuide, LockKind } from '../../lib/guided-capture/guides';

export type LockStatus = 'locked' | 'metering-locked' | 'auto' | 'unsupported' | 'failed';
export type LockState = Record<LockKind, LockStatus>;

export interface CameraInfo {
  lens: 'ultra-wide' | 'wide';
  lensLabel: string;
  width: number | null;
  height: number | null;
  fps: number | null;
  stabilization: string | null;
  hdr: boolean;
  codec: string;
  bitrate: number;
  /** What this device can hard-lock (iOS) vs. only hold by metering (Android). */
  lockable: Record<LockKind, 'lock' | 'metering' | 'none'>;
}

export interface GuidedCameraHandle {
  lock(): Promise<LockState>;
  unlock(): Promise<void>;
  start(onFinished: (filePath: string, reason: RecordingFinishedReason) => void, onError: (e: Error) => void): Promise<void>;
  stop(): Promise<void>;
  cancel(): Promise<void>;
}

export interface GuidedCameraProps {
  guide: CaptureGuide;
  isActive: boolean;
  maxBytes: number;
  onReady?: (info: CameraInfo) => void;
  onError?: (message: string) => void;
  style?: StyleProp<ViewStyle>;
}

export const NATIVE_GUIDED_CAMERA = true;

export function useGuidedCameraPermission() {
  const p = useCameraPermission();
  return { granted: p.hasPermission, canAsk: p.canRequestPermission, request: p.requestPermission, status: p.status as string };
}

const AUTO: LockState = { exposure: 'auto', focus: 'auto', whiteBalance: 'auto' };

export const GuidedCamera = forwardRef<GuidedCameraHandle, GuidedCameraProps>(function GuidedCamera(
  { guide, isActive, maxBytes, onReady, onError, style }, ref,
) {
  const target = guide.camera;
  const device = useCameraDevice('back', { physicalDevices: [target.lens === 'ultra-wide' ? 'ultra-wide-angle' : 'wide-angle'] });
  const video = useVideoOutput({
    targetResolution: target.longEdge >= 3840 ? CommonResolutions.UHD_16_9 : CommonResolutions.FHD_16_9,
    targetBitRate: target.bitrateMbps * 1_000_000,
    enableAudio: target.audio,
    fileType: Platform.OS === 'ios' ? 'mov' : 'mp4',
  });
  const cam = useRef<CameraRef>(null);
  const recorder = useRef<Recorder | null>(null);
  const size = useRef({ w: 0, h: 0 });
  const codec = useRef<string>('h264');
  const [session, setSession] = useState<CameraSessionConfig | null>(null);

  const isUltraWide = device?.type === 'ultra-wide-angle';

  const constraints = useMemo<Constraint[]>(() => {
    if (!device) return [];
    const list: Constraint[] = [{ resolutionBias: video }];
    if (device.supportsFPS(target.fps)) list.unshift({ fps: target.fps });
    const stab = target.stabilization === 'standard' && device.supportsVideoStabilizationMode('standard') ? 'standard' : 'off';
    list.push({ videoStabilizationMode: stab });
    if (!target.hdr) list.push({ videoDynamicRange: CommonDynamicRanges.ANY_SDR });
    return list;
  }, [device, video, target.fps, target.stabilization, target.hdr]);

  const lockable = useMemo<CameraInfo['lockable']>(() => {
    const k = (canLock: boolean | undefined, canMeter: boolean | undefined) => (canLock ? 'lock' : canMeter ? 'metering' : 'none');
    return {
      exposure: k(device?.supportsExposureLocking, device?.supportsExposureMetering),
      focus: k(device?.supportsFocusLocking, device?.supportsFocusMetering),
      whiteBalance: k(device?.supportsWhiteBalanceLocking, device?.supportsWhiteBalanceMetering),
    };
  }, [device]);

  const report = useCallback((cfg: CameraSessionConfig | null) => {
    if (!device) return;
    const res = video.currentResolution;
    const long = res ? Math.max(res.width, res.height) : null;
    const short = res ? Math.min(res.width, res.height) : null;
    onReady?.({
      lens: isUltraWide ? 'ultra-wide' : 'wide',
      lensLabel: isUltraWide ? '0.5×' : '1×',
      width: long,
      height: short,
      fps: cfg?.selectedFPS ?? null,
      stabilization: cfg?.selectedVideoStabilizationMode ?? null,
      hdr: cfg?.selectedVideoDynamicRange ? cfg.selectedVideoDynamicRange.bitDepth !== 'sdr-8-bit' : false,
      codec: codec.current,
      bitrate: target.bitrateMbps * 1_000_000,
      lockable,
    });
  }, [device, video, isUltraWide, lockable, onReady, target.bitrateMbps]);

  const onConfigured = useCallback(async () => {
    try {
      const want = target.codec === 'hevc' ? 'h265' : 'h264';
      const codecs = video.getSupportedVideoCodecs();
      const pick = codecs.includes(want) ? want : codecs.includes('h264') ? 'h264' : codecs[0];
      if (pick) { await video.setOutputSettings({ codec: pick }); codec.current = pick; }
    } catch { /* keep the platform default */ }
    report(session);
  }, [video, target.codec, report, session]);

  useImperativeHandle(ref, () => ({
    async lock() {
      const c = cam.current;
      if (!c || !device) return { exposure: 'failed', focus: 'failed', whiteBalance: 'failed' };
      const state: LockState = { ...AUTO };
      const wanted = new Set(guide.locks);
      const modes = ([['AE', 'exposure'], ['AF', 'focus'], ['AWB', 'whiteBalance']] as const)
        .filter(([, k]) => wanted.has(k) && lockable[k] !== 'none').map(([m]) => m);
      let metered = false;
      try {
        if (modes.length) {
          const { w, h } = size.current;
          await c.focusTo({ x: (w || 1) / 2, y: (h || 1) / 2 }, { modes: [...modes], adaptiveness: 'locked', responsiveness: 'steady', autoResetAfter: null });
          metered = true;
        }
      } catch { metered = false; }
      const ctl = c.controller;
      const tryLock = async (k: LockKind, fn: () => Promise<void>, read: () => string) => {
        if (!wanted.has(k)) return;
        if (lockable[k] === 'none') { state[k] = 'unsupported'; return; }
        if (lockable[k] === 'lock' && ctl) {
          try { await fn(); } catch { /* read back below */ }
          state[k] = read() === 'locked' ? 'locked' : metered ? 'metering-locked' : 'failed';
          return;
        }
        state[k] = metered ? 'metering-locked' : 'failed';
      };
      await tryLock('exposure', () => ctl!.lockCurrentExposure(), () => ctl?.exposureMode ?? '');
      await tryLock('focus', () => ctl!.lockCurrentFocus(), () => ctl?.focusMode ?? '');
      await tryLock('whiteBalance', () => ctl!.lockCurrentWhiteBalance(), () => ctl?.whiteBalanceMode ?? '');
      return state;
    },
    async unlock() {
      try { await cam.current?.resetFocus(); } catch { /* already continuous */ }
    },
    async start(onFinished, onErr) {
      const r = await video.createRecorder({ maxDuration: guide.maxSeconds, maxFileSize: maxBytes });
      recorder.current = r;
      await r.startRecording(
        (path, reason) => { recorder.current = null; onFinished(path, reason); },
        (e) => { recorder.current = null; onErr(e); },
      );
    },
    async stop() { await recorder.current?.stopRecording(); },
    async cancel() { try { await recorder.current?.cancelRecording(); } finally { recorder.current = null; } },
  }), [device, guide.locks, guide.maxSeconds, lockable, maxBytes, video]);

  const onLayout = (e: LayoutChangeEvent) => {
    size.current = { w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height };
  };

  if (!device) return <View style={[StyleSheet.absoluteFill, style]} />;
  return (
    <Camera
      ref={cam}
      style={[StyleSheet.absoluteFill, style]}
      device={device}
      outputs={[video]}
      constraints={constraints}
      isActive={isActive}
      resizeMode="cover"
      onLayout={onLayout}
      onSessionConfigSelected={(cfg) => { setSession(cfg); report(cfg); }}
      onConfigured={onConfigured}
      onError={(e) => onError?.(e.message)}
      enableNativeTapToFocusGesture={false}
      enableNativeZoomGesture={false}
    />
  );
});
